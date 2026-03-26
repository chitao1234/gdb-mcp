"""Execution-focused session API tests."""

from __future__ import annotations

from unittest.mock import patch

from gdb_mcp.domain import result_to_mapping


class TestExecutionApi:
    """Test execution and command-control behavior through the public API."""

    def test_execute_command_no_session(self, session_service):
        """Command execution without an active controller should fail."""

        result = result_to_mapping(session_service.execute_command("info threads"))

        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    def test_continue_execution(self, running_session, command_result):
        """Continue should delegate to the MI exec-continue command."""

        with patch.object(
            running_session,
            "_execute_command_result",
            return_value=command_result(
                "-exec-continue",
                result={"notify": [{"reason": "breakpoint-hit"}]},
            ),
        ) as mock_execute:
            result = result_to_mapping(running_session.continue_execution())

        assert result["status"] == "success"
        mock_execute.assert_called_once_with("-exec-continue")

    def test_step(self, running_session, command_result):
        """Step should delegate to the MI exec-step command."""

        with patch.object(
            running_session,
            "_execute_command_result",
            return_value=command_result(
                "-exec-step",
                result={"notify": [{"reason": "end-stepping-range"}]},
            ),
        ) as mock_execute:
            result = result_to_mapping(running_session.step())

        assert result["status"] == "success"
        mock_execute.assert_called_once_with("-exec-step")

    def test_next(self, running_session, command_result):
        """Next should delegate to the MI exec-next command."""

        with patch.object(
            running_session,
            "_execute_command_result",
            return_value=command_result(
                "-exec-next",
                result={"notify": [{"reason": "end-stepping-range"}]},
            ),
        ) as mock_execute:
            result = result_to_mapping(running_session.next())

        assert result["status"] == "success"
        mock_execute.assert_called_once_with("-exec-next")

    def test_interrupt_no_controller(self, session_service):
        """Interrupt without a controller should return a clear error."""

        result = result_to_mapping(session_service.interrupt())

        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    def test_interrupt_success(self, running_session):
        """Interrupt should report success when a stopped notification arrives."""

        running_session.controller.gdb_process.pid = 12345
        running_session.controller.get_gdb_response.return_value = [
            {"type": "notify", "message": "stopped", "payload": {"reason": "signal-received"}}
        ]

        with patch.object(running_session._os, "kill") as mock_kill:
            result = result_to_mapping(running_session.interrupt())

        assert result["status"] == "success"
        assert "interrupted" in result["message"].lower()
        mock_kill.assert_called_once()

    def test_interrupt_no_stopped_notification(self, running_session):
        """Interrupt should warn when no stopped notification is received."""

        running_session.controller.gdb_process.pid = 12345
        running_session.controller.get_gdb_response.return_value = []

        with patch.object(running_session._os, "kill") as mock_kill:
            result = result_to_mapping(running_session.interrupt())

        assert result["status"] == "warning"
        assert "no stopped notification" in result["message"].lower()
        mock_kill.assert_called_once()

    def test_execute_command_cli(self, running_session, prompt_response):
        """CLI commands should surface console output."""

        with patch.object(
            running_session,
            "_send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {"type": "console", "payload": "Thread 1 (main)\n"},
                    {"type": "result", "payload": None, "token": 1000},
                ]
            ),
        ):
            result = result_to_mapping(running_session.execute_command("info threads"))

        assert result["status"] == "success"
        assert "Thread 1" in result["output"]

    def test_execute_command_mi(self, running_session, prompt_response):
        """MI commands should surface parsed result payloads."""

        with patch.object(
            running_session,
            "_send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {"type": "result", "payload": {"threads": []}, "token": 1000},
                ]
            ),
        ):
            result = result_to_mapping(running_session.execute_command("-thread-info"))

        assert result["status"] == "success"
        assert "result" in result

    def test_execute_command_mi_error_result(self, running_session, prompt_response):
        """MI error result records should remain visible as errors."""

        with patch.object(
            running_session,
            "_send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {
                        "type": "result",
                        "message": "error",
                        "payload": {"msg": "Thread ID 999 not known"},
                        "token": 1000,
                    }
                ]
            ),
        ):
            result = result_to_mapping(running_session.execute_command("-thread-select 999"))

        assert result["status"] == "error"
        assert result["command"] == "-thread-select 999"
        assert "Thread ID 999 not known" in result["message"]


class TestCallFunctionApi:
    """Test function-call behavior through the public API."""

    def test_call_function_no_session(self, session_service):
        """Function calls without an active session should fail."""

        result = result_to_mapping(session_service.call_function('printf("hello")'))

        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    def test_call_function_success(self, running_session, prompt_response):
        """Function calls should surface console return values."""

        with patch.object(
            running_session,
            "_send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[
                    {"type": "console", "payload": "$1 = 5\n"},
                    {"type": "result", "payload": None, "token": 1000},
                ]
            ),
        ):
            result = result_to_mapping(running_session.call_function('strlen("hello")'))

        assert result["status"] == "success"
        assert result["function_call"] == 'strlen("hello")'
        assert "$1 = 5" in result["result"]

    def test_call_function_timeout(self, running_session, prompt_response):
        """Function calls should report timeout failures."""

        with patch.object(
            running_session,
            "_send_command_and_wait_for_prompt",
            return_value=prompt_response(timed_out=True),
        ):
            result = result_to_mapping(running_session.call_function("some_slow_function()"))

        assert result["status"] == "error"
        assert "Timeout" in result["message"]

    def test_call_function_error(self, running_session, prompt_response):
        """Transport-level call failures should surface their message."""

        with patch.object(
            running_session,
            "_send_command_and_wait_for_prompt",
            return_value=prompt_response(error="No symbol table loaded"),
        ):
            result = result_to_mapping(running_session.call_function("unknown_func()"))

        assert result["status"] == "error"
        assert "No symbol table" in result["message"]
