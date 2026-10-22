"""Delve lifecycle and compatibility behavior independent of a dlv installation."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import Mock

import pytest

from gdb_mcp import contracts as c
from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.domain.errors import DebuggerDecodeError
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.delve_backend import DelveRpcBackend
from gdb_mcp.session.delve_values import (
    breakpoint_record,
    frame_record,
    integer,
    mapping,
    mappings,
    text,
    variable_record,
    variable_value,
)
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.transport.delve_rpc import DelveRpcError


def backend() -> DelveRpcBackend:
    selected = create_default_session_service(c.DEBUGGER_DELVE).backend
    assert isinstance(selected, DelveRpcBackend)
    return selected


def test_implementation_value_error_reaches_the_dispatch_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_variable", Mock(side_effect=ValueError("implementation bug")))

    with pytest.raises(ValueError, match="implementation bug"):
        selected.evaluate_expression("value")


@pytest.mark.parametrize(
    "failure",
    [DelveRpcError("future debugger failure"), OSError("connection failed")],
)
def test_expected_delve_failures_remain_operation_errors(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_variable", Mock(side_effect=failure))

    result = selected.evaluate_expression("value")

    assert isinstance(result, OperationError)
    assert result.message == str(failure)


def test_malformed_delve_address_is_a_decode_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_variable", Mock(return_value={"value": "not an address"}))

    with pytest.raises(DebuggerDecodeError, match="address"):
        selected._address("pointer")


def test_malformed_delve_memory_encoding_is_a_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_scope", Mock(return_value={}))
    monkeypatch.setattr(selected, "_request", Mock(return_value={"Mem": "%%%"}))

    with pytest.raises(DebuggerDecodeError, match="base64"):
        selected.read_memory("0x0", 1)


def test_unexpected_startup_failure_cleans_up_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_spawn", Mock(side_effect=ValueError("implementation bug")))
    cleanup = Mock()
    monkeypatch.setattr(selected, "_cleanup", cleanup)

    with pytest.raises(ValueError, match="implementation bug"):
        selected.start(SessionConfig(debugger=c.DEBUGGER_DELVE, program="app"))

    cleanup.assert_called_once_with()
    assert selected.runtime.state is c.SessionState.FAILED


def test_empty_session_does_not_resolve_or_spawn_delve(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = Mock(side_effect=AssertionError("must defer spawning"))
    monkeypatch.setattr("gdb_mcp.session.delve_backend.subprocess.Popen", spawn)
    registry = SessionRegistry()
    started = registry.start_session(debugger=c.DEBUGGER_DELVE, debugger_path="/missing/dlv")
    assert isinstance(started, OperationSuccess)
    session = registry.get_session(started.value.session_id)
    assert session is not None
    assert session.state == c.SessionState.READY
    assert session.runtime.execution_state == "not_started"
    assert session.get_status().value.inferior_count == 0
    assert not session.has_controller
    assert not session.is_running
    assert isinstance(registry.close_session(started.value.session_id), OperationSuccess)
    spawn.assert_not_called()


def test_failed_deferred_attach_can_be_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = backend()
    assert isinstance(selected.start(SessionConfig(debugger=c.DEBUGGER_DELVE)), OperationSuccess)
    calls: list[int | None] = []

    def spawn(*, pid: int | None = None, timeout_sec: float = 30) -> None:
        calls.append(pid)
        if len(calls) == 1:
            raise DelveRpcError("attach failed")
        selected.runtime.mark_attached(pid or 0)
        selected.runtime.mark_inferior_paused("attached")

    monkeypatch.setattr(selected, "_spawn", spawn)
    assert isinstance(selected.attach_process(42), OperationError)
    assert selected.runtime.state == c.SessionState.READY
    assert isinstance(selected.attach_process(42), OperationSuccess)
    assert calls == [42, 42]


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("launched", "entry"),
        ("attached", "pause"),
        ("manual", "pause"),
        ("next finished", "step"),
        ("call returned", "step"),
    ],
)
def test_native_stop_reasons_keep_their_canonical_kind(reason: str, kind: str) -> None:
    selected = backend()
    selected._client_state({"State": {"Pid": 42, "stopReason": reason}})
    assert selected.runtime.stop_reason == reason
    assert selected.runtime.stop_kind == kind


def test_old_state_without_reason_uses_pending_operation() -> None:
    selected = backend()
    selected._has_run = True
    selected._command = "step"
    selected._client_state({"State": {"Pid": 42, "currentGoroutine": {"id": 7}}})
    assert selected.runtime.current_thread_id == 7
    assert selected.runtime.stop_kind == "step"
    selected._client_state({"State": {"Pid": 42, "currentGoroutine": {"id": 7}}})
    assert len(selected.runtime.stop_history) == 1


def test_missing_jump_method_is_remembered_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_locations", Mock(return_value=[{"pc": 123}]))
    request = Mock(
        side_effect=DelveRpcError(
            "unknown method: RPCServer.SetExecutionPoint", "unsupported_operation"
        )
    )
    monkeypatch.setattr(selected, "_request", request)
    result = selected.jump("main.main")
    assert isinstance(result, OperationError) and result.code == "unsupported_operation"
    assert not selected.capabilities.supports(c.ACTION_JUMP)
    assert backend().capabilities.supports(c.ACTION_JUMP)


def test_go_values_keep_aggregate_children_and_unreadable_details() -> None:
    payload: dict[str, object] = {
        "name": "items",
        "kind": 23,
        "type": "[]int",
        "len": 3,
        "children": [{"value": "1"}, {"value": "2"}],
    }
    record = variable_record(payload)
    assert record.value == "[]int{1, 2, ...}"
    assert record.to_payload()["children"] == payload["children"]
    assert variable_value({"kind": 24, "value": "a\nb"}) == '"a\nb"'.replace("\n", "\\n")
    assert (
        variable_value({"unreadable": "memory unavailable"}) == "<unreadable: memory unavailable>"
    )
    breakpoint = breakpoint_record({"id": 1, "addr": 123})
    assert breakpoint.number == "1" and breakpoint.locations[0].addr == "0x7b"


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        (mapping, "not a mapping"),
        (mappings, "not a list"),
        (mappings, [{"ok": True}, "not a mapping"]),
        (integer, "not an integer"),
        (integer, None),
        (integer, True),
        (text, 123),
    ],
)
def test_malformed_delve_values_raise_decode_errors(
    decoder: Callable[[object], object], payload: object
) -> None:
    with pytest.raises(DebuggerDecodeError):
        decoder(payload)


def test_frame_record_keeps_unknown_fields_and_omits_absent_optional_values() -> None:
    record = frame_record({"pc": 16, "new-field": {"nested-key": "value"}})

    assert record.to_payload() == {
        "level": 0,
        "addr": "0x10",
        "pc": 16,
        "new-field": {"nested-key": "value"},
    }


def test_frame_record_rejects_malformed_consumed_fields() -> None:
    with pytest.raises(DebuggerDecodeError, match="integer"):
        frame_record({"pc": "sixteen"})


def test_breakpoint_record_does_not_invent_missing_location_values() -> None:
    record = breakpoint_record({"id": 3, "new-field": "retained"})

    assert record.to_payload() == {
        "id": 3,
        "number": "3",
        "type": "breakpoint",
        "temporary": False,
        "locations": [],
        "new-field": "retained",
    }


def test_breakpoint_record_rejects_malformed_consumed_fields() -> None:
    with pytest.raises(DebuggerDecodeError, match="integer"):
        breakpoint_record({})
    with pytest.raises(DebuggerDecodeError, match="integer"):
        breakpoint_record({"id": "three"})
    with pytest.raises(DebuggerDecodeError, match="addresses"):
        breakpoint_record({"id": 3, "addrs": "0x10"})


def test_variable_record_rejects_malformed_children() -> None:
    with pytest.raises(DebuggerDecodeError, match="list of mappings"):
        variable_record({"name": "value", "children": "not a list"})


def test_delve_records_keep_zero_values_and_distinct_dashed_keys() -> None:
    fields: dict[str, object] = {
        "new-field": {"nested-key": [None, 0, False]},
        "original-location": "native",
        "original_location": "extension",
    }
    payload = {**fields, "id": 1, "addrs": [], "addr": 0, "totalHitCount": 0, "disabled": False}
    record = breakpoint_record(payload)

    assert record.number == "1"
    assert record.enabled is True
    assert record.times == 0
    assert record.locations[0].addr == "0x0"
    assert record.verified is False
    assert record.to_payload() == {
        **payload,
        "number": "1",
        "type": "breakpoint",
        "enabled": True,
        "times": 0,
        "temporary": False,
        "verified": False,
        "locations": [{"addr": "0x0"}],
    }
    assert frame_record({"pc": 0, "line": 0, **fields}).addr == "0x0"
    assert variable_record({"addr": 0, "value": "0", **fields}).address == "0x0"


@pytest.mark.parametrize("location", [{}, {"pc": "invalid"}, {"pcs": "invalid"}])
def test_invalid_breakpoint_locations_do_not_send_fabricated_addresses(
    monkeypatch: pytest.MonkeyPatch, location: dict[str, object]
) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_locations", Mock(return_value=[location]))
    request = Mock()
    monkeypatch.setattr(selected, "_request", request)

    with pytest.raises(DebuggerDecodeError):
        selected.set_breakpoint("main.main")

    request.assert_not_called()


def test_delve_register_records_keep_debugger_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = backend()
    native = {"DwarfNumber": 0, "Name": "rax", "Value": "0x0", "new-field": {"dash-key": True}}
    monkeypatch.setattr(selected, "_scope", Mock(return_value={}))
    monkeypatch.setattr(selected, "_request", Mock(return_value={"Regs": [native]}))

    result = selected.get_registers()

    assert isinstance(result, OperationSuccess)
    assert result.value.registers[0].to_payload() == {
        **native,
        "number": 0,
        "name": "rax",
        "value": "0x0",
    }
