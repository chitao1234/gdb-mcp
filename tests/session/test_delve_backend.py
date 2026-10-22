"""Delve lifecycle and compatibility behavior independent of a dlv installation."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from gdb_mcp import contracts as c
from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.session.delve_backend import DelveRpcBackend
from gdb_mcp.session.delve_values import breakpoint_record, variable_record, variable_value
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.transport.delve_rpc import DelveRpcError


def backend() -> DelveRpcBackend:
    selected = create_default_session_service(c.DEBUGGER_DELVE).backend
    assert isinstance(selected, DelveRpcBackend)
    return selected


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
    assert isinstance(selected.start(), OperationSuccess)
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
