"""Unit coverage for the LLDB bridge backend's contract mapping.

These tests replace the native bridge with a scripted JSON responder; the real
SB API path is exercised by the integration suite when an LLDB installation is
available.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TypeVar, cast
from unittest.mock import ANY, Mock, patch

import pytest

from gdb_mcp.contracts import ACTION_JUMP, ACTION_UNTIL
from gdb_mcp.domain import (
    InspectionLocation,
    OperationError,
    OperationResult,
    OperationSuccess,
    StopEvent,
)
from gdb_mcp.domain.errors import DebuggerDecodeError
from gdb_mcp.session import lldb_values
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.lldb_backend import LldbSbBackend
from gdb_mcp.session.lldb_bridge import BridgeClient
from gdb_mcp.session.lldb_bridge_types import BridgeError
from gdb_mcp.session.protocols import OsModuleProtocol, TimeModuleProtocol
from gdb_mcp.session.requests import (
    AddressLocation,
    AddressRangeLocation,
    CurrentLocation,
    DisassemblyLocation,
    DisassemblyRequest,
    ExecutionOptions,
    FileLineLocation,
    FileRangeLocation,
    FunctionLocation,
    SourceContextRequest,
)


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.breakpoints: list[dict[str, object]] = []
        self.watchpoints: list[dict[str, object]] = []
        self.next_breakpoint = 1
        self.next_watchpoint = 1

    def _breakpoint_payload(self, number: int, *, catchpoint: bool = False) -> dict[str, object]:
        return {
            "number": str(number),
            "type": "catchpoint" if catchpoint else "breakpoint",
            "enabled": True,
            "times": 0,
            "verified": not catchpoint,
            "condition": "",
            "locations": (
                []
                if catchpoint
                else [
                    {
                        "addr": "0x401000",
                        "func": "main",
                        "file": "main.c",
                        "line": 2,
                        "column": 3,
                        "resolved": True,
                    }
                ]
            ),
        }

    def _watchpoint_payload(self, number: int) -> dict[str, object]:
        return {
            "number": str(number),
            "type": "watchpoint",
            "enabled": True,
            "times": 0,
            "verified": True,
            "condition": "",
            "locations": [],
            "watch": {"access": "write", "address": "0x7ffffff0", "size": 4, "spec": "sum"},
        }

    def request(
        self, operation: str, *, timeout_sec: int = 30, **payload: object
    ) -> dict[str, object]:
        self.calls.append((operation, payload))
        if operation in {"status", "wait"}:
            return {
                "target_loaded": True,
                "execution_state": "paused",
                "state_detail": "crashed",
                "stop_reason": "signal",
                "stop_kind": "signal",
                "inferior_count": 1,
                "current_inferior_id": 1,
                "current_thread_id": 1,
                "current_frame": 0,
                "signal": 11,
                "signal_name": "SIGSEGV",
                "signal_code": 1,
                "fault_address": "0x0",
                "crash": {
                    "exception": "EXC_BAD_ACCESS",
                    "code": 1,
                    "fault_address": "0x0",
                    "signal_name": "SIGSEGV",
                },
                "frame": {
                    "level": 0,
                    "addr": "0x401000",
                    "func": "main",
                    "file": "main.c",
                    "line": 2,
                },
                "stop_data": [11],
                "pid": 123,
                "return_value": "5",
            }
        if operation == "breakpoint_create":
            number = self.next_breakpoint
            self.next_breakpoint += 1
            record = self._breakpoint_payload(number, catchpoint=payload.get("mode") == "exception")
            self.breakpoints.append(record)
            return record
        if operation == "watchpoint_create":
            number = self.next_watchpoint
            self.next_watchpoint += 1
            record = self._watchpoint_payload(number)
            self.watchpoints.append(record)
            return record
        if operation == "breakpoint_list":
            return {"breakpoints": list(self.breakpoints)}
        if operation == "watchpoint_list":
            return {"watchpoints": list(self.watchpoints)}
        if operation == "breakpoint_get":
            return next(item for item in self.breakpoints if item["number"] == str(payload["id"]))
        if operation == "watchpoint_get":
            return next(item for item in self.watchpoints if item["number"] == str(payload["id"]))
        if operation == "breakpoint_delete":
            self.breakpoints = [
                item for item in self.breakpoints if item["number"] != str(payload["id"])
            ]
            return {}
        if operation == "watchpoint_delete":
            self.watchpoints = [
                item for item in self.watchpoints if item["number"] != str(payload["id"])
            ]
            return {}
        if operation == "source":
            return {
                "file": "main.c",
                "fullname": "/tmp/main.c",
                "function": "main",
                "line": 2,
                "start_line": 2,
                "end_line": 2,
                "lines": [
                    {
                        "line_number": 2,
                        "text": "int x = 1;",
                        "column": 7,
                        "is_current": True,
                    }
                ],
            }
        if operation == "variables":
            return {
                "thread_id": 1,
                "frame": 0,
                "variables": [
                    {
                        "name": "x",
                        "type": "int",
                        "value": "1",
                        "summary": "",
                        "address": "0x7ffffff0",
                        "children_count": 0,
                    }
                ],
            }
        if operation == "evaluate":
            return {
                "expression": "x",
                "value": "1",
                "type": "int",
                "summary": "",
            }
        if operation in {
            "setting_set",
            "breakpoint_enable",
            "watchpoint_enable",
            "breakpoint_condition",
            "watchpoint_condition",
        }:
            return {}
        return {}


_PayloadT = TypeVar("_PayloadT")


def _value(result: OperationResult[_PayloadT]) -> _PayloadT:
    assert isinstance(result, OperationSuccess)
    return result.value


def _backend() -> tuple[LldbSbBackend, FakeBridge]:
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    bridge = FakeBridge()
    backend._client = cast(BridgeClient, bridge)
    return backend, bridge


@pytest.mark.parametrize(
    "failure", [RuntimeError("close bug"), DebuggerDecodeError("bad consumed field")]
)
def test_lldb_stop_propagates_unexpected_failure(failure: Exception) -> None:
    backend, _ = _backend()
    backend._client = Mock(spec=BridgeClient, close=Mock(side_effect=failure))
    backend.runtime.mark_ready()

    with pytest.raises(type(failure), match=str(failure)):
        backend.stop()

    assert not backend.has_controller
    assert not backend.runtime.is_running


def test_lldb_stop_preserves_expected_failure_code() -> None:
    backend, _ = _backend()
    backend._client = Mock(
        spec=BridgeClient, close=Mock(side_effect=BridgeError("bad shutdown", "protocol_error"))
    )

    result = backend.stop()

    assert isinstance(result, OperationError)
    assert result.code == "protocol_error"


def test_lldb_unexpected_startup_exception_is_not_reclassified() -> None:
    """Unexpected bridge implementation failures should propagate to dispatch."""

    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    with (
        patch(
            "gdb_mcp.session.lldb_backend.prepare_bridge",
            side_effect=RuntimeError("bridge implementation bug"),
        ),
        pytest.raises(RuntimeError, match="bridge implementation bug"),
    ):
        backend.start(SessionConfig.from_inputs(debugger="lldb", program="/bin/true"))

    assert backend.runtime.state.value == "failed"


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        (lldb_values.mapping, "not a mapping"),
        (lldb_values.mappings, "not a list"),
        (lldb_values.mappings, [{"ok": True}, "not a mapping"]),
        (lambda value: lldb_values.text({"field": value}, "field"), 1),
        (lambda value: lldb_values.integer({"field": value}, "field"), "1"),
    ],
)
def test_lldb_decoders_reject_malformed_values(
    decoder: Callable[[object], object], payload: object
) -> None:
    with pytest.raises(DebuggerDecodeError):
        decoder(payload)


def test_lldb_add_inferior_rejects_missing_id() -> None:
    backend, _ = _backend()
    with (
        patch.object(
            backend,
            "_request",
            side_effect=[{}, {"targets": [], "current_inferior_id": None}],
        ),
        pytest.raises(DebuggerDecodeError, match="inferior_id"),
    ):
        backend.add_inferior()


def test_lldb_memory_rejects_missing_read_count() -> None:
    backend, _ = _backend()
    with (
        patch.object(backend, "_request", return_value={"address": "0x10", "data": "00"}),
        pytest.raises(DebuggerDecodeError, match="read count"),
    ):
        backend.read_memory("0x10", 1)


@pytest.mark.parametrize("number", [None, "not-a-number", True])
def test_lldb_breakpoint_creation_rejects_invalid_numbers(number: object) -> None:
    backend, _ = _backend()
    with (
        patch.object(backend, "_request", return_value={"number": number}),
        pytest.raises(DebuggerDecodeError),
    ):
        backend.set_breakpoint("main")


@pytest.mark.parametrize("thread_id", [[], {}, True])
def test_lldb_thread_ids_do_not_stringify_malformed_values(thread_id: object) -> None:
    with pytest.raises(DebuggerDecodeError):
        lldb_values.thread({"id": thread_id})


def test_lldb_memory_keeps_zero_address_and_count() -> None:
    backend, _ = _backend()
    with patch.object(backend, "_request", return_value={"address": "0x0", "data": "", "read": 0}):
        memory = _value(backend.read_memory("0x0", 0))

    assert memory.captured_bytes == 0
    assert memory.blocks[0].begin == "0x0"
    assert memory.blocks[0].end == "0x0"
    assert memory.blocks[0].contents == ""


def test_lldb_memory_rejects_malformed_address() -> None:
    backend, _ = _backend()
    with (
        patch.object(
            backend, "_request", return_value={"address": "invalid", "data": "00", "read": 1}
        ),
        pytest.raises(DebuggerDecodeError, match="hexadecimal"),
    ):
        backend.read_memory("0x10", 1)


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
def test_lldb_location_request_is_sent_without_preflight_checks(action: str) -> None:
    backend, _ = _backend()
    native_message = "error: Until target outside of the current function.\n"
    response = {"succeeded": False, "error": native_message, "execution_state": "paused"}
    with patch.object(backend, "_request", return_value=response) as request:
        operation = backend.jump if action == ACTION_JUMP else backend.until
        result = operation("missing.c:999999")

    request.assert_called_once_with(
        "execute_location",
        relocate=action == ACTION_JUMP,
        kind="file_line",
        file="missing.c",
        line=999999,
    )
    assert isinstance(result, OperationError)
    assert result.message == native_message


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
def test_lldb_location_acknowledgement_does_not_reuse_a_stop(action: str) -> None:
    backend, _ = _backend()
    previous = StopEvent(execution_state="paused", reason="breakpoint", stop_kind="breakpoint")
    backend.runtime.record_stop_event(previous)
    with patch.object(
        backend,
        "_request",
        return_value={
            "succeeded": True,
            "execution_state": "running",
            "relocated": action == ACTION_JUMP,
        },
    ) as request:
        operation = backend.jump if action == ACTION_JUMP else backend.until
        result = _value(operation("*$pc + 4", options=ExecutionOptions(wait_for_stop=False)))

    assert request.call_count == 1
    assert result.execution_state == "running"
    assert result.last_stop_event is None
    assert result.stop_reason is None


def test_lldb_jump_reports_relocation_when_resume_fails() -> None:
    backend, _ = _backend()
    with patch.object(
        backend,
        "_request",
        return_value={
            "succeeded": False,
            "relocated": True,
            "execution_state": "paused",
            "error": "native resume failed",
        },
    ):
        result = backend.jump("42")

    assert isinstance(result, OperationError)
    assert result.message == "native resume failed"
    assert result.details["relocated"] is True


def test_factory_selects_the_lldb_backend() -> None:
    service = create_default_session_service("lldb")

    assert isinstance(service.backend, LldbSbBackend)


def test_lldb_rr_replay_points_to_connect_mode_and_reverse_caveat() -> None:
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )

    result = backend.start(SessionConfig.from_inputs(debugger="lldb", rr_replay="/tmp/rr-trace"))

    assert isinstance(result, OperationError)
    assert result.code == "unsupported_combination"
    assert "reverse" in result.message
    assert "gdb_session_connect" in result.message
    assert "connect://" in result.message
    assert "debugger='gdb'" in result.message


def test_finish_publishes_the_return_value() -> None:
    backend, _ = _backend()

    finished = _value(backend.finish(options=ExecutionOptions(timeout_sec=1)))

    assert finished.return_value == "5"
    assert finished.stop_reason == "signal"


def test_startup_resets_stop_event_deduplication() -> None:
    backend, bridge = _backend()
    backend.runtime.mark_ready()
    backend.get_status()
    assert len(backend.runtime.stop_history) == 1

    backend._client = None
    with (
        patch("gdb_mcp.session.lldb_backend.prepare_bridge"),
        patch("gdb_mcp.session.lldb_backend.BridgeClient", return_value=bridge),
    ):
        started = backend.start(SessionConfig(debugger="lldb"))

    assert isinstance(started, OperationSuccess)
    assert len(backend.runtime.stop_history) == 1
    assert backend.runtime.last_stop_event is not None


@pytest.mark.parametrize("identity_field", ["pid", "current_inferior_id"])
def test_stop_identity_includes_process_and_inferior(identity_field: str) -> None:
    backend, bridge = _backend()
    payload = bridge.request("status")
    backend._events.apply(payload)
    backend._events.apply(payload)
    assert len(backend.runtime.stop_history) == 1

    backend._events.apply({**payload, identity_field: 2})

    assert len(backend.runtime.stop_history) == 2


@pytest.mark.parametrize(
    ("details", "reason", "kind"),
    [
        ({}, "breakpoint", "watchpoint"),
        ({"stop_reason": "step", "stop_kind": "step"}, "step", "step"),
        ({"stop_reason": None, "stop_kind": None}, None, "unknown"),
    ],
)
def test_inventory_only_replaces_stop_details_it_reports(
    details: dict[str, object], reason: str | None, kind: str
) -> None:
    backend, bridge = _backend()
    backend.runtime.mark_inferior_paused("breakpoint", stop_kind="watchpoint")
    with patch.object(
        bridge,
        "request",
        return_value={
            "current_inferior_id": 1,
            "targets": [{"inferior_id": 1, "execution_state": "paused", **details}],
        },
    ):
        assert isinstance(backend.list_inferiors(), OperationSuccess)

    assert backend.runtime.stop_reason == reason
    assert backend.runtime.stop_kind == kind


@pytest.mark.parametrize("state", ["running", "not_started", "unknown"])
def test_inventory_state_transition_clears_previous_stop(state: str) -> None:
    backend, bridge = _backend()
    backend.runtime.mark_inferior_paused("breakpoint", stop_kind="watchpoint")
    with patch.object(
        bridge,
        "request",
        return_value={
            "current_inferior_id": 1,
            "targets": [{"inferior_id": 1, "execution_state": state}],
        },
    ):
        assert isinstance(backend.list_inferiors(), OperationSuccess)

    assert backend.runtime.execution_state == state
    assert backend.runtime.stop_reason is None
    assert backend.runtime.stop_kind == "unknown"


def test_unknown_execution_state_replaces_previous_runtime_state() -> None:
    backend, _ = _backend()
    backend.runtime.mark_inferior_running()

    status = backend._events.apply(
        {
            "execution_state": "future-state",
            "target_loaded": True,
            "current_inferior_id": 1,
            "inferior_count": 1,
        }
    )

    assert status.execution_state == "unknown"
    assert backend.runtime.execution_state == "unknown"
    assert backend.runtime.stop_history == []


def test_malformed_stop_does_not_partially_update_runtime() -> None:
    backend, bridge = _backend()
    backend.runtime.mark_inferior_running()
    backend.runtime.mark_thread_selected(99)
    payload = bridge.request("status")
    with pytest.raises(DebuggerDecodeError):
        backend._events.apply({**payload, "signal_code": "bad integer"})

    assert backend.runtime.execution_state == "running"
    assert backend.runtime.current_thread_id == 99
    assert backend.runtime.stop_history == []
    backend._events.apply(payload)
    assert len(backend.runtime.stop_history) == 1


def test_crash_detail_follows_selected_inferior() -> None:
    backend, bridge = _backend()
    backend._events.apply(bridge.request("status"))
    assert backend.runtime.status_snapshot(debugger_alive=True).state_detail == "crashed"

    backend.runtime.mark_inferior_paused("breakpoint", inferior_id=2)
    backend.runtime.mark_inferior_selected(2)
    assert backend.runtime.status_snapshot(debugger_alive=True).state_detail is None

    backend.runtime.mark_inferior_selected(1)
    assert backend.runtime.status_snapshot(debugger_alive=True).state_detail == "crashed"


def test_status_enriches_crash_and_stop_event() -> None:
    backend, _ = _backend()

    status = _value(backend.get_status())

    assert status.execution_state == "paused"
    assert status.state_detail == "crashed"
    assert status.stop_kind == "signal"
    event = backend.runtime.last_stop_event
    assert event is not None
    assert event.state_detail == "crashed"
    assert event.signal_code == 1
    assert event.fault_address == "0x0"
    assert event.signal_meaning == "Segmentation fault"
    assert event.backend_data is not None
    assert event.backend_data.debugger == "lldb"
    assert event.backend_data.data["exception"] == {"exception": "EXC_BAD_ACCESS"}
    assert "signal_code" not in event.backend_data.data
    assert "fault_address" not in event.backend_data.data
    assert event.frame is not None and event.frame.func == "main"


def test_breakpoints_and_watchpoints_share_one_public_number_space() -> None:
    backend, _ = _backend()

    first = _value(backend.set_breakpoint("main")).breakpoint
    second = _value(backend.set_watchpoint("sum")).breakpoint

    assert first.number == "1"
    assert second.number == "2"
    assert second.type == "watchpoint"
    assert second.watch is not None
    assert second.watch.spec == "sum"

    listed = _value(backend.list_breakpoints())
    assert [record.number for record in listed.breakpoints] == ["1", "2"]
    assert listed.breakpoints[1].watch is not None
    assert listed.breakpoints[1].watch.address == "0x7ffffff0"

    looked_up = _value(backend.get_breakpoint(2)).breakpoint
    assert looked_up.type == "watchpoint"

    assert _value(backend.delete_watchpoint(2)).message == "Watchpoint 2 deleted"
    listed = _value(backend.list_breakpoints())
    assert [record.number for record in listed.breakpoints] == ["1"]


def test_catchpoint_uses_breakpoint_native_namespace_without_duplicates() -> None:
    backend, _ = _backend()
    backend.set_breakpoint("main")
    catchpoint = _value(backend.set_catchpoint("throw")).breakpoint

    assert catchpoint.type == "catchpoint"
    assert catchpoint.number == "2"

    listed = _value(backend.list_breakpoints())
    assert [record.number for record in listed.breakpoints] == ["1", "2"]
    assert [record.type for record in listed.breakpoints] == ["breakpoint", "catchpoint"]


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (CurrentLocation(), InspectionLocation(kind="current")),
        (FunctionLocation("main"), InspectionLocation(kind="function", function="main")),
        (AddressLocation("0x123"), InspectionLocation(kind="address", address="0x123")),
        (AddressRangeLocation("0x123", "0x456"), InspectionLocation(kind="address_range")),
        (
            FileLineLocation("main.c", 7),
            InspectionLocation(kind="file_line", file="main.c", fullname="main.c", line=7),
        ),
        (
            FileRangeLocation("main.c", 7, 10),
            InspectionLocation(kind="file_line", file="main.c", fullname="main.c"),
        ),
    ],
)
def test_empty_disassembly_keeps_typed_request_metadata(
    selector: DisassemblyLocation, expected: InspectionLocation
) -> None:
    backend, _ = _backend()

    result = _value(backend.disassemble(DisassemblyRequest(location=selector)))

    assert result.location == expected


def test_source_range_falls_back_to_typed_request_metadata() -> None:
    backend, bridge = _backend()
    with patch.object(bridge, "request", return_value={"lines": []}) as source_request:
        source = _value(
            backend.get_source_context(
                SourceContextRequest(location=FileRangeLocation("main.c", 7, 10))
            )
        )

    assert source.location == InspectionLocation(kind="file_range", file="main.c")
    assert source.range.start_line == 7
    assert source.range.end_line == 10
    source_request.assert_called_once_with(
        "source",
        timeout_sec=ANY,
        file="main.c",
        start_line=7,
        end_line=10,
        thread_id=None,
        frame=None,
        context_before=5,
        context_after=5,
    )


def test_source_and_value_enrichment_are_published() -> None:
    backend, _ = _backend()

    source = _value(
        backend.get_source_context(
            SourceContextRequest(location=FileLineLocation(file="main.c", line=2))
        )
    )
    assert source.lines[0].column == 7

    variables = _value(backend.get_variables())
    assert variables.variables[0].summary == ""
    assert variables.variables[0].address == "0x7ffffff0"
    assert variables.variables[0].children_count == 0

    evaluated = _value(backend.evaluate_expression("x"))
    assert evaluated.type == "int"
    assert evaluated.summary == ""
