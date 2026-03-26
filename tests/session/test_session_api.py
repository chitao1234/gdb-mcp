"""Unit tests for the session-layer debugger API."""

import os
from unittest.mock import Mock, patch, MagicMock
from gdb_mcp.domain import CommandExecutionInfo, OperationSuccess, result_to_mapping
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.transport.mi_parser import extract_mi_result_payload, parse_mi_responses


def command_result(
    command: str,
    *,
    result: dict[str, object] | None = None,
    output: str | None = None,
) -> OperationSuccess[CommandExecutionInfo]:
    """Build a typed command-execution result for session-layer mocks."""

    return OperationSuccess(CommandExecutionInfo(command=command, result=result, output=output))


class TestSessionApi:
    """Test cases for the direct session API."""

    def test_session_initialization(self):
        """Test that SessionService initializes correctly."""
        session = create_default_session_service()
        assert session.controller is None
        assert session.is_running is False
        assert session.target_loaded is False

    def test_get_status_no_session(self):
        """Test get_status when no session is running."""
        session = create_default_session_service()
        status = result_to_mapping(session.get_status())
        assert status["is_running"] is False
        assert status["target_loaded"] is False
        assert status["has_controller"] is False

    def test_stop_no_session(self):
        """Test stopping when no session exists."""
        session = create_default_session_service()
        result = result_to_mapping(session.stop())
        assert result["status"] == "error"
        assert "No active session" in result["message"]

    def test_execute_command_no_session(self):
        """Test execute_command when no session is running."""
        session = create_default_session_service()
        result = result_to_mapping(session.execute_command("info threads"))
        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    def test_response_parsing(self):
        """Test parsing raw MI responses into a normalized structure."""

        # Mock responses from GDB
        responses = [
            {"type": "console", "payload": "Test output\n"},
            {"type": "result", "message": "done", "payload": {"msg": "done"}},
            {"type": "notify", "payload": {"msg": "thread-created"}},
        ]

        parsed = parse_mi_responses(responses)

        assert "Test output\n" in parsed.console
        assert parsed.result == {"msg": "done"}
        assert parsed.result_class == "done"
        assert {"msg": "thread-created"} in parsed.notify

    def test_extract_mi_result_payload(self):
        """Test extracting the inner MI result payload from command results."""

        payload = extract_mi_result_payload(
            {
                "status": "success",
                "result": {
                    "result": {"threads": []},
                    "result_class": "done",
                },
            }
        )

        assert payload == {"threads": []}

    def test_parsed_mi_response_extracts_error_message(self):
        """MI error results should expose their message for higher layers."""

        parsed = parse_mi_responses(
            [
                {
                    "type": "result",
                    "message": "error",
                    "payload": {"msg": "Thread ID 999 not known"},
                }
            ]
        )

        assert parsed.is_error_result() is True
        assert parsed.error_message() == "Thread ID 999 not known"

    def test_cli_command_wrapping(self):
        """Test that CLI commands are properly detected."""
        session = create_default_session_service()

        # CLI commands don't start with '-'
        assert not "info threads".startswith("-")
        assert not "print x".startswith("-")

        # MI commands start with '-'
        assert "-break-list".startswith("-")
        assert "-exec-run".startswith("-")


class TestSessionApiWithMock:
    """Test cases that mock the GdbController."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_already_running(self, mock_controller_class):
        """Test starting a session when one is already running."""
        session = create_default_session_service()

        # Manually set controller to simulate running session
        session.controller = Mock()

        result = result_to_mapping(session.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "already running" in result["message"].lower()

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_basic(self, mock_controller_class):
        """Test basic session start."""
        # Create a mock controller instance
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Mock the initialization check
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {"type": "console", "payload": "Reading symbols from /bin/ls...\n"},
                    {"type": "result", "message": "done", "token": 1000},
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert result["program"] == "/bin/ls"
        assert session.is_running is True

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_custom_gdb_path(self, mock_controller_class):
        """Test session start with custom GDB path."""
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Mock the initialization check
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.start(program="/bin/ls", gdb_path="/usr/local/bin/gdb-custom"))

        # Verify GdbController was called with correct command
        call_args = mock_controller_class.call_args
        command = call_args[1]["command"]

        assert command[0] == "/usr/local/bin/gdb-custom"
        assert "--interpreter=mi" in command
        assert result["status"] == "success"

    @patch("gdb_mcp.session.factory.GdbController")
    @patch.dict(os.environ, {"GDB_PATH": "/custom/path/to/gdb"})
    def test_start_session_with_gdb_path_env_var(self, mock_controller_class):
        """Test session start uses GDB_PATH environment variable when gdb_path is not specified."""
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Mock the initialization check
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            # Don't specify gdb_path - should use environment variable
            result = result_to_mapping(session.start(program="/bin/ls"))

        # Verify GdbController was called with GDB_PATH from environment
        call_args = mock_controller_class.call_args
        command = call_args[1]["command"]

        assert command[0] == "/custom/path/to/gdb"
        assert "--interpreter=mi" in command
        assert result["status"] == "success"

    @patch("gdb_mcp.session.factory.GdbController")
    @patch.dict(os.environ, {"GDB_PATH": "/env/gdb"})
    def test_start_session_explicit_path_overrides_env_var(self, mock_controller_class):
        """Test that explicit gdb_path parameter overrides GDB_PATH environment variable."""
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Mock the initialization check
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            # Explicitly specify gdb_path - should override environment variable
            result = result_to_mapping(session.start(program="/bin/ls", gdb_path="/explicit/gdb"))

        # Verify GdbController was called with explicit path, not environment variable
        call_args = mock_controller_class.call_args
        command = call_args[1]["command"]

        assert command[0] == "/explicit/gdb"
        assert command[0] != "/env/gdb"
        assert result["status"] == "success"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_env_variables(self, mock_controller_class):
        """Test session start with environment variables."""
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Track calls to execute_command by patching it
        env_commands = []

        def mock_execute(cmd, **kwargs):
            if "set environment" in cmd:
                env_commands.append(cmd)
            return command_result(cmd, output="")

        # Mock both initialization and execute_command
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            with patch.object(session, "_execute_command_result", side_effect=mock_execute):
                result = result_to_mapping(
                    session.start(program="/bin/ls", env={"DEBUG_MODE": "1", "LOG_LEVEL": "verbose"})
                )

        # Verify environment commands were executed
        assert len(env_commands) == 2
        assert any("DEBUG_MODE" in cmd for cmd in env_commands)
        assert any("LOG_LEVEL" in cmd for cmd in env_commands)

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_detects_missing_debug_symbols(self, mock_controller_class):
        """Test that missing debug symbols are detected."""
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Mock initialization with debug symbol warning
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {"type": "console", "payload": "Reading symbols from /bin/ls...\n"},
                    {"type": "console", "payload": "(no debugging symbols found)...done.\n"},
                    {"type": "result", "message": "done", "token": 1000},
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert "warnings" in result
        assert any("not compiled with -g" in w for w in result["warnings"])


class TestThreadOperations:
    """Test cases for thread inspection methods."""

    def test_get_threads_no_session(self):
        """Test get_threads when no session is running."""
        session = create_default_session_service()
        # Manually set controller to None to simulate no session
        session.controller = None

        result = result_to_mapping(session.get_threads())
        assert result["status"] == "error"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_get_threads_success(self, mock_controller_class):
        """Test successful thread retrieval."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        # Mock execute_command to return thread info
        def mock_execute(cmd, **kwargs):
            return command_result(
                cmd,
                result={
                    "result": {
                        "threads": [
                            {"id": "1", "name": "main"},
                            {"id": "2", "name": "worker-1"},
                        ],
                        "current-thread-id": "1",
                    }
                },
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.get_threads())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert result["current_thread_id"] == "1"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_select_thread(self, mock_controller_class):
        """Test selecting a specific thread."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(
                cmd,
                result={
                    "result": {
                        "new-thread-id": "2",
                        "frame": {"level": "0", "func": "worker_func"},
                    }
                },
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.select_thread(thread_id=2))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["new_thread_id"] == "2"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_get_backtrace_default(self, mock_controller_class):
        """Test backtrace with default parameters."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(
                cmd,
                result={"result": {"stack": [{"level": "0", "func": "main", "file": "test.c"}]}},
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.get_backtrace())

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["thread_id"] is None

    @patch("gdb_mcp.session.factory.GdbController")
    def test_get_backtrace_specific_thread(self, mock_controller_class):
        """Test backtrace for a specific thread."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        commands_executed = []

        def mock_execute(cmd, **kwargs):
            commands_executed.append(cmd)
            if "thread-select" in cmd:
                return command_result(cmd)
            return command_result(cmd, result={"result": {"stack": []}})

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.get_backtrace(thread_id=3))

        assert result["status"] == "success"
        assert any("thread-select 3" in cmd for cmd in commands_executed)


class TestBreakpointOperations:
    """Test cases for breakpoint management."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_set_breakpoint_simple(self, mock_controller_class):
        """Test setting a simple breakpoint."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(
                cmd,
                result={
                    "result": {
                        "bkpt": {
                            "number": "1",
                            "type": "breakpoint",
                            "addr": "0x12345",
                            "func": "main",
                        }
                    }
                },
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.set_breakpoint("main"))

        assert result["status"] == "success"
        assert "breakpoint" in result
        assert result["breakpoint"]["func"] == "main"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_set_breakpoint_with_condition(self, mock_controller_class):
        """Test setting a conditional breakpoint."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        commands_executed = []

        def mock_execute(cmd, **kwargs):
            commands_executed.append(cmd)
            return command_result(cmd, result={"result": {"bkpt": {"number": "1"}}})

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.set_breakpoint("foo.c:42", condition="x > 10", temporary=True))

        assert result["status"] == "success"
        # Verify the command includes condition and temporary flags
        assert any("-break-insert" in cmd for cmd in commands_executed)

    @patch("gdb_mcp.session.factory.GdbController")
    def test_list_breakpoints(self, mock_controller_class):
        """Test listing breakpoints."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(
                cmd,
                result={
                    "result": {
                        "BreakpointTable": {
                            "body": [
                                {"number": "1", "type": "breakpoint"},
                                {"number": "2", "type": "breakpoint"},
                            ]
                        }
                    }
                },
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.list_breakpoints())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert len(result["breakpoints"]) == 2


class TestExecutionControl:
    """Test cases for execution control methods."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_continue_execution(self, mock_controller_class):
        """Test continue execution."""
        session = create_default_session_service()
        session.controller = MagicMock()  # Just need a controller object
        session.is_running = True

        # Mock execute_command since continue_execution now just calls it
        with patch.object(
            session,
            "_execute_command_result",
            return_value=command_result(
                "-exec-continue",
                result={"notify": [{"reason": "breakpoint-hit"}]},
            ),
        ) as mock_execute:
            result = result_to_mapping(session.continue_execution())

        assert result["status"] == "success"
        mock_execute.assert_called_once_with("-exec-continue")

    @patch("gdb_mcp.session.factory.GdbController")
    def test_step(self, mock_controller_class):
        """Test step into."""
        session = create_default_session_service()
        session.controller = MagicMock()  # Just need a controller object
        session.is_running = True

        # Mock execute_command since step now just calls it
        with patch.object(
            session,
            "_execute_command_result",
            return_value=command_result(
                "-exec-step",
                result={"notify": [{"reason": "end-stepping-range"}]},
            ),
        ) as mock_execute:
            result = result_to_mapping(session.step())

        assert result["status"] == "success"
        mock_execute.assert_called_once_with("-exec-step")

    @patch("gdb_mcp.session.factory.GdbController")
    def test_next(self, mock_controller_class):
        """Test step over."""
        session = create_default_session_service()
        session.controller = MagicMock()  # Just need a controller object
        session.is_running = True

        # Mock execute_command since next now just calls it
        with patch.object(
            session,
            "_execute_command_result",
            return_value=command_result(
                "-exec-next",
                result={"notify": [{"reason": "end-stepping-range"}]},
            ),
        ) as mock_execute:
            result = result_to_mapping(session.next())

        assert result["status"] == "success"
        mock_execute.assert_called_once_with("-exec-next")

    def test_interrupt_no_controller(self):
        """Test interrupt when no session exists."""
        session = create_default_session_service()
        result = result_to_mapping(session.interrupt())

        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    @patch("gdb_mcp.session.factory.os.kill")
    def test_interrupt_success(self, mock_kill, mock_controller_class):
        """Test successful interrupt with stopped notification."""
        mock_controller = MagicMock()
        mock_controller.gdb_process.pid = 12345
        # Return the *stopped notification in GDB/MI format
        mock_controller.get_gdb_response.return_value = [
            {"type": "notify", "message": "stopped", "payload": {"reason": "signal-received"}}
        ]

        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        result = result_to_mapping(session.interrupt())

        assert result["status"] == "success"
        assert "interrupted" in result["message"].lower()
        mock_kill.assert_called_once()

    @patch("gdb_mcp.session.factory.GdbController")
    @patch("gdb_mcp.session.factory.os.kill")
    def test_interrupt_no_stopped_notification(self, mock_kill, mock_controller_class):
        """Test interrupt when no stopped notification is received."""
        mock_controller = MagicMock()
        mock_controller.gdb_process.pid = 12345
        # Return empty responses (no stopped notification)
        mock_controller.get_gdb_response.return_value = []

        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        result = result_to_mapping(session.interrupt())

        # Should return warning status when no stopped notification received
        assert result["status"] == "warning"
        assert "no stopped notification" in result["message"].lower()
        mock_kill.assert_called_once()


class TestDataInspection:
    """Test cases for data inspection methods."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_evaluate_expression(self, mock_controller_class):
        """Test expression evaluation."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(cmd, result={"result": {"value": "42"}})

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.evaluate_expression("x + y"))

        assert result["status"] == "success"
        assert result["expression"] == "x + y"
        assert result["value"] == "42"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_get_variables(self, mock_controller_class):
        """Test getting local variables."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            if "stack-select-frame" in cmd or "thread-select" in cmd:
                return command_result(cmd)
            return command_result(
                cmd,
                result={
                    "result": {
                        "variables": [
                            {"name": "x", "value": "10"},
                            {"name": "y", "value": "20"},
                        ]
                    }
                },
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.get_variables(thread_id=2, frame=1))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["frame"] == 1
        assert len(result["variables"]) == 2

    @patch("gdb_mcp.session.factory.GdbController")
    def test_get_registers(self, mock_controller_class):
        """Test getting register values."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(
                cmd,
                result={
                    "result": {
                        "register-values": [
                            {"number": "0", "value": "0x1234"},
                            {"number": "1", "value": "0x5678"},
                        ]
                    }
                },
            )

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.get_registers())

        assert result["status"] == "success"
        assert len(result["registers"]) == 2


class TestSessionManagement:
    """Test cases for session management operations."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_stop_active_session(self, mock_controller_class):
        """Test stopping an active session."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        result = result_to_mapping(session.stop())

        assert result["status"] == "success"
        assert session.controller is None
        assert session.is_running is False

    @patch("gdb_mcp.session.factory.GdbController")
    def test_execute_command_cli(self, mock_controller_class):
        """Test executing a CLI command with active session."""
        session = create_default_session_service()
        session.controller = MagicMock()  # Just need a controller object
        session.is_running = True

        # Mock the internal send_command method
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {"type": "console", "payload": "Thread 1 (main)\n"},
                    {"type": "result", "payload": None, "token": 1000},
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.execute_command("info threads"))

        assert result["status"] == "success"
        assert "Thread 1" in result["output"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_execute_command_mi(self, mock_controller_class):
        """Test executing an MI command with active session."""
        session = create_default_session_service()
        session.controller = MagicMock()  # Just need a controller object
        session.is_running = True

        # Mock the internal send_command method
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {"type": "result", "payload": {"threads": []}, "token": 1000},
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.execute_command("-thread-info"))

        assert result["status"] == "success"
        assert "result" in result

    @patch("gdb_mcp.session.factory.GdbController")
    def test_execute_command_mi_error_result(self, mock_controller_class):
        """MI error result records should be surfaced as errors."""

        session = create_default_session_service()
        session.controller = MagicMock()
        session.is_running = True

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {
                        "type": "result",
                        "message": "error",
                        "payload": {"msg": "Thread ID 999 not known"},
                        "token": 1000,
                    }
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.execute_command("-thread-select 999"))

        assert result["status"] == "error"
        assert result["command"] == "-thread-select 999"
        assert "Thread ID 999 not known" in result["message"]


class TestErrorHandling:
    """Test cases for error handling."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_exception(self, mock_controller_class):
        """Test that start handles exceptions gracefully."""
        mock_controller_class.side_effect = Exception("GDB not found")

        session = create_default_session_service()
        result = result_to_mapping(session.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "GDB not found" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_execute_command_exception(self, mock_controller_class):
        """Test that execute_command handles errors."""
        session = create_default_session_service()
        session.controller = MagicMock()
        session.is_running = True

        # Mock _send_command_and_wait_for_prompt to return error
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "error": "Timeout",
                "command_responses": [],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.execute_command("info threads"))

        assert result["status"] == "error"
        assert "Timeout" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_call_function_mi_error_result(self, mock_controller_class):
        """Function-call MI errors should not be reported as success."""

        session = create_default_session_service()
        session.controller = MagicMock()
        session.is_running = True

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {
                        "type": "result",
                        "message": "error",
                        "payload": {"msg": "Cannot evaluate function -- may be inlined"},
                        "token": 1000,
                    }
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.call_function("foo()"))

        assert result["status"] == "error"
        assert result["function_call"] == "foo()"
        assert "Cannot evaluate function" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_set_breakpoint_no_result(self, mock_controller_class):
        """Test set_breakpoint when GDB returns no result."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        def mock_execute(cmd, **kwargs):
            return command_result(cmd, result={"result": None})

        with patch.object(session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(session.set_breakpoint("main"))

        assert result["status"] == "error"
        assert "no result from GDB" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_gdb_internal_fatal_error(self, mock_controller_class):
        """Test that GDB internal fatal errors are detected and session is stopped."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        # Mock _send_command_and_wait_for_prompt to return a fatal error
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "error": "GDB internal fatal error: internal-error: assertion failed",
                "command_responses": [],
                "async_notifications": [],
                "timed_out": False,
                "fatal": True,
            },
        ):
            result = result_to_mapping(session.execute_command("some command"))

        # Verify error is returned with fatal flag
        assert result["status"] == "error"
        assert "internal" in result["message"].lower()
        assert result.get("fatal") is True

    @patch("gdb_mcp.session.factory.GdbController")
    def test_gdb_fatal_error_message_format(self, mock_controller_class):
        """Test detection of 'A fatal error internal to GDB' message format."""
        mock_controller = MagicMock()
        session = create_default_session_service()
        session.controller = mock_controller
        session.is_running = True

        # Mock _send_command_and_wait_for_prompt to return the actual GDB fatal error message
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "error": "GDB internal fatal error: A fatal error internal to GDB has been detected, further\ndebugging is not possible.  GDB will now terminate.\n",
                "command_responses": [],
                "async_notifications": [],
                "timed_out": False,
                "fatal": True,
            },
        ):
            result = result_to_mapping(session.execute_command("core-file /path/to/core"))

        # Verify error is returned with fatal flag
        assert result["status"] == "error"
        assert "fatal" in result["message"].lower()
        assert result.get("fatal") is True

    @patch("gdb_mcp.session.factory.GdbController")
    def test_fatal_error_during_initialization(self, mock_controller_class):
        """Test that fatal errors during GDB initialization are handled properly."""
        mock_controller = MagicMock()
        mock_controller_class.return_value = mock_controller

        session = create_default_session_service()

        # Mock initialization check to return fatal error
        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "error": "GDB internal fatal error: internal-error during initialization",
                "command_responses": [],
                "async_notifications": [],
                "timed_out": False,
                "fatal": True,
            },
        ):
            result = result_to_mapping(session.start(program="/bin/ls"))

        # Verify startup failed with fatal error
        assert result["status"] == "error"
        assert "failed to initialize" in result["message"].lower()
        assert result.get("fatal") is True
        # Session should be cleaned up
        assert session.controller is None


class TestCallFunction:
    """Test cases for the call_function method."""

    def test_call_function_no_session(self):
        """Test call_function when no session is running."""
        session = create_default_session_service()
        result = result_to_mapping(session.call_function('printf("hello")'))
        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_call_function_success(self, mock_controller_class):
        """Test successful function call execution."""
        session = create_default_session_service()
        session.controller = MagicMock()
        session.is_running = True

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [
                    {"type": "console", "payload": "$1 = 5\n"},
                    {"type": "result", "payload": None, "token": 1000},
                ],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.call_function('strlen("hello")'))

        assert result["status"] == "success"
        assert result["function_call"] == 'strlen("hello")'
        assert "$1 = 5" in result["result"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_call_function_timeout(self, mock_controller_class):
        """Test call_function when command times out."""
        session = create_default_session_service()
        session.controller = MagicMock()
        session.is_running = True

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [],
                "async_notifications": [],
                "timed_out": True,
            },
        ):
            result = result_to_mapping(session.call_function("some_slow_function()"))

        assert result["status"] == "error"
        assert "Timeout" in result["message"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_call_function_error(self, mock_controller_class):
        """Test call_function when there's an error."""
        session = create_default_session_service()
        session.controller = MagicMock()
        session.is_running = True

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "error": "No symbol table loaded",
                "command_responses": [],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(session.call_function("unknown_func()"))

        assert result["status"] == "error"
        assert "No symbol table" in result["message"]
