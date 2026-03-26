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
from gdb_mcp.mcp.handlers import dispatch_tool_call


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

    def test_stop_session_removes_from_manager(self):
        """Successful stop should remove the session from the registry."""

        manager = Mock()
        session = Mock()
        session.stop.return_value = OperationSuccess(SessionMessage(message="Session stopped"))
        manager.get_session.return_value = session
        manager.remove_session.return_value = True

        result_data = dispatch("gdb_stop_session", {"session_id": 1}, manager)

        manager.get_session.assert_called_once_with(1)
        session.stop.assert_called_once()
        manager.remove_session.assert_called_once_with(1)
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

        dispatch("gdb_execute_command", {"session_id": 5, "command": "info threads"}, manager)

        manager.get_session.assert_called_once_with(5)
        session.execute_command.assert_called_once_with(command="info threads")

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
