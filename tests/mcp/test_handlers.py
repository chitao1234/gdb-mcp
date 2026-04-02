"""Unit tests for MCP handler dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock, Mock

from gdb_mcp.domain import (
    BreakpointInfo,
    CommandExecutionInfo,
    DisassemblyInfo,
    FrameInfo,
    InferiorListInfo,
    InferiorSelectionInfo,
    MemoryCaptureRange,
    OperationError,
    OperationSuccess,
    SessionListInfo,
    SessionMessage,
    SessionStatusSnapshot,
    SessionSummary,
    SourceContextInfo,
    StopEvent,
    ThreadSelectionInfo,
    VariablesInfo,
)
from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS, dispatch_tool_call
from gdb_mcp.mcp.schemas import build_tool_definitions
from gdb_mcp.session.factory import create_default_session_service


def _session_double() -> Mock:
    """Create a handler test double that satisfies the workflow-lock contract."""

    session = Mock()
    workflow_lock = MagicMock()
    workflow_lock.__enter__.return_value = None
    workflow_lock.__exit__.return_value = None
    session.runtime = Mock(workflow_lock=workflow_lock)
    session.controller = None
    session.is_running = False
    return session


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

        result_data = dispatch("gdb_session_start", {}, manager)

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
            "gdb_session_start",
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

        result_data = dispatch("gdb_session_start", {}, manager)

        assert result_data["status"] == "error"
        assert "session_id" not in result_data

    def test_session_query_list_routes_to_registry(self):
        """Global session inventory requests should route through gdb_session_query."""

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

        result_data = dispatch("gdb_session_query", {"action": "list", "query": {}}, manager)

        manager.list_sessions.assert_called_once()
        manager.resolve_session.assert_not_called()
        assert result_data["status"] == "success"
        assert result_data["action"] == "list"
        assert result_data["result"]["count"] == 1
        assert result_data["result"]["sessions"][0]["session_id"] == 1

    def test_tool_with_valid_session_id_works(self):
        """Session-scoped tools should route to the retrieved session."""

        manager = Mock()
        session = _session_double()
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(1)
        session.get_status.assert_called_once()
        assert result_data["status"] == "success"
        assert result_data["action"] == "status"
        assert result_data["result"]["is_running"] is False
        assert result_data["result"]["target_loaded"] is False

    def test_tool_with_invalid_session_id_returns_error(self):
        """Invalid session IDs should fail before any tool execution."""

        manager = Mock()
        manager.resolve_session.return_value = OperationError(
            message="Invalid session_id: 999. Use gdb_session_start to create a new session."
        )

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 999, "action": "status", "query": {}},
            manager,
        )

        assert result_data["status"] == "error"
        assert "Invalid session_id: 999" in result_data["message"]
        assert "gdb_session_start" in result_data["message"]

    def test_tool_with_closing_session_returns_closing_error(self):
        """Commands against closing sessions should get a precise lifecycle error."""

        manager = Mock()
        manager.resolve_session.return_value = OperationError(message="Session 2 is closing")

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 2, "action": "status", "query": {}},
            manager,
        )

        assert result_data["status"] == "error"
        assert "closing" in result_data["message"]

    def test_tool_with_missing_session_id_returns_validation_error(self):
        """Known tools should validate arguments before session lookup."""

        manager = Mock()

        result_data = dispatch("gdb_session_query", {"action": "status", "query": {}}, manager)

        assert result_data["status"] == "error"
        assert "session_id" in result_data["message"]
        manager.resolve_session.assert_not_called()

    def test_tool_with_non_object_arguments_returns_validation_error(self):
        """Non-dict arguments should fail as request validation errors."""

        manager = Mock()

        result_data = dispatch("gdb_session_query", ["not", "an", "object"], manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Tool arguments must be a JSON object"
        assert result_data["tool"] == "gdb_session_query"
        manager.resolve_session.assert_not_called()

    def test_tool_with_unknown_argument_returns_validation_error(self):
        """Unexpected tool arguments should be rejected at the MCP boundary."""

        manager = Mock()

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}, "unexpected": True},
            manager,
        )

        assert result_data["status"] == "error"
        assert "unexpected" in result_data["message"]
        manager.resolve_session.assert_not_called()

    def test_session_manage_stop_routes_to_registry_close(self):
        """Successful stop should go through the registry lifecycle API."""

        manager = Mock()
        manager.close_session.return_value = OperationSuccess(
            SessionMessage(message="Session stopped")
        )

        result_data = dispatch(
            "gdb_session_manage",
            {"session_id": 1, "action": "stop", "session": {}},
            manager,
        )

        manager.close_session.assert_called_once_with(1)
        manager.resolve_session.assert_not_called()
        assert result_data["status"] == "success"
        assert result_data["action"] == "stop"
        assert result_data["result"]["message"] == "Session stopped"

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
        session = _session_double()
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

    def test_execution_manage_run_routes_wait_policy(self):
        """Execution manage run should translate wait policy into session arguments."""

        manager = Mock()
        session = _session_double()
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 5,
                "action": "run",
                "execution": {
                    "args": ["--flag", "value"],
                    "wait": {"until": "acknowledged", "timeout_sec": 7},
                },
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(5)
        session.run.assert_called_once_with(
            args=["--flag", "value"],
            timeout_sec=7,
            wait_for_stop=False,
        )

    def test_run_accepts_shell_style_string_args(self):
        """String-form run args should be shell-split before forwarding."""

        manager = Mock()
        session = _session_double()
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 5,
                "action": "run",
                "execution": {
                    "args": '--flag "hello world"',
                    "wait": {"timeout_sec": 7},
                },
            },
            manager,
        )

        session.run.assert_called_once_with(
            args=["--flag", "hello world"],
            timeout_sec=7,
            wait_for_stop=True,
        )

    def test_add_inferior_routes_to_correct_session(self):
        """Inferior creation requests should forward executable and selection policy."""

        manager = Mock()
        session = _session_double()
        session.add_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_manage",
            {
                "session_id": 4,
                "action": "create",
                "inferior": {"executable": "/tmp/app", "make_current": True},
            },
            manager,
        )

        session.add_inferior.assert_called_once_with(
            executable="/tmp/app",
            make_current=True,
        )

    def test_remove_inferior_routes_to_correct_session(self):
        """Inferior removal requests should forward the normalized inferior ID."""

        manager = Mock()
        session = _session_double()
        session.remove_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_manage",
            {"session_id": 4, "action": "remove", "inferior": {"inferior_id": 2}},
            manager,
        )

        session.remove_inferior.assert_called_once_with(inferior_id=2)

    def test_execution_manage_continue_routes_ack_mode(self):
        """Execution manage continue should translate wait policy into session arguments."""

        manager = Mock()
        session = _session_double()
        session.continue_execution.return_value = OperationSuccess({"command": "-exec-continue"})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 4,
                "action": "continue",
                "execution": {"wait": {"until": "acknowledged", "timeout_sec": 5}},
            },
            manager,
        )

        session.continue_execution.assert_called_once_with(
            wait_for_stop=False,
            timeout_sec=5,
        )

    def test_finish_routes_to_execution_service(self):
        """Finish requests should forward timeout to the session execution API."""

        manager = Mock()
        session = _session_double()
        session.finish.return_value = OperationSuccess({"message": "finished"})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 4,
                "action": "finish",
                "execution": {"wait": {"timeout_sec": 9}},
            },
            manager,
        )

        session.finish.assert_called_once_with(timeout_sec=9, wait_for_stop=True)

    def test_attach_process_routes_to_correct_session(self):
        """Attach requests should forward pid and timeout."""

        manager = Mock()
        session = _session_double()
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

    def test_context_manage_select_thread_routes_to_service(self):
        """Context manage should route thread selection through the session service."""

        manager = Mock()
        session = _session_double()
        session.select_thread.return_value = OperationSuccess(ThreadSelectionInfo(thread_id=7))
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_context_manage",
            {
                "session_id": 2,
                "action": "select_thread",
                "context": {"thread_id": 7},
            },
            manager,
        )

        session.select_thread.assert_called_once_with(thread_id=7)

    def test_context_query_frame_routes_with_thread_and_frame_override(self):
        """Context query frame should route optional thread/frame overrides."""

        manager = Mock()
        session = _session_double()
        session.get_frame_info.return_value = OperationSuccess(
            FrameInfo(frame={"level": "1", "func": "worker"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_context_query",
            {
                "session_id": 2,
                "action": "frame",
                "query": {"thread_id": 7, "frame": 1},
            },
            manager,
        )

        session.get_frame_info.assert_called_once_with(thread_id=7, frame=1)

    def test_inspect_query_disassembly_routes_location_union(self):
        """Inspect query disassembly should route location unions into service kwargs."""

        manager = Mock()
        session = _session_double()
        session.disassemble.return_value = OperationSuccess(
            DisassemblyInfo(
                scope="function",
                thread_id=None,
                frame=None,
                function="main",
                file=None,
                fullname=None,
                line=None,
                start_address=None,
                end_address=None,
                mode="mixed",
                instructions=[],
                count=0,
            )
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 2,
                "action": "disassembly",
                "query": {
                    "location": {"kind": "function", "function": "main"},
                    "instruction_count": 8,
                    "mode": "mixed",
                },
            },
            manager,
        )

        session.disassemble.assert_called_once_with(
            thread_id=None,
            frame=None,
            function="main",
            address=None,
            start_address=None,
            end_address=None,
            file=None,
            line=None,
            instruction_count=8,
            mode="mixed",
        )

    def test_inspect_query_variables_routes_context_selector(self):
        """Inspect query variables should route optional context selection."""

        manager = Mock()
        session = _session_double()
        session.get_variables.return_value = OperationSuccess(
            VariablesInfo(thread_id=3, frame=1, variables=[])
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 2,
                "action": "variables",
                "query": {"context": {"thread_id": 3, "frame": 1}},
            },
            manager,
        )

        session.get_variables.assert_called_once_with(thread_id=3, frame=1)

    def test_inspect_query_source_routes_file_range(self):
        """Inspect query source should route file-range selectors."""

        manager = Mock()
        session = _session_double()
        session.get_source_context.return_value = OperationSuccess(
            SourceContextInfo(
                scope="file_range",
                thread_id=None,
                frame=None,
                function=None,
                address=None,
                file="main.c",
                fullname=None,
                line=None,
                start_line=10,
                end_line=12,
                lines=[],
                count=0,
            )
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 2,
                "action": "source",
                "query": {
                    "location": {
                        "kind": "file_range",
                        "file": "main.c",
                        "start_line": 10,
                        "end_line": 12,
                    },
                    "context_before": 0,
                    "context_after": 0,
                },
            },
            manager,
        )

        session.get_source_context.assert_called_once_with(
            thread_id=None,
            frame=None,
            function=None,
            address=None,
            file="main.c",
            line=None,
            start_line=10,
            end_line=12,
            context_before=0,
            context_after=0,
        )

    def test_breakpoint_query_get_routes_to_service(self):
        """Breakpoint query get should route through the breakpoint service."""

        manager = Mock()
        session = _session_double()
        session.get_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "4", "type": "breakpoint"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_breakpoint_query",
            {"session_id": 3, "action": "get", "query": {"number": 4}},
            manager,
        )

        session.get_breakpoint.assert_called_once_with(4)

    def test_breakpoint_manage_update_routes_changes(self):
        """Breakpoint manage update should route condition changes."""

        manager = Mock()
        session = _session_double()
        session.update_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(
                breakpoint={"number": "4", "type": "breakpoint", "exp": "count > 100"}
            )
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_breakpoint_manage",
            {
                "session_id": 3,
                "action": "update",
                "breakpoint": {"number": 4},
                "changes": {"condition": "count > 100", "clear_condition": False},
            },
            manager,
        )

        session.update_breakpoint.assert_called_once_with(
            4,
            condition="count > 100",
            clear_condition=False,
        )

    def test_inferior_query_current_returns_current_inferior(self):
        """Inferior query current should unwrap the selected inferior from inventory."""

        manager = Mock()
        session = _session_double()
        session.list_inferiors.return_value = OperationSuccess(
            InferiorListInfo(
                inferiors=[
                    {"inferior_id": 1, "is_current": True, "display": "i1"},
                    {"inferior_id": 2, "is_current": False, "display": "i2"},
                ],
                count=2,
                current_inferior_id=1,
            )
        )
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_inferior_query",
            {"session_id": 8, "action": "current", "query": {}},
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["action"] == "current"
        assert result_data["result"]["inferior"]["inferior_id"] == 1

    def test_inferior_manage_select_routes_to_service(self):
        """Inferior manage select should route through the session service."""

        manager = Mock()
        session = _session_double()
        session.select_inferior.return_value = OperationSuccess(
            InferiorSelectionInfo(inferior_id=2, is_current=True)
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_manage",
            {
                "session_id": 8,
                "action": "select",
                "inferior": {"inferior_id": 2},
            },
            manager,
        )

        session.select_inferior.assert_called_once_with(inferior_id=2)

    def test_set_watchpoint_routes_to_correct_session(self):
        """Watchpoint requests should forward expression and access mode."""

        manager = Mock()
        session = _session_double()
        session.set_watchpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "2", "type": "hw watchpoint"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_breakpoint_manage",
            {
                "session_id": 3,
                "action": "create",
                "breakpoint": {
                    "kind": "watch",
                    "expression": "value",
                    "access": "access",
                },
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.set_watchpoint.assert_called_once_with(expression="value", access="access")

    def test_delete_watchpoint_routes_to_correct_session(self):
        """Watchpoint deletion should forward the shared breakpoint number."""

        manager = Mock()
        session = _session_double()
        session.delete_breakpoint.return_value = OperationSuccess(
            SessionMessage(message="Breakpoint 2 deleted")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 3, "action": "delete", "breakpoint": {"number": 2}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.delete_breakpoint.assert_called_once_with(number=2)

    def test_set_catchpoint_routes_to_correct_session(self):
        """Catchpoint requests should forward kind, argument, and temporary flag."""

        manager = Mock()
        session = _session_double()
        session.set_catchpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "3", "type": "catchpoint"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_breakpoint_manage",
            {
                "session_id": 3,
                "action": "create",
                "breakpoint": {
                    "kind": "catch",
                    "event": "syscall",
                    "argument": "open",
                    "temporary": True,
                },
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
        session = _session_double()
        session.wait_for_stop.return_value = OperationSuccess({"matched": True})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 3,
                "action": "wait_for_stop",
                "execution": {"timeout_sec": 5, "stop_reasons": ["fork"]},
            },
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
        session = _session_double()
        session.read_memory.return_value = OperationSuccess({"captured_bytes": 4})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 3,
                "action": "memory",
                "query": {"address": "&value", "count": 4, "offset": 1},
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.read_memory.assert_called_once_with(address="&value", count=4, offset=1)

    def test_list_inferiors_routes_to_correct_session(self):
        """Inferior inventory requests should route to the resolved session."""

        manager = Mock()
        session = _session_double()
        session.list_inferiors.return_value = OperationSuccess({"count": 1, "inferiors": []})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_query",
            {"session_id": 4, "action": "list", "query": {}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.list_inferiors.assert_called_once_with()

    def test_select_inferior_routes_to_correct_session(self):
        """Inferior selection should forward the requested inferior ID."""

        manager = Mock()
        session = _session_double()
        session.select_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_manage",
            {"session_id": 4, "action": "select", "inferior": {"inferior_id": 2}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.select_inferior.assert_called_once_with(inferior_id=2)

    def test_set_follow_fork_mode_routes_to_correct_session(self):
        """Follow-fork-mode requests should forward the selected mode."""

        manager = Mock()
        session = _session_double()
        session.set_follow_fork_mode.return_value = OperationSuccess({"mode": "child"})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_manage",
            {
                "session_id": 4,
                "action": "set_follow_fork_mode",
                "inferior": {"mode": "child"},
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.set_follow_fork_mode.assert_called_once_with(mode="child")

    def test_set_detach_on_fork_routes_to_correct_session(self):
        """Detach-on-fork requests should forward the selected boolean value."""

        manager = Mock()
        session = _session_double()
        session.set_detach_on_fork.return_value = OperationSuccess({"enabled": False})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inferior_manage",
            {
                "session_id": 4,
                "action": "set_detach_on_fork",
                "inferior": {"enabled": False},
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.set_detach_on_fork.assert_called_once_with(enabled=False)

    def test_set_breakpoint_routes_to_correct_session(self):
        """Breakpoint requests should be routed to the resolved session."""

        manager = Mock()
        session = _session_double()
        session.set_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": 1, "location": "main"})
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_breakpoint_manage",
            {
                "session_id": 3,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main"},
            },
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.set_breakpoint.assert_called_once()

    def test_workflow_batch_routes_validated_steps_and_captures_stop_event(self):
        """Workflow batch requests should execute validated session steps atomically."""

        manager = Mock()
        session = create_default_session_service()
        session.set_breakpoint = Mock(
            return_value=OperationSuccess(
                BreakpointInfo(breakpoint={"number": "1", "original_location": "main"})
            )
        )

        def continue_side_effect(*, wait_for_stop, timeout_sec):
            assert wait_for_stop is True
            assert timeout_sec == 30
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
            "gdb_workflow_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_breakpoint_manage",
                        "label": "break main",
                        "arguments": {
                            "action": "create",
                            "breakpoint": {"kind": "code", "location": "main"},
                        },
                    },
                    {
                        "tool": "gdb_execution_manage",
                        "label": "run until stop",
                        "arguments": {"action": "continue"},
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
        assert result_data["steps"][0]["tool"] == "gdb_breakpoint_manage"
        assert result_data["steps"][0]["action"] == "create"
        assert result_data["steps"][0]["label"] == "break main"
        assert result_data["steps"][0]["status"] == "success"
        assert result_data["steps"][1]["tool"] == "gdb_execution_manage"
        assert result_data["steps"][1]["action"] == "continue"
        assert result_data["steps"][1]["stop_event"]["reason"] == "breakpoint-hit"
        assert result_data["last_stop_event"]["reason"] == "breakpoint-hit"
        session.set_breakpoint.assert_called_once_with(
            location="main",
            condition=None,
            temporary=False,
        )
        session.continue_execution.assert_called_once_with(wait_for_stop=True, timeout_sec=30)

    def test_workflow_batch_stops_on_first_error_by_default(self):
        """Fail-fast workflow batches should stop before executing later steps."""

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
            "gdb_workflow_batch",
            {
                "session_id": 9,
                "steps": [
                    {
                        "tool": "gdb_execute_command",
                        "arguments": {"command": "info threads"},
                    },
                    {
                        "tool": "gdb_session_query",
                        "arguments": {"action": "status", "query": {}},
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

    def test_workflow_batch_accepts_string_step_shorthand(self):
        """Workflow batch requests should allow shorthand step strings."""

        manager = Mock()
        session = create_default_session_service()
        session.capture_bundle = Mock(
            return_value=OperationSuccess(SessionMessage(message="bundle written"))
        )
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_workflow_batch",
            {
                "session_id": 9,
                "steps": ["gdb_capture_bundle"],
            },
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["completed_steps"] == 1
        assert result_data["steps"][0]["tool"] == "gdb_capture_bundle"
        session.capture_bundle.assert_called_once()

    def test_session_tool_dispatch_uses_workflow_lock(self):
        """Session-scoped tools should serialize through the workflow lock."""

        manager = Mock()
        session = _session_double()
        workflow_lock = MagicMock()
        workflow_lock.__enter__.return_value = None
        workflow_lock.__exit__.return_value = None
        session.runtime = Mock(workflow_lock=workflow_lock)
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}},
            manager,
        )

        workflow_lock.__enter__.assert_called_once()
        workflow_lock.__exit__.assert_called_once()

    def test_workflow_batch_rejects_step_level_session_id(self):
        """Workflow batch steps should inherit session_id from the batch envelope only."""

        manager = Mock()
        session = create_default_session_service()
        manager.resolve_session.return_value = session

        result_data = dispatch(
            "gdb_workflow_batch",
            {
                "session_id": 3,
                "steps": [
                    {
                        "tool": "gdb_capture_bundle",
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
        session = _session_double()
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
        session = _session_double()
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
        session = _session_double()
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
        session = _session_double()
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
        session = _session_double()
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
        session.run.assert_called_once_with(args=None, timeout_sec=30, wait_for_stop=True)
        assert result_data["status"] == "success"
        assert result_data["matched_failure"] is True
        assert result_data["failure_iteration"] == 1
        assert result_data["trigger"] == "stop_reason:signal-received"
        assert result_data["capture_bundle"] is None

    def test_multiple_tools_use_different_sessions(self):
        """Separate session IDs should be routed independently."""

        manager = Mock()
        session_1 = _session_double()
        session_1.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        session_2 = _session_double()
        session_2.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=True, target_loaded=True, has_controller=True)
        )

        def resolve_session_side_effect(session_id):
            if session_id == 1:
                return session_1
            if session_id == 2:
                return session_2
            return OperationError(
                message=f"Invalid session_id: {session_id}. Use gdb_session_start to create a new session."
            )

        manager.resolve_session.side_effect = resolve_session_side_effect

        result_1 = dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}},
            manager,
        )
        result_2 = dispatch(
            "gdb_session_query",
            {"session_id": 2, "action": "status", "query": {}},
            manager,
        )

        assert manager.resolve_session.call_count == 2
        session_1.get_status.assert_called_once()
        session_2.get_status.assert_called_once()
        assert result_1["result"]["is_running"] is False
        assert result_2["result"]["is_running"] is True

    def test_evaluate_expression_routes_thread_and_frame_overrides(self):
        """Expression requests should forward optional context overrides."""

        manager = Mock()
        session = _session_double()
        session.evaluate_expression.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-evaluate-expression")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "evaluate",
                "query": {"expression": "x", "context": {"thread_id": 2, "frame": 1}},
            },
            manager,
        )

        session.evaluate_expression.assert_called_once_with("x", thread_id=2, frame=1)

    def test_evaluate_expression_accepts_numeric_string_overrides(self):
        """Expression context overrides should accept numeric-string values."""

        manager = Mock()
        session = _session_double()
        session.evaluate_expression.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-evaluate-expression")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "evaluate",
                "query": {"expression": "x", "context": {"thread_id": "2", "frame": "1"}},
            },
            manager,
        )

        session.evaluate_expression.assert_called_once_with("x", thread_id=2, frame=1)

    def test_get_registers_routes_thread_and_frame_overrides(self):
        """Register requests should forward optional context overrides."""

        manager = Mock()
        session = _session_double()
        session.get_registers.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-list-register-values x")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "registers",
                "query": {"context": {"thread_id": 2, "frame": 3}},
            },
            manager,
        )

        session.get_registers.assert_called_once_with(
            thread_id=2,
            frame=3,
            register_numbers=None,
            register_names=None,
            include_vector_registers=True,
            max_registers=None,
            value_format="hex",
        )

    def test_get_registers_accepts_numeric_string_overrides(self):
        """Register context overrides should accept numeric-string values."""

        manager = Mock()
        session = _session_double()
        session.get_registers.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-list-register-values x")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "registers",
                "query": {"context": {"thread_id": "2", "frame": "3"}},
            },
            manager,
        )

        session.get_registers.assert_called_once_with(
            thread_id=2,
            frame=3,
            register_numbers=None,
            register_names=None,
            include_vector_registers=True,
            max_registers=None,
            value_format="hex",
        )

    def test_get_registers_routes_filter_and_format_options(self):
        """Register requests should forward selector and rendering options."""

        manager = Mock()
        session = _session_double()
        session.get_registers.return_value = OperationSuccess(
            CommandExecutionInfo(command="-data-list-register-values N")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "registers",
                "query": {
                    "register_numbers": ["0", 1],
                    "register_names": ["rip", "rax"],
                    "include_vector_registers": False,
                    "max_registers": 8,
                    "value_format": "natural",
                },
            },
            manager,
        )

        session.get_registers.assert_called_once_with(
            thread_id=None,
            frame=None,
            register_numbers=[0, 1],
            register_names=["rip", "rax"],
            include_vector_registers=False,
            max_registers=8,
            value_format="natural",
        )

    def test_get_backtrace_accepts_numeric_string_thread_id(self):
        """Backtrace requests should accept numeric-string thread IDs."""

        manager = Mock()
        session = _session_double()
        session.get_backtrace.return_value = OperationSuccess(
            CommandExecutionInfo(command="-stack-list-frames")
        )
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_context_query",
            {
                "session_id": 1,
                "action": "backtrace",
                "query": {"thread_id": "2", "max_frames": 5},
            },
            manager,
        )

        session.get_backtrace.assert_called_once_with(thread_id=2, max_frames=5)

    def test_disassemble_routes_normalized_selectors(self):
        """Disassembly requests should forward normalized selector values."""

        manager = Mock()
        session = _session_double()
        session.disassemble.return_value = OperationSuccess({"count": 0, "instructions": []})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 4,
                "action": "disassembly",
                "query": {
                    "context": {"thread_id": "2", "frame": "1"},
                    "location": {"kind": "current"},
                    "instruction_count": 12,
                    "mode": "mixed",
                },
            },
            manager,
        )

        session.disassemble.assert_called_once_with(
            thread_id=2,
            frame=1,
            function=None,
            address=None,
            start_address=None,
            end_address=None,
            file=None,
            line=None,
            instruction_count=12,
            mode="mixed",
        )

    def test_get_variables_accepts_numeric_string_context(self):
        """Variable requests should accept numeric-string thread/frame selectors."""

        manager = Mock()
        session = _session_double()
        session.get_variables.return_value = OperationSuccess({"variables": []})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "variables",
                "query": {"context": {"thread_id": "2", "frame": "3"}},
            },
            manager,
        )

        session.get_variables.assert_called_once_with(thread_id=2, frame=3)

    def test_get_source_context_routes_normalized_selectors(self):
        """Source-context requests should forward normalized selector values."""

        manager = Mock()
        session = _session_double()
        session.get_source_context.return_value = OperationSuccess({"count": 0, "lines": []})
        manager.resolve_session.return_value = session

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 4,
                "action": "source",
                "query": {
                    "location": {"kind": "file_line", "file": "main.c", "line": "12"},
                    "context_before": 2,
                    "context_after": 3,
                },
            },
            manager,
        )

        session.get_source_context.assert_called_once_with(
            thread_id=None,
            frame=None,
            function=None,
            address=None,
            file="main.c",
            line=12,
            start_line=None,
            end_line=None,
            context_before=2,
            context_after=3,
        )

    def test_tool_definitions_match_dispatch_registry(self):
        """Every exported tool should have a matching dispatch path and vice versa."""

        exported_tools = {tool.name for tool in build_tool_definitions()}
        dispatched_tools = set(SESSION_TOOL_SPECS) | {
            "gdb_session_start",
            "gdb_session_query",
            "gdb_session_manage",
            "gdb_run_until_failure",
        }

        assert exported_tools == dispatched_tools
