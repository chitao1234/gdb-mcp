"""Unit tests for MCP handler dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock, Mock

from gdb_mcp.domain import (
    BreakpointInfo,
    CommandExecutionInfo,
    MemoryCaptureRange,
    OperationError,
    OperationSuccess,
    StopEvent,
    SessionListInfo,
    SessionMessage,
    SessionStatusSnapshot,
    SessionSummary,
)
from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS, dispatch_tool_call
from gdb_mcp.mcp.schemas import build_tool_definitions
from gdb_mcp.session.factory import create_default_session_service


def dispatch(name: str, arguments, session_manager) -> dict[str, object]:
    """Call the structured MCP dispatcher and parse its JSON payload."""

    result = asyncio.run(
        dispatch_tool_call(
            name,
            arguments,
            session_manager,
            logger=logging.getLogger("test-mcp-handlers"),
        )
    )
    return json.loads(result[0].text)


class TestHandlerDispatch:
    """Test direct MCP handler dispatch against the handler boundary."""

    def test_start_session_returns_session_id(self):
        """Successful startup should include the published session ID."""

        manager = Mock()
        manager.start_session.return_value = (
            42,
            OperationSuccess(SessionMessage(message="Session started")),
        )

        result_data = dispatch("gdb_start_session", {}, manager)

        manager.start_session.assert_called_once()
        assert result_data["status"] == "success"
        assert result_data["session_id"] == 42

    def test_start_session_normalizes_shell_style_string_args(self):
        """Startup args in shell-string form should be normalized before session creation."""

        manager = Mock()
        manager.start_session.return_value = (
            7,
            OperationSuccess(SessionMessage(message="Session started")),
        )

        dispatch(
            "gdb_start_session",
            {"program": "/bin/echo", "args": '--flag "hello world"'},
            manager,
        )

        manager.start_session.assert_called_once_with(
            program="/bin/echo",
            args=["--flag", "hello world"],
            init_commands=None,
            env=None,
            gdb_path=None,
            working_dir=None,
            core=None,
        )

    def test_start_session_failure_does_not_expose_session_id(self):
        """Failed startup should not expose a session ID."""

        manager = Mock()
        manager.start_session.return_value = (
            None,
            OperationError(message="Startup failed"),
        )

        result_data = dispatch("gdb_start_session", {}, manager)

        assert result_data["status"] == "error"
        assert "session_id" not in result_data

    def test_list_sessions_routes_to_registry(self):
        """Global session inventory requests should not require a session_id."""

        manager = Mock()
        manager.list_sessions.return_value = OperationSuccess(
            SessionListInfo(
                sessions=[
                    SessionSummary(
                        session_id=1,
                        lifecycle_state="ready",
                        execution_state="paused",
                        target_loaded=True,
                        has_controller=True,
                        program="/tmp/a.out",
                    )
                ],
                count=1,
            )
        )

        result_data = dispatch("gdb_list_sessions", {}, manager)

        manager.list_sessions.assert_called_once()
        manager.resolve_session.assert_not_called()
        assert result_data["status"] == "success"
        assert result_data["count"] == 1
        assert result_data["sessions"][0]["session_id"] == 1

    def test_tool_with_valid_session_id_works(self):
        """Session-scoped tools should route to the retrieved session."""

        manager = Mock()
        session = Mock()
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        manager.resolve_session.return_value = session

        result_data = dispatch("gdb_get_status", {"session_id": 1}, manager)

        manager.resolve_session.assert_called_once_with(1)
        session.get_status.assert_called_once()
        assert result_data == {
            "status": "success",
            "is_running": False,
            "target_loaded": False,
            "has_controller": True,
            "execution_state": "unknown",
            "stop_reason": None,
            "exit_code": None,
            "current_inferior_id": None,
            "inferior_count": None,
            "follow_fork_mode": None,
            "detach_on_fork": None,
        }

    def test_tool_with_invalid_session_id_returns_error(self):
        """Invalid session IDs should fail before any tool execution."""

        manager = Mock()
        manager.resolve_session.return_value = OperationError(
            message="Invalid session_id: 999. Use gdb_start_session to create a new session."
        )

        result_data = dispatch("gdb_get_status", {"session_id": 999}, manager)

        assert result_data["status"] == "error"
        assert "Invalid session_id: 999" in result_data["message"]
        assert "gdb_start_session" in result_data["message"]

    def test_tool_with_closing_session_returns_closing_error(self):
        """Commands against closing sessions should get a precise lifecycle error."""

        manager = Mock()
        manager.resolve_session.return_value = OperationError(message="Session 2 is closing")

        result_data = dispatch("gdb_get_status", {"session_id": 2}, manager)

        assert result_data["status"] == "error"
        assert "closing" in result_data["message"]

    def test_tool_with_missing_session_id_returns_validation_error(self):
        """Known tools should validate arguments before session lookup."""

        manager = Mock()

        result_data = dispatch("gdb_get_status", {}, manager)

        assert result_data["status"] == "error"
        assert "session_id" in result_data["message"]
        manager.resolve_session.assert_not_called()

    def test_tool_with_non_object_arguments_returns_validation_error(self):
        """Non-dict arguments should fail as request validation errors."""

        manager = Mock()

        result_data = dispatch("gdb_get_status", ["not", "an", "object"], manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Tool arguments must be a JSON object"
        assert result_data["tool"] == "gdb_get_status"
        manager.resolve_session.assert_not_called()

    def test_tool_with_unknown_argument_returns_validation_error(self):
        """Unexpected tool arguments should be rejected at the MCP boundary."""

        manager = Mock()

        result_data = dispatch("gdb_get_status", {"session_id": 1, "unexpected": True}, manager)

        assert result_data["status"] == "error"
        assert "unexpected" in result_data["message"]
        manager.resolve_session.assert_not_called()

    def test_stop_session_uses_registry_close(self):
        """Successful stop should go through the registry lifecycle API."""

        manager = Mock()
        manager.close_session.return_value = OperationSuccess(
            SessionMessage(message="Session stopped")
        )

        result_data = dispatch("gdb_stop_session", {"session_id": 1}, manager)

        manager.close_session.assert_called_once_with(1)
        manager.resolve_session.assert_not_called()
        assert result_data["status"] == "success"

    def test_unknown_tool_returns_unknown_tool_error(self):
        """Unknown tools should fail before any session lookup happens."""

        manager = Mock()

        result_data = dispatch("gdb_typo_tool", {"session_id": 1}, manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Unknown tool: gdb_typo_tool"
        manager.resolve_session.assert_not_called()

    def test_execute_command_routes_to_correct_session(self):
        """Execute-command requests should forward the command payload."""

        manager = Mock()
        session = Mock()
        session.execute_command.return_value = OperationSuccess(
            CommandExecutionInfo(command="info threads", output="Thread info")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_execute_command",
            {"session_id": 5, "command": "info threads", "timeout_sec": 12},
            manager,
        )

        manager.resolve_session.assert_called_once_with(5)
        session.execute_command.assert_called_once_with(command="info threads", timeout_sec=12)

    def test_run_routes_to_correct_session(self):
        """Structured run requests should forward argv and timeout."""

        manager = Mock()
        session = Mock()
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_run",
            {"session_id": 5, "args": ["--flag", "value"], "timeout_sec": 7},
            manager,
        )

        manager.resolve_session.assert_called_once_with(5)
        session.run.assert_called_once_with(args=["--flag", "value"], timeout_sec=7)

    def test_run_accepts_shell_style_string_args(self):
        """String-form run args should be shell-split before forwarding."""

        manager = Mock()
        session = Mock()
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_run",
            {"session_id": 5, "args": '--flag "hello world"', "timeout_sec": 7},
            manager,
        )

        session.run.assert_called_once_with(args=["--flag", "hello world"], timeout_sec=7)

    def test_attach_process_routes_to_correct_session(self):
        """Attach requests should forward pid and timeout."""

        manager = Mock()
        session = Mock()
        session.attach_process.return_value = OperationSuccess(
            CommandExecutionInfo(command="attach 42")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_attach_process",
            {"session_id": 3, "pid": 42, "timeout_sec": 9},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.attach_process.assert_called_once_with(pid=42, timeout_sec=9)

    def test_set_watchpoint_routes_to_correct_session(self):
        """Watchpoint requests should forward expression and access mode."""

        manager = Mock()
        session = Mock()
        session.set_watchpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "2", "type": "hw watchpoint"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_set_watchpoint",
            {"session_id": 3, "expression": "value", "access": "access"},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.set_watchpoint.assert_called_once_with(expression="value", access="access")

    def test_delete_watchpoint_routes_to_correct_session(self):
        """Watchpoint deletion should forward the shared breakpoint number."""

        manager = Mock()
        session = Mock()
        session.delete_watchpoint.return_value = OperationSuccess(
            SessionMessage(message="Watchpoint 2 deleted")
        )
        manager.resolve_session.return_value = session

        dispatch("gdb_delete_watchpoint", {"session_id": 3, "number": 2}, manager)

        manager.resolve_session.assert_called_once_with(3)
        session.delete_watchpoint.assert_called_once_with(number=2)

    def test_set_catchpoint_routes_to_correct_session(self):
        """Catchpoint requests should forward kind, argument, and temporary flag."""

        manager = Mock()
        session = Mock()
        session.set_catchpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "3", "type": "catchpoint"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_set_catchpoint",
            {
                "session_id": 3,
                "kind": "syscall",
                "argument": "open",
                "temporary": True,
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.set_catchpoint.assert_called_once_with(
            "syscall",
            argument="open",
            temporary=True,
        )

    def test_wait_for_stop_routes_to_correct_session(self):
        """Wait requests should forward timeout and optional reason filters."""

        manager = Mock()
        session = Mock()
        session.wait_for_stop.return_value = OperationSuccess({"matched": True})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_wait_for_stop",
            {"session_id": 3, "timeout_sec": 5, "stop_reasons": ["fork"]},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.wait_for_stop.assert_called_once_with(
            timeout_sec=5,
            stop_reasons=("fork",),
        )

    def test_read_memory_routes_to_correct_session(self):
        """Memory read requests should forward address, count, and offset."""

        manager = Mock()
        session = Mock()
        session.read_memory.return_value = OperationSuccess({"captured_bytes": 4})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_read_memory",
            {"session_id": 3, "address": "&value", "count": 4, "offset": 1},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.read_memory.assert_called_once_with(address="&value", count=4, offset=1)

    def test_list_inferiors_routes_to_correct_session(self):
        """Inferior inventory requests should route to the resolved session."""

        manager = Mock()
        session = Mock()
        session.list_inferiors.return_value = OperationSuccess({"count": 1, "inferiors": []})
        manager.resolve_session.return_value = session

        dispatch("gdb_list_inferiors", {"session_id": 4}, manager)

        manager.resolve_session.assert_called_once_with(4)
        session.list_inferiors.assert_called_once_with()

    def test_select_inferior_routes_to_correct_session(self):
        """Inferior selection should forward the requested inferior ID."""

        manager = Mock()
        session = Mock()
        session.select_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager.resolve_session.return_value = session

        dispatch("gdb_select_inferior", {"session_id": 4, "inferior_id": 2}, manager)

        manager.resolve_session.assert_called_once_with(4)
        session.select_inferior.assert_called_once_with(inferior_id=2)

    def test_set_follow_fork_mode_routes_to_correct_session(self):
        """Follow-fork-mode requests should forward the selected mode."""

        manager = Mock()
        session = Mock()
        session.set_follow_fork_mode.return_value = OperationSuccess({"mode": "child"})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_set_follow_fork_mode",
            {"session_id": 4, "mode": "child"},
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.set_follow_fork_mode.assert_called_once_with(mode="child")

    def test_set_detach_on_fork_routes_to_correct_session(self):
        """Detach-on-fork requests should forward the selected boolean value."""

        manager = Mock()
        session = Mock()
        session.set_detach_on_fork.return_value = OperationSuccess({"enabled": False})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_set_detach_on_fork",
            {"session_id": 4, "enabled": False},
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.set_detach_on_fork.assert_called_once_with(enabled=False)

    def test_set_breakpoint_routes_to_correct_session(self):
        """Breakpoint requests should be routed to the resolved session."""

        manager = Mock()
        session = Mock()
        session.set_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": 1, "location": "main"})
        )
        manager.resolve_session.return_value = session

        dispatch("gdb_set_breakpoint", {"session_id": 3, "location": "main"}, manager)

        manager.resolve_session.assert_called_once_with(3)
        session.set_breakpoint.assert_called_once()

    def test_batch_routes_validated_steps_and_captures_stop_event(self):
        """Batch requests should execute validated session steps atomically."""

        manager = Mock()
        session = create_default_session_service()
        session.set_breakpoint = Mock(
            return_value=OperationSuccess(
                BreakpointInfo(breakpoint={"number": "1", "original_location": "main"})
            )
        )

        def continue_side_effect():
            session.runtime.mark_inferior_paused("breakpoint-hit")
            stop_event = StopEvent(
                execution_state="paused",
                reason="breakpoint-hit",
                command="-exec-continue",
                thread_id=2,
            )
            session.runtime.record_stop_event(stop_event)
            return OperationSuccess(CommandExecutionInfo(command="-exec-continue"))

        session.continue_execution = Mock(side_effect=continue_side_effect)
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_set_breakpoint",
                        "label": "break main",
                        "arguments": {"location": "main"},
                    },
                    {
                        "tool": "gdb_continue",
                        "label": "run until stop",
                        "arguments": {},
                    },
                ],
            },
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["count"] == 2
        assert result_data["completed_steps"] == 2
        assert result_data["error_count"] == 0
        assert result_data["stopped_early"] is False
        assert result_data["steps"][0]["tool"] == "gdb_set_breakpoint"
        assert result_data["steps"][0]["label"] == "break main"
        assert result_data["steps"][0]["status"] == "success"
        assert result_data["steps"][1]["tool"] == "gdb_continue"
        assert result_data["steps"][1]["stop_event"]["reason"] == "breakpoint-hit"
        assert result_data["last_stop_event"]["reason"] == "breakpoint-hit"
        session.set_breakpoint.assert_called_once_with(
            location="main",
            condition=None,
            temporary=False,
        )
        session.continue_execution.assert_called_once_with()

    def test_batch_stops_on_first_error_by_default(self):
        """Fail-fast batches should stop before executing later steps."""

        manager = Mock()
        session = create_default_session_service()
        session.execute_command = Mock(
            return_value=OperationError(message="boom", details={"command": "info threads"})
        )
        session.get_status = Mock(
            return_value=OperationSuccess(
                SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=False)
            )
        )
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_batch",
            {
                "session_id": 9,
                "steps": [
                    {
                        "tool": "gdb_execute_command",
                        "arguments": {"command": "info threads"},
                    },
                    {
                        "tool": "gdb_get_status",
                        "arguments": {},
                    },
                ],
            },
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["completed_steps"] == 1
        assert result_data["error_count"] == 1
        assert result_data["stopped_early"] is True
        assert result_data["failure_step_index"] == 0
        assert result_data["steps"][0]["status"] == "error"
        session.execute_command.assert_called_once_with(command="info threads", timeout_sec=30)
        session.get_status.assert_not_called()

    def test_batch_accepts_string_step_shorthand(self):
        """Batch requests should allow shorthand step strings."""

        manager = Mock()
        session = create_default_session_service()
        session.get_status = Mock(
            return_value=OperationSuccess(
                SessionStatusSnapshot(is_running=True, target_loaded=True, has_controller=True)
            )
        )
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_batch",
            {
                "session_id": 9,
                "steps": ["gdb_get_status"],
            },
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["completed_steps"] == 1
        assert result_data["steps"][0]["tool"] == "gdb_get_status"
        session.get_status.assert_called_once_with()

    def test_session_tool_dispatch_uses_workflow_lock(self):
        """Session-scoped tools should serialize through the workflow lock."""

        manager = Mock()
        session = Mock()
        workflow_lock = MagicMock()
        workflow_lock.__enter__.return_value = None
        workflow_lock.__exit__.return_value = None
        session.runtime = Mock(workflow_lock=workflow_lock)
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        manager.resolve_session.return_value = session

        dispatch("gdb_get_status", {"session_id": 1}, manager)

        workflow_lock.__enter__.assert_called_once()
        workflow_lock.__exit__.assert_called_once()

    def test_batch_rejects_step_level_session_id(self):
        """Batch steps should inherit session_id from the batch envelope only."""

        manager = Mock()
        session = create_default_session_service()
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_batch",
            {
                "session_id": 3,
                "steps": [
                    {
                        "tool": "gdb_get_status",
                        "arguments": {"session_id": 99},
                    }
                ],
            },
            manager,
        )

        assert result_data["status"] == "error"
        assert "must not include session_id" in result_data["message"]

    def test_capture_bundle_routes_to_session(self):
        """Capture requests should forward the bundle options to the resolved session."""

        manager = Mock()
        session = Mock()
        session.capture_bundle.return_value = OperationSuccess(
            SessionMessage(message="bundle written")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_capture_bundle",
            {
                "session_id": 5,
                "output_dir": "/tmp/bundles",
                "bundle_name": "case-1",
                "expressions": ["value", "result"],
                "memory_ranges": [
                    {"address": "&value", "count": 4, "name": "value-bytes"},
                ],
                "max_frames": 50,
                "include_transcript": False,
            },
            manager,
        )

        session.capture_bundle.assert_called_once_with(
            output_dir="/tmp/bundles",
            bundle_name="case-1",
            expressions=["value", "result"],
            memory_ranges=[
                MemoryCaptureRange(address="&value", count=4, offset=0, name="value-bytes")
            ],
            max_frames=50,
            include_threads=True,
            include_backtraces=True,
            include_frame=True,
            include_variables=True,
            include_registers=True,
            include_transcript=False,
            include_stop_history=True,
        )

    def test_capture_bundle_accepts_memory_range_shorthand(self):
        """Capture requests should parse shorthand memory-range strings."""

        manager = Mock()
        session = Mock()
        session.capture_bundle.return_value = OperationSuccess(SessionMessage(message="bundle written"))
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_capture_bundle",
            {
                "session_id": 5,
                "memory_ranges": ["&value:4@1"],
            },
            manager,
        )

        session.capture_bundle.assert_called_once()
        assert session.capture_bundle.call_args.kwargs["memory_ranges"] == [
            MemoryCaptureRange(address="&value", count=4, offset=1, name=None)
        ]

    def test_run_until_failure_capture_forwards_memory_ranges(self):
        """Campaign capture settings should forward explicit memory ranges to bundle capture."""

        manager = Mock()
        session = Mock()
        session.start.return_value = OperationSuccess(SessionMessage(message="started"))
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="paused",
                stop_reason="signal-received",
            )
        )
        session.last_stop_event = StopEvent(execution_state="paused", reason="signal-received")
        session.capture_bundle.return_value = OperationSuccess(SessionMessage(message="bundle"))
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        manager.create_untracked_session.return_value = session

        dispatch(
            "gdb_run_until_failure",
            {
                "startup": {"program": "/tmp/a.out"},
                "capture": {
                    "enabled": True,
                    "memory_ranges": [
                        {"address": "&value", "count": 8, "name": "value-bytes"}
                    ],
                },
            },
            manager,
        )

        session.capture_bundle.assert_called_once()
        assert session.capture_bundle.call_args.kwargs["memory_ranges"] == [
            MemoryCaptureRange(address="&value", count=8, offset=0, name="value-bytes")
        ]

    def test_run_until_failure_capture_accepts_exact_bundle_name(self):
        """Campaign capture should accept bundle_name alias for deterministic naming."""

        manager = Mock()
        session = Mock()
        session.start.return_value = OperationSuccess(SessionMessage(message="started"))
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="paused",
                stop_reason="signal-received",
            )
        )
        session.last_stop_event = StopEvent(execution_state="paused", reason="signal-received")
        session.capture_bundle.return_value = OperationSuccess(SessionMessage(message="bundle"))
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        manager.create_untracked_session.return_value = session

        dispatch(
            "gdb_run_until_failure",
            {
                "startup": {"program": "/tmp/a.out"},
                "capture": {
                    "enabled": True,
                    "bundle_name": "exact-bundle",
                },
            },
            manager,
        )

        session.capture_bundle.assert_called_once()
        assert session.capture_bundle.call_args.kwargs["bundle_name"] == "exact-bundle"

    def test_run_until_failure_uses_untracked_sessions(self):
        """Campaign requests should create fresh untracked sessions and return campaign data."""

        manager = Mock()
        session = Mock()
        session.start.return_value = OperationSuccess(SessionMessage(message="started"))
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="paused",
                stop_reason="signal-received",
            )
        )
        session.last_stop_event = StopEvent(execution_state="paused", reason="signal-received")
        session.capture_bundle.return_value = OperationSuccess(SessionMessage(message="bundle"))
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        manager.create_untracked_session.return_value = session

        result_data = dispatch(
            "gdb_run_until_failure",
            {
                "startup": {"program": "/tmp/a.out"},
                "max_iterations": 2,
                "capture": {"enabled": False},
            },
            manager,
        )

        manager.create_untracked_session.assert_called_once()
        session.start.assert_called_once_with(
            program="/tmp/a.out",
            args=None,
            init_commands=None,
            env=None,
            gdb_path=None,
            working_dir=None,
            core=None,
        )
        session.run.assert_called_once_with(args=None, timeout_sec=30)
        assert result_data["status"] == "success"
        assert result_data["matched_failure"] is True
        assert result_data["failure_iteration"] == 1
        assert result_data["trigger"] == "stop_reason:signal-received"
        assert result_data["capture_bundle"] is None

    def test_multiple_tools_use_different_sessions(self):
        """Separate session IDs should be routed independently."""

        manager = Mock()
        session_1 = Mock()
        session_1.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        session_2 = Mock()
        session_2.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=True, target_loaded=True, has_controller=True)
        )

        def resolve_session_side_effect(session_id):
            if session_id == 1:
                return session_1
            if session_id == 2:
                return session_2
            return OperationError(
                message=f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session."
            )

        manager.resolve_session.side_effect = resolve_session_side_effect

        result_1 = dispatch("gdb_get_status", {"session_id": 1}, manager)
        result_2 = dispatch("gdb_get_status", {"session_id": 2}, manager)

        assert manager.resolve_session.call_count == 2
        session_1.get_status.assert_called_once()
        session_2.get_status.assert_called_once()
        assert result_1["is_running"] is False
        assert result_2["is_running"] is True

    def test_evaluate_expression_routes_thread_and_frame_overrides(self):
        """Expression requests should forward optional context overrides."""

        manager = Mock()
        session = Mock()
        session.evaluate_expression.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-evaluate-expression")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_evaluate_expression",
            {"session_id": 1, "expression": "x", "thread_id": 2, "frame": 1},
            manager,
        )

        session.evaluate_expression.assert_called_once_with("x", thread_id=2, frame=1)

    def test_evaluate_expression_accepts_numeric_string_overrides(self):
        """Expression context overrides should accept numeric-string values."""

        manager = Mock()
        session = Mock()
        session.evaluate_expression.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-evaluate-expression")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_evaluate_expression",
            {"session_id": 1, "expression": "x", "thread_id": "2", "frame": "1"},
            manager,
        )

        session.evaluate_expression.assert_called_once_with("x", thread_id=2, frame=1)

    def test_get_registers_routes_thread_and_frame_overrides(self):
        """Register requests should forward optional context overrides."""

        manager = Mock()
        session = Mock()
        session.get_registers.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-list-register-values x")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_get_registers",
            {"session_id": 1, "thread_id": 2, "frame": 3},
            manager,
        )

        session.get_registers.assert_called_once_with(thread_id=2, frame=3)

    def test_get_registers_accepts_numeric_string_overrides(self):
        """Register context overrides should accept numeric-string values."""

        manager = Mock()
        session = Mock()
        session.get_registers.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-list-register-values x")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_get_registers",
            {"session_id": 1, "thread_id": "2", "frame": "3"},
            manager,
        )

        session.get_registers.assert_called_once_with(thread_id=2, frame=3)

    def test_get_backtrace_accepts_numeric_string_thread_id(self):
        """Backtrace requests should accept numeric-string thread IDs."""

        manager = Mock()
        session = Mock()
        session.get_backtrace.return_value = OperationSuccess(
            CommandExecutionInfo(command="-stack-list-frames")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_get_backtrace",
            {"session_id": 1, "thread_id": "2", "max_frames": 5},
            manager,
        )

        session.get_backtrace.assert_called_once_with(thread_id=2, max_frames=5)

    def test_get_variables_accepts_numeric_string_context(self):
        """Variable requests should accept numeric-string thread/frame selectors."""

        manager = Mock()
        session = Mock()
        session.get_variables.return_value = OperationSuccess({"variables": []})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_get_variables",
            {"session_id": 1, "thread_id": "2", "frame": "3"},
            manager,
        )

        session.get_variables.assert_called_once_with(thread_id=2, frame=3)

    def test_tool_definitions_match_dispatch_registry(self):
        """Every exported tool should have a matching dispatch path and vice versa."""

        exported_tools = {tool.name for tool in build_tool_definitions()}
        dispatched_tools = set(SESSION_TOOL_SPECS) | {
            "gdb_start_session",
            "gdb_run_until_failure",
            "gdb_stop_session",
            "gdb_list_sessions",
        }

        assert exported_tools == dispatched_tools
