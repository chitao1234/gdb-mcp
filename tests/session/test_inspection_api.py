"""Inspection-focused session API tests."""

from __future__ import annotations

from gdb_mcp.domain import result_to_mapping


class TestThreadAndStackInspectionApi:
    """Test thread selection and stack inspection behavior."""

    def test_get_threads_no_session(self, session_service):
        """Thread inspection without a controller should fail."""

        result = result_to_mapping(session_service.get_threads())

        assert result["status"] == "error"

    def test_get_threads_success(self, scripted_running_session, mi_result):
        """Thread inspection should surface thread count and current thread."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "threads": [
                            {"id": "1", "name": "main"},
                            {"id": "2", "name": "worker-1"},
                        ],
                        "current-thread-id": "1",
                    }
                )
            ]
        )

        result = result_to_mapping(session.get_threads())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert result["current_thread_id"] == "1"
        assert controller.io_manager.stdin.writes[0].decode().endswith("-thread-info\n")

    def test_select_thread(self, scripted_running_session, mi_result):
        """Thread selection should surface the selected thread and frame."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "new-thread-id": "2",
                        "frame": {"level": "0", "func": "worker_func"},
                    }
                )
            ]
        )

        result = result_to_mapping(session.select_thread(thread_id=2))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["new_thread_id"] == "2"
        assert controller.io_manager.stdin.writes[0].decode().endswith("-thread-select 2\n")

    def test_get_backtrace_default(self, scripted_running_session, mi_result):
        """Backtrace requests should return frame count for the current thread."""

        session, controller = scripted_running_session(
            [mi_result({"stack": [{"level": "0", "func": "main", "file": "test.c"}]})]
        )

        result = result_to_mapping(session.get_backtrace())

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["thread_id"] is None
        assert controller.io_manager.stdin.writes[0].decode().endswith("-stack-list-frames 0 100\n")

    def test_get_backtrace_specific_thread(self, scripted_running_session, mi_result):
        """Backtrace requests for a specific thread should switch threads first."""

        session, controller = scripted_running_session(
            [mi_result()],
            [mi_result({"stack": []})],
        )

        result = result_to_mapping(session.get_backtrace(thread_id=3))

        assert result["status"] == "success"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert any("-thread-select 3" in command for command in written)
        assert any("-stack-list-frames 0 100" in command for command in written)


class TestDataInspectionApi:
    """Test data-inspection behavior through the public API."""

    def test_evaluate_expression(self, scripted_running_session, mi_result):
        """Expression evaluation should surface the resulting value."""

        session, controller = scripted_running_session([mi_result({"value": "42"})])

        result = result_to_mapping(session.evaluate_expression("x + y"))

        assert result["status"] == "success"
        assert result["expression"] == "x + y"
        assert result["value"] == "42"
        assert controller.io_manager.stdin.writes[0].decode().endswith(
            '-data-evaluate-expression "x + y"\n'
        )

    def test_get_variables(self, scripted_running_session, mi_result):
        """Variable inspection should surface thread, frame, and values."""

        session, controller = scripted_running_session(
            [mi_result()],
            [mi_result()],
            [
                mi_result(
                    {
                        "variables": [
                            {"name": "x", "value": "10"},
                            {"name": "y", "value": "20"},
                        ]
                    }
                )
            ],
        )

        result = result_to_mapping(session.get_variables(thread_id=2, frame=1))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["frame"] == 1
        assert len(result["variables"]) == 2
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert any("-thread-select 2" in command for command in written)
        assert any("-stack-select-frame 1" in command for command in written)
        assert any("-stack-list-variables --simple-values" in command for command in written)

    def test_get_registers(self, scripted_running_session, mi_result):
        """Register inspection should surface all returned register values."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "register-values": [
                            {"number": "0", "value": "0x1234"},
                            {"number": "1", "value": "0x5678"},
                        ]
                    }
                )
            ]
        )

        result = result_to_mapping(session.get_registers())

        assert result["status"] == "success"
        assert len(result["registers"]) == 2
        assert controller.io_manager.stdin.writes[0].decode().endswith(
            "-data-list-register-values x\n"
        )
