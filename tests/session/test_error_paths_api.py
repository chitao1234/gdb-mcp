"""Error-path session API tests."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from gdb_mcp.domain import (
    CommandExecutionInfo,
    OperationSuccess,
    result_to_mapping,
)
from gdb_mcp.session.service import SessionService
from tests.support import dig, text

PromptResponseBuilder = Callable[..., dict[str, object]]
CommandResultBuilder = Callable[..., OperationSuccess[CommandExecutionInfo]]


class TestSessionErrorPaths:
    """Test error and fatal-path behavior through the public API."""

    @patch("gdb_mcp.session.factory.GdbController")
    def test_start_session_exception(
        self, mock_controller_class: MagicMock, session_service: SessionService
    ) -> None:
        """Startup exceptions should be surfaced as user-facing errors."""

        mock_controller_class.side_effect = Exception("GDB not found")

        result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "GDB not found" in text(result["message"])

    def test_execute_command_exception(
        self, running_session: SessionService, prompt_response: PromptResponseBuilder
    ) -> None:
        """Transport errors during command execution should surface clearly."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(error="Timeout"),
        ):
            result = result_to_mapping(running_session.execute_command("info threads"))

        assert result["status"] == "error"
        assert "Timeout" in text(result["message"])

    def test_call_function_mi_error_result(
        self, running_session: SessionService, prompt_response: PromptResponseBuilder
    ) -> None:
        """MI error result records should not be flattened into success."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {
                        "type": "result",
                        "message": "error",
                        "payload": {"msg": "Cannot evaluate function -- may be inlined"},
                        "token": 1000,
                    }
                ]
            ),
        ):
            result = result_to_mapping(running_session.call_function("foo()"))

        assert result["status"] == "error"
        assert dig(result, "details", "function_call") == "foo()"
        assert "Cannot evaluate function" in text(result["message"])

    def test_set_breakpoint_no_result(
        self, running_session: SessionService, command_result: CommandResultBuilder
    ) -> None:
        """Breakpoint creation without MI result data should fail clearly."""

        def mock_execute(command: str, **kwargs: object) -> OperationSuccess[CommandExecutionInfo]:
            del kwargs
            return command_result(command, result={"result": None})

        with patch.object(
            running_session._command_runner, "execute_command_result", side_effect=mock_execute
        ):
            result = result_to_mapping(running_session.set_breakpoint("main"))

        assert result["status"] == "error"
        assert "no result from GDB" in text(result["message"])

    def test_gdb_internal_fatal_error(
        self, running_session: SessionService, prompt_response: PromptResponseBuilder
    ) -> None:
        """Fatal transport responses should propagate the fatal flag."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                error="GDB internal fatal error: internal-error: assertion failed",
                fatal=True,
            ),
        ):
            result = result_to_mapping(running_session.execute_command("some command"))

        assert result["status"] == "error"
        assert "internal" in text(result["message"]).lower()
        assert result.get("fatal") is True

    def test_gdb_fatal_error_message_format(
        self, running_session: SessionService, prompt_response: PromptResponseBuilder
    ) -> None:
        """The alternate fatal-error message format should also propagate fatality."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                error=(
                    "GDB internal fatal error: A fatal error internal to GDB has been "
                    "detected, further\ndebugging is not possible.  GDB will now terminate.\n"
                ),
                fatal=True,
            ),
        ):
            result = result_to_mapping(running_session.execute_command("core-file /path/to/core"))

        assert result["status"] == "error"
        assert "fatal" in text(result["message"]).lower()
        assert result.get("fatal") is True

    @patch("gdb_mcp.session.factory.GdbController")
    def test_fatal_error_during_initialization(
        self,
        mock_controller_class: MagicMock,
        session_service: SessionService,
        prompt_response: PromptResponseBuilder,
    ) -> None:
        """Fatal initialization failures should clean up controller state."""

        mock_controller_class.return_value = MagicMock()

        with patch.object(
            session_service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                error="GDB internal fatal error: internal-error during initialization",
                fatal=True,
            ),
        ):
            result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "failed to initialize" in text(result["message"]).lower()
        assert result.get("fatal") is True
        assert session_service.controller is None
