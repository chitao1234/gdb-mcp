"""Lifecycle-focused session API tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, Mock, patch

from gdb_mcp.domain import OperationError, result_to_mapping


class TestLifecycleApi:
    """Test session lifecycle behavior through the public API."""

    def test_get_status_no_session(self, session_service):
        """Status should reflect an idle session before startup."""

        status = result_to_mapping(session_service.get_status())

        assert status["is_running"] is False
        assert status["target_loaded"] is False
        assert status["has_controller"] is False

    def test_stop_no_session(self, session_service):
        """Stopping without a controller should return a user-facing error."""

        result = result_to_mapping(session_service.stop())

        assert result["status"] == "error"
        assert "No active session" in result["message"]

    def test_start_session_already_running(self, session_service):
        """Starting twice should be rejected before touching transport."""

        session_service.runtime.controller = Mock()

        result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "already running" in result["message"].lower()

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_basic(self, mock_controller_class, session_service, prompt_response):
        """Basic startup should attach a controller and expose program info."""

        mock_controller_class.return_value = MagicMock()

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {"type": "console", "payload": "Reading symbols from /bin/ls...\n"},
                    {"type": "result", "message": "done", "token": 1000},
                ]
            ),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert result["program"] == "/bin/ls"
        assert session_service.is_running is True

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_with_custom_gdb_path(
        self,
        mock_controller_class,
        session_service,
        prompt_response,
    ):
        """Explicit gdb_path should be forwarded into process startup."""

        mock_controller_class.return_value = MagicMock()

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[{"type": "result", "message": "done", "token": 1000}]
            ),
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
    @patch.dict(os.environ, {"GDB_PATH": "/custom/path/to/gdb"})
    def test_start_session_with_gdb_path_env_var(
        self,
        mock_controller_class,
        session_service,
        prompt_response,
    ):
        """GDB_PATH should be used when gdb_path is not provided."""

        mock_controller_class.return_value = MagicMock()

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[{"type": "result", "message": "done", "token": 1000}]
            ),
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
        mock_controller_class,
        session_service,
        prompt_response,
    ):
        """An explicit gdb_path should override GDB_PATH."""

        mock_controller_class.return_value = MagicMock()

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[{"type": "result", "message": "done", "token": 1000}]
            ),
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
        mock_controller_class,
        session_service,
        prompt_response,
        command_result,
    ):
        """Environment variables should be translated into init commands."""

        del mock_controller_class
        env_commands: list[str] = []

        def mock_execute(command, **kwargs):
            del kwargs
            if "set environment" in command:
                env_commands.append(command)
            return command_result(command, output="")

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[{"type": "result", "message": "done", "token": 1000}]
            ),
        ):
            with patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
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
    def test_start_session_fails_when_env_setup_fails(
        self,
        mock_controller_class,
        session_service,
        prompt_response,
        command_result,
    ):
        """Environment setup failures should fail startup instead of reporting READY."""

        del mock_controller_class

        def mock_execute(command, **kwargs):
            del kwargs
            if command.startswith("set environment "):
                return OperationError(message="Permission denied for environment setup")
            return command_result(command, output="")

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[{"type": "result", "message": "done", "token": 1000}]
            ),
        ):
            with patch.object(
                session_service._command_runner, "execute_command_result", side_effect=mock_execute
            ):
                result = result_to_mapping(
                    session_service.start(program="/bin/ls", env={"DEBUG_MODE": "1"})
                )

        assert result["status"] == "error"
        assert "environment" in result["message"].lower()

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_detects_missing_debug_symbols(
        self,
        mock_controller_class,
        session_service,
        prompt_response,
    ):
        """Startup warnings should surface missing debug symbols."""

        mock_controller_class.return_value = MagicMock()

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {"type": "console", "payload": "Reading symbols from /bin/ls...\n"},
                    {"type": "console", "payload": "(no debugging symbols found)...done.\n"},
                    {"type": "result", "message": "done", "token": 1000},
                ]
            ),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "success"
        assert "warnings" in result
        assert any("not compiled with -g" in warning for warning in result["warnings"])

    def test_stop_active_session(self, running_session):
        """Stopping an active session should clear the controller and running flag."""

        result = result_to_mapping(running_session.stop())

        assert result["status"] == "success"
        assert running_session.controller is None
        assert running_session.is_running is False
