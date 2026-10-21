"""Unit tests for MCP handler dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from mcp.types import TextContent

from gdb_mcp.contracts import (
    ACTION_JUMP,
    ACTION_UNTIL,
    DEBUGGER_GDB,
    DEBUGGER_LLDB,
    TOOL_EXECUTION_MANAGE,
    DebuggerName,
    SessionState,
)
from gdb_mcp.domain import (
    AddressRange,
    BackendData,
    BreakpointInfo,
    BreakpointListInfo,
    CommandExecutionInfo,
    DisassemblyInfo,
    ExecutionState,
    FrameInfo,
    InferiorListInfo,
    InferiorRecord,
    InferiorSelectionInfo,
    InspectionContextInfo,
    InspectionLocation,
    MemoryCaptureRange,
    OperationError,
    OperationSuccess,
    SessionListInfo,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
    SessionSummary,
    SourceContextInfo,
    SourceRange,
    StartupDiagnostics,
    StopEvent,
    ThreadSelectionInfo,
    VariablesInfo,
)
from gdb_mcp.domain.adapters import breakpoint_record, frame_record
from gdb_mcp.mcp.handlers import TOOL_SPECS, dispatch_tool_call
from gdb_mcp.mcp.schemas import build_tool_definitions
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.service import SessionService
from tests.support import dig, mapping, mapping_list, text


def _startup_diagnostics(debugger: DebuggerName = DEBUGGER_GDB) -> StartupDiagnostics:
    """Return the explicit diagnostics every successful startup publishes."""

    return StartupDiagnostics(
        environment_results=[],
        initialization_results=[],
        backend_data=BackendData(debugger=debugger, console_output="", data={}),
    )


def _workflow_lock() -> MagicMock:
    """Create a context-manager double used as the session workflow lock."""

    workflow_lock = MagicMock()
    workflow_lock.__enter__.return_value = None
    workflow_lock.__exit__.return_value = None
    return workflow_lock


def _session_double(
    *,
    execution_state: ExecutionState = "unknown",
    stop_reason: str | None = None,
    exit_code: int | None = None,
) -> Mock:
    """Create a handler test double that satisfies the workflow-lock contract."""

    session = Mock(spec=SessionService)
    runtime = create_default_session_service().runtime
    runtime.execution_state = execution_state
    runtime.stop_reason = stop_reason
    runtime.exit_code = exit_code
    runtime.workflow_lock = _workflow_lock()
    session.runtime = runtime
    session.has_controller = False
    session.is_running = False
    return session


def _manager_for(session: object) -> Mock:
    """Create a registry double that resolves every session ID to the session."""

    manager = Mock(spec=SessionRegistry)
    manager.resolve_session.return_value = session
    return manager


def _command_result(command: str, *, console_output: str = "") -> CommandExecutionInfo:
    """Return one explicitly tagged GDB command result for a handler double."""

    return CommandExecutionInfo(
        command=command,
        backend_data=BackendData(debugger=DEBUGGER_GDB, console_output=console_output, data={}),
    )


def _stub(session: object, name: str, **kwargs: object) -> Mock:
    """Install a Mock method on a test double and return it."""

    recorder = Mock(**kwargs)
    setattr(session, name, recorder)
    return recorder


def _failure_session(manager: Mock) -> Mock:
    """Create a campaign session double and register it as the untracked session."""

    session = _session_double()
    session.start.return_value = OperationSuccess(SessionMessage(message="started"))
    session.run.return_value = OperationSuccess(_command_result("-exec-run"))
    session.get_status.return_value = OperationSuccess(
        SessionStatusSnapshot(
            debugger_alive=True,
            target_loaded=True,
            session_state=SessionState.READY,
            execution_state="paused",
            stop_reason="signal-received",
        )
    )
    session.last_stop_event = StopEvent(execution_state="paused", reason="signal-received")
    session.capture_bundle.return_value = OperationSuccess(SessionMessage(message="bundle"))
    session.has_controller = True
    session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
    manager.create_untracked_session.return_value = session
    return session


def _continue_side_effect(
    session: SessionService | Mock,
) -> Callable[..., OperationSuccess[CommandExecutionInfo]]:
    """Build a continue stub that records a breakpoint stop on the session runtime."""

    def continue_side_effect(
        *, wait_for_stop: bool, timeout_sec: int, reverse: bool
    ) -> OperationSuccess[CommandExecutionInfo]:
        assert wait_for_stop is True
        assert timeout_sec == 30
        assert reverse is False
        session.runtime.mark_inferior_paused("breakpoint-hit")
        stop_event = StopEvent(
            execution_state="paused",
            reason="breakpoint-hit",
            command="-exec-continue",
            thread_id=2,
        )
        session.runtime.record_stop_event(stop_event)
        return OperationSuccess(_command_result("-exec-continue"))

    return continue_side_effect


def dispatch(name: str, arguments: object, session_manager: object) -> dict[str, object]:
    """Call the structured MCP dispatcher and parse its JSON payload."""

    result = asyncio.run(
        dispatch_tool_call(
            name,
            arguments,
            cast(SessionRegistry, session_manager),
            logger=logging.getLogger("test-mcp-handlers"),
        )
    )
    content = result.content[0]
    assert isinstance(content, TextContent)
    return mapping(json.loads(content.text))


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
def test_location_execution_routes_location_and_wait_policy(action: str) -> None:
    session = _session_double()
    operation = session.jump if action == ACTION_JUMP else session.until
    operation.return_value = OperationSuccess({"command": action})

    result = dispatch(
        TOOL_EXECUTION_MANAGE,
        {
            "session_id": 7,
            "action": action,
            "execution": {
                "location": "*$pc + 4",
                "wait_until": "acknowledged",
                "timeout_sec": 5,
            },
        },
        _manager_for(session),
    )

    assert result["status"] == "success"
    assert result["action"] == action
    operation.assert_called_once_with(
        location="*$pc + 4",
        wait_for_stop=False,
        timeout_sec=5,
    )


class TestHandlerDispatch:
    """Test direct MCP handler dispatch against the handler boundary."""

    def test_start_session_returns_session_id(self) -> None:
        """Successful startup should include the published session ID."""

        manager = Mock(spec=SessionRegistry)
        manager.start_session.return_value = OperationSuccess(
            SessionStartInfo(
                session_id=42,
                message="Session started",
                diagnostics=_startup_diagnostics(),
            ),
            warnings=("symbols missing",),
        )

        result_data = dispatch("gdb_session_start", {}, manager)

        manager.start_session.assert_called_once()
        assert result_data["status"] == "success"
        assert dig(result_data, "result", "session_id") == 42
        assert result_data["warnings"] == ["symbols missing"]

    def test_start_session_normalizes_shell_style_string_args(self) -> None:
        """Startup args in shell-string form should be normalized before session creation."""

        manager = Mock(spec=SessionRegistry)
        manager.start_session.return_value = OperationSuccess(
            SessionStartInfo(
                session_id=7,
                message="Session started",
                diagnostics=_startup_diagnostics(),
            ),
        )

        dispatch(
            "gdb_session_start",
            {"program": "/bin/echo", "args": '--flag "hello world"'},
            manager,
        )

        manager.start_session.assert_called_once_with(
            program="/bin/echo",
            debugger="gdb",
            args=["--flag", "hello world"],
            init_commands=None,
            env=None,
            debugger_path=None,
            debugger_args=None,
            working_dir=None,
            core=None,
            rr_replay=None,
        )

    def test_start_session_failure_does_not_expose_session_id(self) -> None:
        """Failed startup should not expose a session ID."""

        manager = Mock(spec=SessionRegistry)
        manager.start_session.return_value = OperationError(message="Startup failed")

        result_data = dispatch("gdb_session_start", {}, manager)

        assert result_data["status"] == "error"
        assert "session_id" not in result_data

    def test_connect_session_passes_endpoint_to_registry(self) -> None:
        """Connecting should forward the endpoint and publish the session ID."""

        manager = Mock(spec=SessionRegistry)
        manager.start_session.return_value = OperationSuccess(
            SessionStartInfo(
                session_id=11,
                message="GDB session started",
                diagnostics=_startup_diagnostics(),
            ),
        )

        result_data = dispatch(
            "gdb_session_connect",
            {"endpoint": "tcp://127.0.0.1:4399", "init_commands": ["set pagination off"]},
            manager,
        )

        manager.start_session.assert_called_once_with(
            endpoint="tcp://127.0.0.1:4399",
            init_commands=["set pagination off"],
            debugger="gdb",
            program=None,
            debugger_path=None,
        )
        assert result_data["status"] == "success"
        assert dig(result_data, "result", "session_id") == 11

    def test_connect_session_passes_lldb_remote_fields_to_registry(self) -> None:
        """An LLDB remote connection forwards its backend and target fields."""

        manager = Mock(spec=SessionRegistry)
        manager.start_session.return_value = OperationSuccess(
            SessionStartInfo(
                session_id=12,
                message="LLDB session started",
                diagnostics=_startup_diagnostics(DEBUGGER_LLDB),
            ),
        )

        result_data = dispatch(
            "gdb_session_connect",
            {
                "debugger": "lldb",
                "endpoint": "connect://127.0.0.1:12345",
                "program": "/tmp/app",
                "debugger_path": "/opt/lldb/19/bin/lldb",
            },
            manager,
        )

        manager.start_session.assert_called_once_with(
            endpoint="connect://127.0.0.1:12345",
            init_commands=None,
            debugger="lldb",
            program="/tmp/app",
            debugger_path="/opt/lldb/19/bin/lldb",
        )
        assert result_data["status"] == "success"
        assert dig(result_data, "result", "session_id") == 12

    def test_connect_session_failure_does_not_expose_session_id(self) -> None:
        """A failed endpoint connection should not expose a session ID."""

        manager = Mock(spec=SessionRegistry)
        manager.start_session.return_value = (
            None,
            OperationError(message="Could not reach MI endpoint"),
        )

        result_data = dispatch(
            "gdb_session_connect",
            {"endpoint": "unix:///tmp/nope.sock"},
            manager,
        )

        assert result_data["status"] == "error"
        assert "session_id" not in result_data

    def test_session_query_list_routes_to_registry(self) -> None:
        """Global session inventory requests should route through gdb_session_query."""

        manager = Mock(spec=SessionRegistry)
        manager.list_sessions.return_value = OperationSuccess(
            SessionListInfo(
                sessions=[
                    SessionSummary(
                        session_id=1,
                        session_state=SessionState.READY,
                        execution_state="paused",
                        target_loaded=True,
                        debugger_alive=True,
                        program="/tmp/a.out",
                    )
                ],
            )
        )

        result_data = dispatch("gdb_session_query", {"action": "list", "query": {}}, manager)

        manager.list_sessions.assert_called_once()
        manager.resolve_session.assert_not_called()
        assert result_data["status"] == "success"
        assert result_data["action"] == "list"
        assert len(mapping_list(dig(result_data, "result", "sessions"))) == 1
        assert mapping_list(dig(result_data, "result", "sessions"))[0]["session_id"] == 1

    def test_tool_with_valid_session_id_works(self) -> None:
        """Session-scoped tools should route to the retrieved session."""

        session = _session_double()
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=False, target_loaded=False, session_state=SessionState.READY
            )
        )
        manager = _manager_for(session)

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(1)
        session.get_status.assert_called_once()
        assert result_data["status"] == "success"
        assert result_data["action"] == "status"
        assert dig(result_data, "result", "debugger_alive") is False
        assert dig(result_data, "result", "target_loaded") is False

    def test_tool_with_invalid_session_id_returns_error(self) -> None:
        """Invalid session IDs should fail before any tool execution."""

        manager = Mock(spec=SessionRegistry)
        manager.resolve_session.return_value = OperationError(
            message="Invalid session_id: 999. Use gdb_session_start to create a new session."
        )

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 999, "action": "status", "query": {}},
            manager,
        )

        assert result_data["status"] == "error"
        assert "Invalid session_id: 999" in text(result_data["message"])
        assert "gdb_session_start" in text(result_data["message"])

    def test_tool_with_closing_session_returns_closing_error(self) -> None:
        """Commands against closing sessions should get a precise lifecycle error."""

        manager = Mock(spec=SessionRegistry)
        manager.resolve_session.return_value = OperationError(message="Session 2 is closing")

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 2, "action": "status", "query": {}},
            manager,
        )

        assert result_data["status"] == "error"
        assert "closing" in text(result_data["message"])


class TestHandlerRouting:
    """Test handler routing, argument validation, and session failure annotation."""

    # Session-scoped failures should carry the state the caller needs to recover.

    def _dispatch_backtrace_failure(
        self,
        message: str,
        *,
        execution_state: ExecutionState = "unknown",
        stop_reason: str | None = None,
        exit_code: int | None = None,
    ) -> dict[str, object]:
        """Dispatch a backtrace failure with the supplied runtime state."""

        session = _session_double(
            execution_state=execution_state, stop_reason=stop_reason, exit_code=exit_code
        )
        session.get_backtrace.return_value = OperationError(
            message=message, details={"command": "-stack-list-frames 0 1"}
        )
        manager = _manager_for(session)

        return dispatch(
            "gdb_context_query",
            {"session_id": 11, "action": "backtrace", "query": {}},
            manager,
        )

    def test_failure_carries_session_state_and_specific_code(self) -> None:
        """Failure details should include the session state behind the error code."""

        result_data = self._dispatch_backtrace_failure(
            "No registers.",
            execution_state="exited",
            stop_reason="exited",
            exit_code=1,
        )

        assert result_data["code"] == "no_registers"
        assert dig(result_data, "details", "command") == "-stack-list-frames 0 1"
        assert dig(result_data, "details", "session_id") == 11
        assert dig(result_data, "details", "execution_state") == "exited"
        assert dig(result_data, "details", "stop_reason") == "exited"
        assert dig(result_data, "details", "exit_code") == 1

    def test_absent_state_is_omitted(self) -> None:
        """Unavailable runtime state should not appear in failure details."""

        result_data = self._dispatch_backtrace_failure("No registers.", execution_state="paused")

        assert dig(result_data, "details", "execution_state") == "paused"
        assert "exit_code" not in mapping(result_data["details"])
        assert "stop_reason" not in mapping(result_data["details"])

    def test_unclassified_failure_keeps_the_generic_code(self) -> None:
        """Unrecognized failure messages should keep the generic error code."""

        result_data = self._dispatch_backtrace_failure(
            "something inexplicable", execution_state="paused"
        )

        assert result_data["code"] == "error"
        assert dig(result_data, "details", "execution_state") == "paused"

    @pytest.mark.parametrize(
        ("message", "expected_code"),
        [
            ("No registers.", "no_registers"),
            ("The program is not being run.", "inferior_not_running"),
            (
                "You can't do that without a process to debug.",
                "inferior_not_running",
            ),
            (
                "Cannot execute this command while the selected thread is running.",
                "inferior_running",
            ),
            ("No stack.", "no_stack_frame"),
            ("Cannot access memory at address 0x7fffffffe000", "memory_unavailable"),
            ('No symbol "folio" in current context.', "symbol_unavailable"),
            ("value has been optimized out", "value_unavailable"),
            ("Timeout waiting for command response after 30s", "command_timeout"),
            ("GDB returned an error", "error"),
        ],
    )
    def test_failure_messages_map_to_branchable_codes(
        self, message: str, expected_code: str
    ) -> None:
        """Recognized GDB messages should map to branchable failure codes."""

        result_data = self._dispatch_backtrace_failure(message)

        assert result_data["code"] == expected_code

    def test_successful_results_are_not_annotated(self) -> None:
        """Successful results should not receive failure-state annotations."""

        session = _session_double(execution_state="paused")
        session.get_backtrace.return_value = OperationSuccess({"thread_id": 1, "frames": []})
        manager = _manager_for(session)

        result_data = dispatch(
            "gdb_context_query",
            {"session_id": 11, "action": "backtrace", "query": {}},
            manager,
        )

        assert result_data["status"] == "success"
        assert "execution_state" not in result_data

    def test_tool_with_missing_session_id_returns_validation_error(self) -> None:
        """Known tools should validate arguments before session lookup."""

        manager = Mock(spec=SessionRegistry)

        result_data = dispatch("gdb_session_query", {"action": "status", "query": {}}, manager)

        assert result_data["status"] == "error"
        assert "session_id" in text(result_data["message"])
        manager.resolve_session.assert_not_called()

    def test_tool_with_non_object_arguments_returns_validation_error(self) -> None:
        """Non-dict arguments should fail as request validation errors."""

        manager = Mock(spec=SessionRegistry)

        result_data = dispatch("gdb_session_query", ["not", "an", "object"], manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Tool arguments must be a JSON object"
        assert result_data["tool"] == "gdb_session_query"
        manager.resolve_session.assert_not_called()

    def test_tool_with_unknown_argument_returns_validation_error(self) -> None:
        """Unexpected tool arguments should be rejected at the MCP boundary."""

        manager = Mock(spec=SessionRegistry)

        result_data = dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}, "unexpected": True},
            manager,
        )

        assert result_data["status"] == "error"
        assert "unexpected" in text(result_data["message"])
        manager.resolve_session.assert_not_called()

    def test_session_manage_stop_routes_to_registry_close(self) -> None:
        """Successful stop should go through the registry lifecycle API."""

        manager = Mock(spec=SessionRegistry)
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
        assert dig(result_data, "result", "message") == "Session stopped"

    def test_unknown_tool_returns_unknown_tool_error(self) -> None:
        """Unknown tools should fail before any session lookup happens."""

        manager = Mock(spec=SessionRegistry)

        result_data = dispatch("gdb_typo_tool", {"session_id": 1}, manager)

        assert result_data["status"] == "error"
        assert result_data["message"] == "Unknown tool: gdb_typo_tool"
        manager.resolve_session.assert_not_called()

    def test_execute_command_routes_to_correct_session(self) -> None:
        """Execute-command requests should forward the command payload."""

        session = _session_double()
        session.execute_command.return_value = OperationSuccess(
            _command_result("info threads", console_output="Thread info")
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_execute_command",
            {"session_id": 5, "command": "info threads", "timeout_sec": 12},
            manager,
        )

        manager.resolve_session.assert_called_once_with(5)
        session.execute_command.assert_called_once_with(command="info threads", timeout_sec=12)

    def test_execution_manage_run_routes_wait_policy(self) -> None:
        """Execution manage run should translate wait policy into session arguments."""

        session = _session_double()
        session.run.return_value = OperationSuccess(_command_result("-exec-run"))
        manager = _manager_for(session)

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 5,
                "action": "run",
                "execution": {
                    "args": ["--flag", "value"],
                    "wait_until": "acknowledged",
                    "timeout_sec": 7,
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

    def test_execution_run_accepts_omitted_execution_payload(self) -> None:
        """Run should work without an explicit execution payload."""

        session = _session_double()
        session.run.return_value = OperationSuccess(_command_result("-exec-run"))
        manager = _manager_for(session)

        dispatch(
            "gdb_execution_manage",
            {"session_id": 5, "action": "run"},
            manager,
        )

        session.run.assert_called_once_with(args=None, timeout_sec=30, wait_for_stop=True)

    def test_run_accepts_shell_style_string_args(self) -> None:
        """String-form run args should be shell-split before forwarding."""

        session = _session_double()
        session.run.return_value = OperationSuccess(_command_result("-exec-run"))
        manager = _manager_for(session)

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 5,
                "action": "run",
                "execution": {
                    "args": '--flag "hello world"',
                    "timeout_sec": 7,
                },
            },
            manager,
        )

        session.run.assert_called_once_with(
            args=["--flag", "hello world"],
            timeout_sec=7,
            wait_for_stop=True,
        )

    def test_add_inferior_routes_to_correct_session(self) -> None:
        """Inferior creation requests should forward executable and selection policy."""

        session = _session_double()
        session.add_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager = _manager_for(session)

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

    def test_add_inferior_accepts_omitted_inferior_payload(self) -> None:
        """Inferior creation should work without an explicit inferior payload."""

        session = _session_double()
        session.add_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager = _manager_for(session)

        dispatch(
            "gdb_inferior_manage",
            {"session_id": 4, "action": "create"},
            manager,
        )

        session.add_inferior.assert_called_once_with(executable=None, make_current=False)

    def test_remove_inferior_routes_to_correct_session(self) -> None:
        """Inferior removal requests should forward the normalized inferior ID."""

        session = _session_double()
        session.remove_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager = _manager_for(session)

        dispatch(
            "gdb_inferior_manage",
            {"session_id": 4, "action": "remove", "inferior": {"inferior_id": 2}},
            manager,
        )

        session.remove_inferior.assert_called_once_with(inferior_id=2)

    def test_execution_manage_continue_routes_ack_mode(self) -> None:
        """Execution manage continue should translate wait policy into session arguments."""

        session = _session_double()
        session.continue_execution.return_value = OperationSuccess({"command": "-exec-continue"})
        manager = _manager_for(session)

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 4,
                "action": "continue",
                "execution": {"wait_until": "acknowledged", "timeout_sec": 5},
            },
            manager,
        )

        session.continue_execution.assert_called_once_with(
            wait_for_stop=False,
            timeout_sec=5,
            reverse=False,
        )

    def test_execution_manage_reverse_direction_reaches_the_session(self) -> None:
        """A reverse request should be forwarded as reverse execution."""

        session = _session_double()
        session.step.return_value = OperationSuccess({"command": "-exec-step --reverse"})
        manager = _manager_for(session)

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 4,
                "action": "step",
                "execution": {"reverse": True, "wait_until": "stop"},
            },
            manager,
        )

        session.step.assert_called_once_with(wait_for_stop=True, timeout_sec=30, reverse=True)

    def test_finish_routes_to_execution_service(self) -> None:
        """Finish requests should forward timeout to the session execution API."""

        session = _session_double()
        session.finish.return_value = OperationSuccess({"message": "finished"})
        manager = _manager_for(session)

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 4,
                "action": "finish",
                "execution": {"timeout_sec": 9},
            },
            manager,
        )

        session.finish.assert_called_once_with(timeout_sec=9, wait_for_stop=True, reverse=False)

    def test_attach_process_routes_to_correct_session(self) -> None:
        """Attach requests should forward pid and timeout."""

        session = _session_double()
        session.attach_process.return_value = OperationSuccess(_command_result("attach 42"))
        manager = _manager_for(session)

        dispatch(
            "gdb_attach_process",
            {"session_id": 3, "pid": 42, "timeout_sec": 9},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.attach_process.assert_called_once_with(pid=42, timeout_sec=9)

    def test_context_manage_select_thread_routes_to_service(self) -> None:
        """Context manage should route thread selection through the session service."""

        session = _session_double()
        session.select_thread.return_value = OperationSuccess(ThreadSelectionInfo(thread_id=7))
        manager = _manager_for(session)

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

    def test_context_manage_select_frame_routes_to_service(self) -> None:
        """Context manage should route frame selection through the session service."""

        session = _session_double()
        session.select_frame.return_value = OperationSuccess(
            FrameInfo(frame=frame_record({"level": "2", "func": "worker"}))
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_context_manage",
            {
                "session_id": 2,
                "action": "select_frame",
                "context": {"frame": 2},
            },
            manager,
        )

        session.select_frame.assert_called_once_with(frame_number=2)

    def test_context_query_threads_routes_to_service(self) -> None:
        """Context query threads should route through the session service."""

        session = _session_double()
        session.get_threads.return_value = OperationSuccess(
            {"threads": [{"id": "1"}], "current_thread_id": "1"}
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_context_query",
            {
                "session_id": 2,
                "action": "threads",
                "query": {},
            },
            manager,
        )

        session.get_threads.assert_called_once_with()

    def test_context_query_frame_routes_with_thread_and_frame_override(self) -> None:
        """Context query frame should route optional thread/frame overrides."""

        session = _session_double()
        session.get_frame_info.return_value = OperationSuccess(
            FrameInfo(frame=frame_record({"level": "1", "func": "worker"}))
        )
        manager = _manager_for(session)

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

    def test_inspect_query_disassembly_routes_location_union(self) -> None:
        """Inspect query disassembly should route location unions into service kwargs."""

        session = _session_double()
        session.disassemble.return_value = OperationSuccess(
            DisassemblyInfo(
                context=InspectionContextInfo(thread_id=None, frame=None),
                location=InspectionLocation(kind="function", function="main"),
                range=AddressRange(start_address=None, end_address=None),
                mode="mixed",
                instructions=[],
            )
        )
        manager = _manager_for(session)

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

    def test_inspect_query_variables_routes_context_selector(self) -> None:
        """Inspect query variables should route optional context selection."""

        session = _session_double()
        session.get_variables.return_value = OperationSuccess(
            VariablesInfo(thread_id=3, frame=1, variables=[])
        )
        manager = _manager_for(session)

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

    def test_inspect_query_source_routes_file_range(self) -> None:
        """Inspect query source should route file-range selectors."""

        session = _session_double()
        session.get_source_context.return_value = OperationSuccess(
            SourceContextInfo(
                context=InspectionContextInfo(thread_id=None, frame=None),
                location=InspectionLocation(kind="file_range", file="main.c"),
                range=SourceRange(start_line=10, end_line=12),
                lines=[],
            )
        )
        manager = _manager_for(session)

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

    def test_breakpoint_query_get_routes_to_service(self) -> None:
        """Breakpoint query get should route through the breakpoint service."""

        session = _session_double()
        session.get_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint=breakpoint_record({"number": "4", "type": "breakpoint"}))
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_breakpoint_query",
            {"session_id": 3, "action": "get", "query": {"number": 4}},
            manager,
        )

        session.get_breakpoint.assert_called_once_with(4)

    def test_breakpoint_query_list_routes_enabled_filter(self) -> None:
        """Breakpoint query list should apply the enabled-state filter after listing."""

        session = _session_double()
        session.list_breakpoints.return_value = OperationSuccess(
            BreakpointListInfo(
                breakpoints=[
                    breakpoint_record({"number": "1", "type": "breakpoint", "enabled": "y"}),
                    breakpoint_record({"number": "2", "type": "watchpoint", "enabled": "n"}),
                ],
            )
        )
        manager = _manager_for(session)

        result_data = dispatch(
            "gdb_breakpoint_query",
            {
                "session_id": 3,
                "action": "list",
                "query": {"enabled": True},
            },
            manager,
        )

        session.list_breakpoints.assert_called_once_with()
        assert result_data["action"] == "list"
        assert len(mapping_list(dig(result_data, "result", "breakpoints"))) == 1
        assert mapping_list(dig(result_data, "result", "breakpoints"))[0]["number"] == "1"

    def test_breakpoint_manage_update_routes_changes(self) -> None:
        """Breakpoint manage update should route condition changes."""

        session = _session_double()
        session.update_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(
                breakpoint=breakpoint_record(
                    {"number": "4", "type": "breakpoint", "exp": "count > 100"}
                )
            )
        )
        manager = _manager_for(session)

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

    def test_inferior_query_current_returns_current_inferior(self) -> None:
        """Inferior query current should unwrap the selected inferior from inventory."""

        session = _session_double()
        session.list_inferiors.return_value = OperationSuccess(
            InferiorListInfo(
                inferiors=[
                    InferiorRecord.from_payload({"inferior_id": 1, "display": "i1"}),
                    InferiorRecord.from_payload({"inferior_id": 2, "display": "i2"}),
                ],
                current_inferior_id=1,
            )
        )
        manager = _manager_for(session)

        result_data = dispatch(
            "gdb_inferior_query",
            {"session_id": 8, "action": "current", "query": {}},
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["action"] == "current"
        assert dig(result_data, "result", "inferior", "inferior_id") == 1

    def test_inferior_manage_select_routes_to_service(self) -> None:
        """Inferior manage select should route through the session service."""

        session = _session_double()
        session.select_inferior.return_value = OperationSuccess(
            InferiorSelectionInfo(inferior_id=2)
        )
        manager = _manager_for(session)

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

    def test_set_watchpoint_routes_to_correct_session(self) -> None:
        """Watchpoint requests should forward expression and access mode."""

        session = _session_double()
        session.set_watchpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint=breakpoint_record({"number": "2", "type": "hw watchpoint"}))
        )
        manager = _manager_for(session)

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

    def test_delete_watchpoint_routes_to_correct_session(self) -> None:
        """Watchpoint deletion should forward the shared breakpoint number."""

        session = _session_double()
        session.delete_breakpoint.return_value = OperationSuccess(
            SessionMessage(message="Breakpoint 2 deleted")
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 3, "action": "delete", "breakpoint": {"number": 2}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(3)
        session.delete_breakpoint.assert_called_once_with(number=2)

    def test_set_catchpoint_routes_to_correct_session(self) -> None:
        """Catchpoint requests should forward kind, argument, and temporary flag."""

        session = _session_double()
        session.set_catchpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint=breakpoint_record({"number": "3", "type": "catchpoint"}))
        )
        manager = _manager_for(session)

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

    def test_wait_for_stop_routes_to_correct_session(self) -> None:
        """Wait requests should forward timeout and optional reason filters."""

        session = _session_double()
        session.wait_for_stop.return_value = OperationSuccess({"matched": True})
        manager = _manager_for(session)

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

    def test_read_memory_routes_to_correct_session(self) -> None:
        """Memory read requests should forward address, count, and offset."""

        session = _session_double()
        session.read_memory.return_value = OperationSuccess({"captured_bytes": 4})
        manager = _manager_for(session)

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

    def test_list_inferiors_routes_to_correct_session(self) -> None:
        """Inferior inventory requests should route to the resolved session."""

        session = _session_double()
        session.list_inferiors.return_value = OperationSuccess({"inferiors": []})
        manager = _manager_for(session)

        dispatch(
            "gdb_inferior_query",
            {"session_id": 4, "action": "list", "query": {}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.list_inferiors.assert_called_once_with()

    def test_select_inferior_routes_to_correct_session(self) -> None:
        """Inferior selection should forward the requested inferior ID."""

        session = _session_double()
        session.select_inferior.return_value = OperationSuccess({"inferior_id": 2})
        manager = _manager_for(session)

        dispatch(
            "gdb_inferior_manage",
            {"session_id": 4, "action": "select", "inferior": {"inferior_id": 2}},
            manager,
        )

        manager.resolve_session.assert_called_once_with(4)
        session.select_inferior.assert_called_once_with(inferior_id=2)

    def test_set_follow_fork_mode_routes_to_correct_session(self) -> None:
        """Follow-fork-mode requests should forward the selected mode."""

        session = _session_double()
        session.set_follow_fork_mode.return_value = OperationSuccess({"mode": "child"})
        manager = _manager_for(session)

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

    def test_set_detach_on_fork_routes_to_correct_session(self) -> None:
        """Detach-on-fork requests should forward the selected boolean value."""

        session = _session_double()
        session.set_detach_on_fork.return_value = OperationSuccess({"enabled": False})
        manager = _manager_for(session)

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

    def test_set_breakpoint_routes_to_correct_session(self) -> None:
        """Breakpoint requests should be routed to the resolved session."""

        session = _session_double()
        session.set_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint=breakpoint_record({"number": "1", "location": "main"}))
        )
        manager = _manager_for(session)

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

    def test_workflow_batch_routes_validated_steps_and_captures_stop_event(self) -> None:
        """Workflow batch requests should execute validated session steps atomically."""

        session: SessionService = create_default_session_service()
        set_breakpoint = _stub(
            session,
            "set_breakpoint",
            return_value=OperationSuccess(
                BreakpointInfo(
                    breakpoint=breakpoint_record({"number": "1", "original-location": "main"})
                )
            ),
        )
        continue_execution = _stub(
            session, "continue_execution", side_effect=_continue_side_effect(session)
        )
        manager = _manager_for(session)

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
        assert dig(result_data, "result", "requested_steps") == 2
        assert len(mapping_list(dig(result_data, "result", "steps"))) == 2
        steps = mapping_list(dig(result_data, "result", "steps"))
        assert steps[0]["tool"] == "gdb_breakpoint_manage"
        assert mapping(steps[0]["result"])["action"] == "create"
        assert steps[0]["label"] == "break main"
        assert mapping(steps[0]["result"])["status"] == "success"
        assert steps[1]["tool"] == "gdb_execution_manage"
        assert mapping(steps[1]["result"])["action"] == "continue"
        assert mapping(steps[1]["stop_event"])["reason"] == "breakpoint-hit"
        assert dig(result_data, "result", "last_stop_event", "reason") == "breakpoint-hit"
        set_breakpoint.assert_called_once_with(
            location="main",
            condition=None,
            temporary=False,
        )
        continue_execution.assert_called_once_with(
            wait_for_stop=True, timeout_sec=30, reverse=False
        )

    def test_workflow_batch_stops_on_first_error_by_default(self) -> None:
        """Fail-fast workflow batches should stop before executing later steps."""

        session: SessionService = create_default_session_service()
        execute_command = _stub(
            session,
            "execute_command",
            return_value=OperationError(message="boom", details={"command": "info threads"}),
        )
        get_status = _stub(
            session,
            "get_status",
            return_value=OperationSuccess(
                SessionStatusSnapshot(
                    debugger_alive=False, target_loaded=False, session_state=SessionState.CREATED
                )
            ),
        )
        manager = _manager_for(session)

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
        assert dig(result_data, "result", "requested_steps") == 2
        steps = mapping_list(dig(result_data, "result", "steps"))
        assert len(steps) == 1
        assert mapping(steps[0]["result"])["status"] == "error"
        execute_command.assert_called_once_with(command="info threads", timeout_sec=30)
        get_status.assert_not_called()

    def test_workflow_batch_accepts_string_step_shorthand(self) -> None:
        """Workflow batch requests should allow shorthand step strings."""

        session: SessionService = create_default_session_service()
        capture_bundle = _stub(
            session,
            "capture_bundle",
            return_value=OperationSuccess(SessionMessage(message="bundle written")),
        )
        manager = _manager_for(session)

        result_data = dispatch(
            "gdb_workflow_batch",
            {
                "session_id": 9,
                "steps": ["gdb_capture_bundle"],
            },
            manager,
        )

        assert result_data["status"] == "success"
        assert len(mapping_list(dig(result_data, "result", "steps"))) == 1
        assert mapping_list(dig(result_data, "result", "steps"))[0]["tool"] == "gdb_capture_bundle"
        capture_bundle.assert_called_once()

    def test_session_tool_dispatch_uses_workflow_lock(self) -> None:
        """Session-scoped tools should serialize through the workflow lock."""

        session = _session_double()
        workflow_lock = _workflow_lock()
        session.runtime.workflow_lock = workflow_lock
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=False, target_loaded=False, session_state=SessionState.READY
            )
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "query": {}},
            manager,
        )

        workflow_lock.__enter__.assert_called_once()
        workflow_lock.__exit__.assert_called_once()

    def test_workflow_batch_rejects_step_level_session_id(self) -> None:
        """Workflow batch steps should inherit session_id from the batch envelope only."""

        session = create_default_session_service()
        manager = _manager_for(session)

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
        assert "must not include session_id" in text(result_data["message"])

    def test_workflow_batch_rejects_session_query_list_step(self) -> None:
        """Workflow batch should reject the global session inventory action inside steps."""

        session = create_default_session_service()
        manager = _manager_for(session)

        result_data = dispatch(
            "gdb_workflow_batch",
            {
                "session_id": 3,
                "steps": [
                    {
                        "tool": "gdb_session_query",
                        "arguments": {"action": "list", "query": {}},
                    }
                ],
            },
            manager,
        )

        assert result_data["status"] == "error"
        assert "gdb_session_query(action=list) is not valid inside gdb_workflow_batch" in text(
            result_data["message"]
        )

    def test_capture_bundle_routes_to_session(self) -> None:
        """Capture requests should forward the bundle options to the resolved session."""

        session = _session_double()
        session.capture_bundle.return_value = OperationSuccess(
            SessionMessage(message="bundle written")
        )
        manager = _manager_for(session)

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

    def test_capture_bundle_accepts_memory_range_shorthand(self) -> None:
        """Capture requests should parse shorthand memory-range strings."""

        session = _session_double()
        session.capture_bundle.return_value = OperationSuccess(
            SessionMessage(message="bundle written")
        )
        manager = _manager_for(session)

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

    def test_run_until_failure_capture_forwards_memory_ranges(self) -> None:
        """Campaign capture settings should forward explicit memory ranges to bundle capture."""

        manager = Mock(spec=SessionRegistry)
        session = _failure_session(manager)

        dispatch(
            "gdb_run_until_failure",
            {
                "startup": {"program": "/tmp/a.out"},
                "capture": {
                    "enabled": True,
                    "memory_ranges": [{"address": "&value", "count": 8, "name": "value-bytes"}],
                },
            },
            manager,
        )

        session.capture_bundle.assert_called_once()
        assert session.capture_bundle.call_args.kwargs["memory_ranges"] == [
            MemoryCaptureRange(address="&value", count=8, offset=0, name="value-bytes")
        ]

    def test_run_until_failure_capture_accepts_exact_bundle_name(self) -> None:
        """Campaign capture should accept bundle_name alias for deterministic naming."""

        manager = Mock(spec=SessionRegistry)
        session = _failure_session(manager)

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

    def test_run_until_failure_uses_untracked_sessions(self) -> None:
        """Campaign requests should create fresh untracked sessions and return campaign data."""

        manager = Mock(spec=SessionRegistry)
        session = _failure_session(manager)

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
            debugger_path=None,
            working_dir=None,
            core=None,
        )
        session.run.assert_called_once_with(args=None, timeout_sec=30, wait_for_stop=True)
        assert result_data["status"] == "success"
        iteration = mapping_list(dig(result_data, "result", "iterations"))[-1]
        assert iteration["matched_failure"] is True
        assert iteration["iteration"] == 1
        assert iteration["trigger"] == "stop_reason:signal-received"
        assert dig(result_data, "result", "capture_bundle") is None

    def test_multiple_tools_use_different_sessions(self) -> None:
        """Separate session IDs should be routed independently."""

        manager = Mock(spec=SessionRegistry)
        session_1 = _session_double()
        session_1.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=False, target_loaded=False, session_state=SessionState.READY
            )
        )
        session_2 = _session_double()
        session_2.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=True, target_loaded=True, session_state=SessionState.READY
            )
        )

        def resolve_session_side_effect(session_id: int) -> Mock | OperationError:
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
        assert dig(result_1, "result", "debugger_alive") is False
        assert dig(result_2, "result", "debugger_alive") is True

    @pytest.mark.parametrize(("thread_id", "frame"), [(2, 1), ("2", "1")])
    def test_evaluate_expression_routes_thread_and_frame_overrides(
        self, thread_id: int | str, frame: int | str
    ) -> None:
        """Expression requests should forward numeric and numeric-string overrides."""

        session = _session_double()
        session.evaluate_expression.return_value = OperationSuccess(
            _command_result("-data-evaluate-expression")
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "evaluate",
                "query": {"expression": "x", "context": {"thread_id": thread_id, "frame": frame}},
            },
            manager,
        )

        session.evaluate_expression.assert_called_once_with("x", thread_id=2, frame=1)

    @pytest.mark.parametrize(("thread_id", "frame"), [(2, 3), ("2", "3")])
    def test_get_registers_routes_thread_and_frame_overrides(
        self, thread_id: int | str, frame: int | str
    ) -> None:
        """Register requests should forward numeric and numeric-string overrides."""

        session = _session_double()
        session.get_registers.return_value = OperationSuccess(
            _command_result("-data-list-register-values x")
        )
        manager = _manager_for(session)

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 1,
                "action": "registers",
                "query": {"context": {"thread_id": thread_id, "frame": frame}},
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

    def test_get_registers_routes_filter_and_format_options(self) -> None:
        """Register requests should forward selector and rendering options."""

        session = _session_double()
        session.get_registers.return_value = OperationSuccess(
            _command_result("-data-list-register-values N")
        )
        manager = _manager_for(session)

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

    def test_get_backtrace_accepts_numeric_string_thread_id(self) -> None:
        """Backtrace requests should accept numeric-string thread IDs."""

        session = _session_double()
        session.get_backtrace.return_value = OperationSuccess(_command_result("-stack-list-frames"))
        manager = _manager_for(session)

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

    def test_disassemble_routes_normalized_selectors(self) -> None:
        """Disassembly requests should forward normalized selector values."""

        session = _session_double()
        session.disassemble.return_value = OperationSuccess({"instructions": []})
        manager = _manager_for(session)

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

    def test_get_variables_accepts_numeric_string_context(self) -> None:
        """Variable requests should accept numeric-string thread/frame selectors."""

        session = _session_double()
        session.get_variables.return_value = OperationSuccess({"variables": []})
        manager = _manager_for(session)

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

    def test_get_source_context_routes_normalized_selectors(self) -> None:
        """Source-context requests should forward normalized selector values."""

        session = _session_double()
        session.get_source_context.return_value = OperationSuccess({"lines": []})
        manager = _manager_for(session)

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

    def test_tool_definitions_match_dispatch_registry(self) -> None:
        """Every exported tool should have a matching dispatch path and vice versa."""

        exported_tools = {tool.name for tool in build_tool_definitions()}
        dispatched_tools = set(TOOL_SPECS)

        assert exported_tools == dispatched_tools
