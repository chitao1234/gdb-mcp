"""Lifecycle-focused session API tests."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock, Mock, patch

from gdb_mcp.domain import (
    CommandExecutionInfo,
    OperationError,
    OperationResult,
    OperationSuccess,
    result_to_mapping,
)
from gdb_mcp.session.service import SessionService
from tests.support import sequence, text

PromptResponseBuilder = Callable[..., dict[str, object]]
CommandResultBuilder = Callable[..., OperationSuccess[CommandExecutionInfo]]


class TestLifecycleApi:
    """Test session lifecycle behavior through the public API."""

    def test_get_status_no_session(self, session_service: SessionService) -> None:
        """Status should reflect an idle session before startup."""

        status = result_to_mapping(session_service.get_status())

        assert status["is_running"] is False
        assert status["target_loaded"] is False
        assert status["has_controller"] is False
        assert status["execution_state"] == "unknown"
        assert status["stop_reason"] is None
        assert status["exit_code"] is None

    def test_get_status_marks_dead_gdb_process_inactive(
        self, running_session: SessionService
    ) -> None:
        """Status should reconcile against a dead GDB child process."""

        with patch.object(running_session._command_runner, "is_gdb_alive", return_value=False):
            status = result_to_mapping(running_session.get_status())

        assert status["status"] == "success"
        assert status["is_running"] is False
        assert status["target_loaded"] is False
        assert status["has_controller"] is False
        assert status["execution_state"] == "unknown"
        assert running_session.controller is None
        assert running_session.state.value == "failed"

    def test_stop_no_session(self, session_service: SessionService) -> None:
        """Stopping without a controller should return a user-facing error."""

        result = result_to_mapping(session_service.stop())

        assert result["status"] == "error"
        assert "No active session" in text(result["message"])

    def test_start_session_already_running(self, session_service: SessionService) -> None:
        """Starting twice should be rejected before touching transport."""

        session_service.runtime.controller = Mock()

        result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "already running" in text(result["message"]).lower()

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_basic(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """Basic startup should attach a controller and expose program info."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[
                        {"type": "console", "payload": "Reading symbols from /bin/ls...\n"},
                        {"type": "result", "message": "done", "token": 1000},
                    ]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert result["program"] == "/bin/ls"
        assert result["target_loaded"] is True
        assert result["execution_state"] == "not_started"
        assert session_service.is_running is True

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_custom_gdb_path(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """Explicit gdb_path should be forwarded into process startup."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(program="/bin/ls", gdb_path="/usr/local/bin/gdb-custom")
            )

        call_args = mock_controller_class.call_args
        command = call_args[1]["command"]

        assert command[0] == "/usr/local/bin/gdb-custom"
        assert "--interpreter=mi" in command
        assert result["status"] == "success"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_program_and_core_uses_explicit_exec_and_core_flags(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """Core-dump startup should use explicit GDB options instead of argv passthrough."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(program="/bin/ls", core="/tmp/core.123")
            )

        command = mock_controller_class.call_args[1]["command"]

        assert result["status"] == "success"
        assert result["execution_state"] == "paused"
        assert "--se=/bin/ls" in command
        assert "--core=/tmp/core.123" in command
        assert "--args" not in command

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_rr_replay_spawns_rr(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """An rr replay should start 'rr replay --interpreter=mi' on the trace."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(session_service.start(rr_replay="/traces/app-0"))

        command = mock_controller_class.call_args[1]["command"]

        assert result["status"] == "success"
        assert result["rr_replay"] == "/traces/app-0"
        assert command[:3] == ["rr", "replay", "--interpreter=mi"]
        assert command[-1] == "/traces/app-0"
        assert "-d" in command

    @patch("gdb_mcp.session.factory.GdbController")
    @patch.dict(os.environ, {"RR_PATH": "/opt/rr/bin/rr"})
    def test_start_session_with_rr_replay_honors_rr_path(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """RR_PATH should choose the rr binary, like GDB_PATH chooses gdb."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            session_service.start(rr_replay="/traces/app-0")

        command = mock_controller_class.call_args[1]["command"]
        assert command[0] == "/opt/rr/bin/rr"

    def test_start_session_rejects_rr_replay_with_program(
        self, session_service: SessionService
    ) -> None:
        """An rr trace owns the target, so a program cannot be loaded as well."""

        result = result_to_mapping(
            session_service.start(rr_replay="/traces/app-0", program="/bin/ls")
        )

        assert result["status"] == "error"
        assert "rr trace already owns the target" in text(result["message"]).lower()
        assert session_service.state.value == "failed"

    @patch("gdb_mcp.session.factory.GdbController")
    @patch.dict(os.environ, {"GDB_PATH": "/custom/path/to/gdb"})
    def test_start_session_with_gdb_path_env_var(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """GDB_PATH should be used when gdb_path is not provided."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        call_args = mock_controller_class.call_args
        command = call_args[1]["command"]

        assert command[0] == "/custom/path/to/gdb"
        assert "--interpreter=mi" in command
        assert result["status"] == "success"

    @patch("gdb_mcp.session.factory.GdbController")
    @patch.dict(os.environ, {"GDB_PATH": "/env/gdb"})
    def test_start_session_explicit_path_overrides_env_var(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """An explicit gdb_path should override GDB_PATH."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(program="/bin/ls", gdb_path="/explicit/gdb")
            )

        call_args = mock_controller_class.call_args
        command = call_args[1]["command"]

        assert command[0] == "/explicit/gdb"
        assert command[0] != "/env/gdb"
        assert result["status"] == "success"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_env_variables(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """Environment variables should be translated into init commands."""

        del mock_controller_class
        env_commands: list[str] = []

        def mock_execute(command: str, **kwargs: object) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            if "set environment" in command:
                env_commands.append(command)
            return command_result(command, output="")

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(
                    program="/bin/ls",
                    env={"DEBUG_MODE": "1", "LOG_LEVEL": "verbose"},
                )
            )

        assert result["status"] == "success"
        assert len(env_commands) == 2
        assert any("DEBUG_MODE" in command for command in env_commands)
        assert any("LOG_LEVEL" in command for command in env_commands)

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_applies_environment_before_init_commands(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """Inferior environment should be set before init commands can run the target."""

        del mock_controller_class
        executed_commands: list[str] = []

        def mock_execute(command: str, **kwargs: object) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            executed_commands.append(command)
            return command_result(command, output="")

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(
                    program="/bin/ls",
                    env={"DEBUG_MODE": "1"},
                    init_commands=["set pagination off", "run"],
                )
            )

        assert result["status"] == "success"
        assert executed_commands == [
            "set confirm off",
            "set environment DEBUG_MODE 1",
            "set pagination off",
            "run",
        ]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_passes_environment_values_verbatim(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """Environment values must reach GDB unchanged, with no extra escape layer."""

        del mock_controller_class
        executed_commands: list[str] = []

        def mock_execute(command: str, **kwargs: object) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            executed_commands.append(command)
            return command_result(command, output="")

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(program="/bin/ls", env={"MSG": 'a\\b"c'})
            )

        assert result["status"] == "success"
        assert 'set environment MSG a\\b"c' in executed_commands

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_rejects_environment_value_with_newline(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """A newline in an environment value would inject a command, so reject it."""

        del mock_controller_class
        executed_commands: list[str] = []

        def mock_execute(command: str, **kwargs: object) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            executed_commands.append(command)
            return command_result(command, output="")

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(program="/bin/ls", env={"MSG": "line1\nline2"})
            )

        assert result["status"] == "error"
        assert "must not contain newlines" in text(result["message"])
        assert not any(command.startswith("set environment") for command in executed_commands)

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_fails_when_env_setup_fails(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """Environment setup failures should fail startup instead of reporting READY."""

        del mock_controller_class

        def mock_execute(command: str, **kwargs: object) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            if command.startswith("set environment "):
                return OperationError(message="Permission denied for environment setup")
            return command_result(command, output="")

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(program="/bin/ls", env={"DEBUG_MODE": "1"})
            )

        assert result["status"] == "error"
        assert "environment" in text(result["message"]).lower()

    def test_start_session_rejects_args_with_core(self, session_service: SessionService) -> None:
        """Core analysis and inferior argv are mutually exclusive at startup."""

        result = result_to_mapping(
            session_service.start(
                program="/bin/ls",
                args=["-l"],
                core="/tmp/core.123",
            )
        )

        assert result["status"] == "error"
        assert "cannot combine 'args' with 'core'" in text(result["message"]).lower()
        assert session_service.state.value == "failed"
        assert session_service.config is not None
        assert session_service.config.args == ("-l",)
        assert session_service.config.core == "/tmp/core.123"

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_detects_missing_debug_symbols(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """Startup warnings should surface missing debug symbols."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[
                        {"type": "console", "payload": "Reading symbols from /bin/ls...\n"},
                        {"type": "console", "payload": "(no debugging symbols found)...done.\n"},
                        {"type": "result", "message": "done", "token": 1000},
                    ]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert "warnings" in result
        assert any(
            "not compiled with -g" in text(warning) for warning in sequence(result["warnings"])
        )

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_missing_program_leaves_target_unloaded(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """Missing startup targets should not report target_loaded=true."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service.runtime.transport,
                "read_initial_output",
                return_value=[
                    {"type": "log", "payload": "/missing/app: No such file or directory.\n"}
                ],
            ),
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=False),
        ):
            result = result_to_mapping(session_service.start(program="/missing/app"))

        status = result_to_mapping(session_service.get_status())

        assert result["status"] == "success"
        assert result["target_loaded"] is False
        assert "warnings" in result
        assert "Program file not found" in [text(item) for item in sequence(result["warnings"])]
        assert status["target_loaded"] is False

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_refreshes_target_loaded_after_init_commands(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """Final startup target state should be refreshed from GDB after init commands."""

        del mock_controller_class
        executed_commands: list[str] = []

        def mock_execute(command: str, **kwargs: object) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            executed_commands.append(command)
            return command_result(command, output="")

        with (
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ),
            patch.object(session_service._lifecycle, "_probe_target_loaded", return_value=True),
        ):
            result = result_to_mapping(
                session_service.start(
                    init_commands=["source setup.gdb"],
                )
            )

        assert result["status"] == "success"
        assert result["target_loaded"] is True
        assert executed_commands == ["set confirm off", "source setup.gdb"]

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_warns_when_target_loaded_refresh_fails(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
        command_result: CommandResultBuilder,
    ) -> None:
        """Startup should keep the last known target_loaded value but surface probe failure."""

        mock_controller_class.return_value = MagicMock()

        with (
            patch.object(
                session_service.runtime.transport,
                "read_initial_output",
                return_value=[{"type": "console", "payload": "Reading symbols from /bin/ls...\n"}],
            ),
            patch.object(
                session_service._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(
                    command_responses=[{"type": "result", "message": "done", "token": 1000}]
                ),
            ),
            patch.object(
                session_service._command_runner,
                "execute_command_result",
                side_effect=[
                    command_result("set confirm off", output=""),
                    OperationError(message="info files failed"),
                ],
            ),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert result["target_loaded"] is True
        assert "warnings" in result
        assert any(
            warning
            == "Could not refresh target_loaded from GDB after startup commands: info files failed"
            for warning in sequence(result["warnings"])
        )

    def test_stop_active_session(self, running_session: SessionService) -> None:
        """Stopping an active session should clear the controller and running flag."""

        result = result_to_mapping(running_session.stop())

        assert result["status"] == "success"
        assert running_session.controller is None
        assert running_session.is_running is False

    def test_stop_failure_marks_session_failed_and_not_running(
        self, running_session: SessionService
    ) -> None:
        """Shutdown failures should not leave the session marked as running."""

        # running_session installs a MagicMock controller (see conftest).
        cast(MagicMock, running_session.controller).exit.side_effect = RuntimeError("exit failed")

        result = result_to_mapping(running_session.stop())

        assert result["status"] == "error"
        assert "exit failed" in text(result["message"])
        assert running_session.controller is None
        assert running_session.is_running is False
        assert running_session.state.value == "failed"
        assert running_session.runtime.last_failure_message is not None
