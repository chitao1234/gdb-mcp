"""Inspection-focused session API tests."""

from __future__ import annotations

from unittest.mock import patch

from gdb_mcp.domain import result_to_mapping


class TestThreadAndStackInspectionApi:
    """Test thread selection and stack inspection behavior."""

    def test_get_threads_no_session(self, session_service):
        """Thread inspection without a controller should fail."""

        result = result_to_mapping(session_service.get_threads())

        assert result["status"] == "error"

    def test_get_threads_success(self, running_session, command_result):
        """Thread inspection should surface thread count and current thread."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(
                command,
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

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.get_threads())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert result["current_thread_id"] == "1"

    def test_select_thread(self, running_session, command_result):
        """Thread selection should surface the selected thread and frame."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(
                command,
                result={
                    "result": {
                        "new-thread-id": "2",
                        "frame": {"level": "0", "func": "worker_func"},
                    }
                },
            )

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.select_thread(thread_id=2))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["new_thread_id"] == "2"

    def test_get_backtrace_default(self, running_session, command_result):
        """Backtrace requests should return frame count for the current thread."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(
                command,
                result={"result": {"stack": [{"level": "0", "func": "main", "file": "test.c"}]}},
            )

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.get_backtrace())

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["thread_id"] is None

    def test_get_backtrace_specific_thread(self, running_session, command_result):
        """Backtrace requests for a specific thread should switch threads first."""

        commands_executed: list[str] = []

        def mock_execute(command, **kwargs):
            del kwargs
            commands_executed.append(command)
            if "thread-select" in command:
                return command_result(command)
            return command_result(command, result={"result": {"stack": []}})

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.get_backtrace(thread_id=3))

        assert result["status"] == "success"
        assert any("thread-select 3" in command for command in commands_executed)


class TestDataInspectionApi:
    """Test data-inspection behavior through the public API."""

    def test_evaluate_expression(self, running_session, command_result):
        """Expression evaluation should surface the resulting value."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(command, result={"result": {"value": "42"}})

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.evaluate_expression("x + y"))

        assert result["status"] == "success"
        assert result["expression"] == "x + y"
        assert result["value"] == "42"

    def test_get_variables(self, running_session, command_result):
        """Variable inspection should surface thread, frame, and values."""

        def mock_execute(command, **kwargs):
            del kwargs
            if "stack-select-frame" in command or "thread-select" in command:
                return command_result(command)
            return command_result(
                command,
                result={
                    "result": {
                        "variables": [
                            {"name": "x", "value": "10"},
                            {"name": "y", "value": "20"},
                        ]
                    }
                },
            )

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.get_variables(thread_id=2, frame=1))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["frame"] == 1
        assert len(result["variables"]) == 2

    def test_get_registers(self, running_session, command_result):
        """Register inspection should surface all returned register values."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(
                command,
                result={
                    "result": {
                        "register-values": [
                            {"number": "0", "value": "0x1234"},
                            {"number": "1", "value": "0x5678"},
                        ]
                    }
                },
            )

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.get_registers())

        assert result["status"] == "success"
        assert len(result["registers"]) == 2
