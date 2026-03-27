"""Unit tests for MCP handler dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import Mock

from gdb_mcp.domain import (
    BreakpointInfo,
    CommandExecutionInfo,
    OperationError,
    OperationSuccess,
    SessionMessage,
    SessionStatusSnapshot,
)
from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS, dispatch_tool_call
from gdb_mcp.mcp.schemas import build_tool_definitions


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

    def test_tool_with_valid_session_id_works(self):
        """Session-scoped tools should route to the retrieved session."""

        manager = Mock()
        session = Mock()
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        manager.get_session.return_value = session

        result_data = dispatch("gdb_get_status", {"session_id": 1}, manager)

        manager.get_session.assert_called_once_with(1)
        session.get_status.assert_called_once()
        assert result_data == {
            "status": "success",
            "is_running": False,
            "target_loaded": False,
            "has_controller": True,
            "execution_state": "unknown",
            "stop_reason": None,
            "exit_code": None,
        }

    def test_tool_with_invalid_session_id_returns_error(self):
        """Invalid session IDs should fail before any tool execution."""

        manager = Mock()
        manager.get_session.return_value = None

        result_data = dispatch("gdb_get_status", {"session_id": 999}, manager)

        assert result_data["status"] == "error"
        assert "Invalid session_id: 999" in result_data["message"]
        assert "gdb_start_session" in result_data["message"]

    def test_tool_with_missing_session_id_returns_validation_error(self):
        """Known tools should validate arguments before session lookup."""

        manager = Mock()

        result_data = dispatch("gdb_get_status", {}, manager)

        assert result_data["status"] == "error"
        assert "session_id" in result_data["message"]
        manager.get_session.assert_not_called()

    def test_tool_with_non_object_arguments_returns_validation_error(self):
        """Non-dict arguments should fail as request validation errors."""

        manager = Mock()

        result_data = dispatch("gdb_get_status", ["not", "an", "object"], manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Tool arguments must be a JSON object"
        assert result_data["tool"] == "gdb_get_status"
        manager.get_session.assert_not_called()

    def test_tool_with_unknown_argument_returns_validation_error(self):
        """Unexpected tool arguments should be rejected at the MCP boundary."""

        manager = Mock()

        result_data = dispatch("gdb_get_status", {"session_id": 1, "unexpected": True}, manager)

        assert result_data["status"] == "error"
        assert "unexpected" in result_data["message"]
        manager.get_session.assert_not_called()

    def test_stop_session_uses_registry_close(self):
        """Successful stop should go through the registry lifecycle API."""

        manager = Mock()
        manager.close_session.return_value = OperationSuccess(
            SessionMessage(message="Session stopped")
        )

        result_data = dispatch("gdb_stop_session", {"session_id": 1}, manager)

        manager.close_session.assert_called_once_with(1)
        manager.get_session.assert_not_called()
        assert result_data["status"] == "success"

    def test_unknown_tool_returns_unknown_tool_error(self):
        """Unknown tools should fail before any session lookup happens."""

        manager = Mock()

        result_data = dispatch("gdb_typo_tool", {"session_id": 1}, manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Unknown tool: gdb_typo_tool"
        manager.get_session.assert_not_called()

    def test_execute_command_routes_to_correct_session(self):
        """Execute-command requests should forward the command payload."""

        manager = Mock()
        session = Mock()
        session.execute_command.return_value = OperationSuccess(
            CommandExecutionInfo(command="info threads", output="Thread info")
        )
        manager.get_session.return_value = session

        dispatch(
            "gdb_execute_command",
            {"session_id": 5, "command": "info threads", "timeout_sec": 12},
            manager,
        )

        manager.get_session.assert_called_once_with(5)
        session.execute_command.assert_called_once_with(command="info threads", timeout_sec=12)

    def test_run_routes_to_correct_session(self):
        """Structured run requests should forward argv and timeout."""

        manager = Mock()
        session = Mock()
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        manager.get_session.return_value = session

        dispatch(
            "gdb_run",
            {"session_id": 5, "args": ["--flag", "value"], "timeout_sec": 7},
            manager,
        )

        manager.get_session.assert_called_once_with(5)
        session.run.assert_called_once_with(args=["--flag", "value"], timeout_sec=7)

    def test_attach_process_routes_to_correct_session(self):
        """Attach requests should forward pid and timeout."""

        manager = Mock()
        session = Mock()
        session.attach_process.return_value = OperationSuccess(
            CommandExecutionInfo(command="attach 42")
        )
        manager.get_session.return_value = session

        dispatch(
            "gdb_attach_process",
            {"session_id": 3, "pid": 42, "timeout_sec": 9},
            manager,
        )

        manager.get_session.assert_called_once_with(3)
        session.attach_process.assert_called_once_with(pid=42, timeout_sec=9)

    def test_set_breakpoint_routes_to_correct_session(self):
        """Breakpoint requests should be routed to the resolved session."""

        manager = Mock()
        session = Mock()
        session.set_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": 1, "location": "main"})
        )
        manager.get_session.return_value = session

        dispatch("gdb_set_breakpoint", {"session_id": 3, "location": "main"}, manager)

        manager.get_session.assert_called_once_with(3)
        session.set_breakpoint.assert_called_once()

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

        def get_session_side_effect(session_id):
            if session_id == 1:
                return session_1
            if session_id == 2:
                return session_2
            return None

        manager.get_session.side_effect = get_session_side_effect

        result_1 = dispatch("gdb_get_status", {"session_id": 1}, manager)
        result_2 = dispatch("gdb_get_status", {"session_id": 2}, manager)

        assert manager.get_session.call_count == 2
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
        manager.get_session.return_value = session

        dispatch(
            "gdb_evaluate_expression",
            {"session_id": 1, "expression": "x", "thread_id": 2, "frame": 1},
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
        manager.get_session.return_value = session

        dispatch(
            "gdb_get_registers",
            {"session_id": 1, "thread_id": 2, "frame": 3},
            manager,
        )

        session.get_registers.assert_called_once_with(thread_id=2, frame=3)

    def test_tool_definitions_match_dispatch_registry(self):
        """Every exported tool should have a matching dispatch path and vice versa."""

        exported_tools = {tool.name for tool in build_tool_definitions()}
        dispatched_tools = set(SESSION_TOOL_SPECS) | {"gdb_start_session", "gdb_stop_session"}

        assert exported_tools == dispatched_tools
