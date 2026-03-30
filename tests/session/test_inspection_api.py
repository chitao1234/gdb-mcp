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

    def test_list_inferiors(self, scripted_running_session, mi_console, mi_result):
        """Inferior inventory should parse the CLI table into structured records."""

        session, controller = scripted_running_session(
            [
                mi_console("  Num  Description       Connection           Executable        \n"),
                mi_console("* 1    <null>                                 /tmp/app \n"),
                mi_console("  2    <null>                                                   \n"),
                mi_result(),
            ]
        )

        result = result_to_mapping(session.list_inferiors())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert result["current_inferior_id"] == 1
        assert result["inferiors"][0]["inferior_id"] == 1
        assert result["inferiors"][0]["is_current"] is True
        assert result["inferiors"][0]["executable"] == "/tmp/app"
        assert result["inferiors"][1]["inferior_id"] == 2
        assert controller.io_manager.stdin.writes[0].decode().endswith(
            '-interpreter-exec console "info inferiors"\n'
        )

    def test_list_inferiors_includes_runtime_state_summary(
        self,
        scripted_running_session,
        mi_console,
        mi_result,
    ):
        """Inferior listings should carry known runtime state metadata per inferior."""

        session, _controller = scripted_running_session(
            [
                mi_console("  Num  Description       Connection           Executable        \n"),
                mi_console("  1    <null>                                 /tmp/app \n"),
                mi_console("* 2    <null>                                 /tmp/app \n"),
                mi_result(),
            ]
        )
        session.runtime.mark_inferior_paused("signal-received", inferior_id=2)
        session.runtime.mark_inferior_selected(2)

        result = result_to_mapping(session.list_inferiors())

        assert result["status"] == "success"
        assert result["inferiors"][0]["inferior_id"] == 1
        assert result["inferiors"][0]["execution_state"] == "unknown"
        assert result["inferiors"][1]["inferior_id"] == 2
        assert result["inferiors"][1]["execution_state"] == "paused"
        assert result["inferiors"][1]["stop_reason"] == "signal-received"

    def test_select_inferior(self, scripted_running_session, mi_console, mi_result):
        """Inferior selection should refresh inventory and update runtime state."""

        session, controller = scripted_running_session(
            [
                mi_console("[Switching to inferior 2 [<null>] (<noexec>)]\n"),
                mi_result(),
            ],
            [
                mi_console("  Num  Description       Connection           Executable        \n"),
                mi_console("  1    <null>                                 /tmp/app \n"),
                mi_console("* 2    <null>                                                   \n"),
                mi_result(),
            ],
        )

        result = result_to_mapping(session.select_inferior(2))

        assert result["status"] == "success"
        assert result["inferior_id"] == 2
        assert result["is_current"] is True
        assert session.runtime.current_inferior_id == 2
        assert session.runtime.current_thread_id is None
        assert session.runtime.current_frame is None
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-interpreter-exec console "inferior 2"\n')
        assert written[1].endswith('-interpreter-exec console "info inferiors"\n')

    def test_get_backtrace_default(self, scripted_running_session, mi_result):
        """Backtrace requests should return frame count for the current thread."""

        session, controller = scripted_running_session(
            [mi_result({"stack": [{"level": "0", "func": "main", "file": "test.c"}]})]
        )
        session.runtime.mark_thread_selected(7)

        result = result_to_mapping(session.get_backtrace())

        assert result["status"] == "success"
        assert result["count"] == 1
        assert result["thread_id"] == 7
        assert controller.io_manager.stdin.writes[0].decode().endswith("-stack-list-frames 0 99\n")

    def test_get_backtrace_specific_thread(self, scripted_running_session, mi_result):
        """Backtrace requests for a specific thread should restore the original selection."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "3"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result()],
            [mi_result({"stack": []})],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.get_backtrace(thread_id=3))

        assert result["status"] == "success"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-thread-info\n")
        assert written[1].endswith("-stack-info-frame\n")
        assert any("-thread-select 3" in command for command in written)
        assert any("-stack-list-frames 0 99" in command for command in written)
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")

    def test_get_backtrace_max_frames_is_an_upper_bound(
        self,
        scripted_running_session,
        mi_result,
    ):
        """The requested max_frames count should not become an inclusive upper index."""

        session, controller = scripted_running_session(
            [mi_result({"stack": [{"level": "0", "func": "main"}]})]
        )

        result = result_to_mapping(session.get_backtrace(max_frames=1))

        assert result["status"] == "success"
        assert controller.io_manager.stdin.writes[0].decode().endswith("-stack-list-frames 0 0\n")


class TestDataInspectionApi:
    """Test data-inspection behavior through the public API."""

    def test_evaluate_expression(self, scripted_running_session, mi_result):
        """Expression evaluation should surface the resulting value."""

        session, controller = scripted_running_session([mi_result({"value": "42"})])

        result = result_to_mapping(session.evaluate_expression("x + y"))

        assert result["status"] == "success"
        assert result["expression"] == "x + y"
        assert result["value"] == "42"
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith('-data-evaluate-expression "x + y"\n')
        )

    def test_evaluate_expression_escapes_quotes_and_backslashes(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Expression evaluation should escape MI-sensitive characters."""

        session, controller = scripted_running_session([mi_result({"value": "5"})])

        result = result_to_mapping(session.evaluate_expression('strlen("a\\\\b")'))

        assert result["status"] == "success"
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith('-data-evaluate-expression "strlen(\\"a\\\\\\\\b\\")"\n')
        )

    def test_evaluate_expression_with_thread_and_frame_restores_selection(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Expression evaluation should support stateless context overrides."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result()],
            [mi_result()],
            [mi_result({"value": "99"})],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.evaluate_expression("x", thread_id=2, frame=1))

        assert result["status"] == "success"
        assert result["value"] == "99"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-thread-info\n")
        assert written[1].endswith("-stack-info-frame\n")
        assert any("-thread-select 2" in command for command in written)
        assert any("-stack-select-frame 1" in command for command in written)
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")

    def test_evaluate_expression_restores_selection_on_frame_switch_error(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Failed temporary frame selection should still restore the original context."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result()],
            [mi_result({"msg": "No frame 999"}, message="error")],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.evaluate_expression("x", thread_id=2, frame=999))

        assert result["status"] == "error"
        assert result["message"] == "No frame 999"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")
        assert session.runtime.current_thread_id == 1
        assert session.runtime.current_frame == 0

    def test_get_variables(self, scripted_running_session, mi_result):
        """Variable inspection should restore the original thread/frame selection."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
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
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.get_variables(thread_id=2, frame=1))

        assert result["status"] == "success"
        assert result["thread_id"] == 2
        assert result["frame"] == 1
        assert len(result["variables"]) == 2
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-thread-info\n")
        assert written[1].endswith("-stack-info-frame\n")
        assert any("-thread-select 2" in command for command in written)
        assert any("-stack-select-frame 1" in command for command in written)
        assert any("-stack-list-variables --simple-values" in command for command in written)
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")
        assert session.runtime.current_thread_id == 1
        assert session.runtime.current_frame == 0

    def test_get_variables_reports_current_thread_when_no_override_is_supplied(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Variable inspection should report the effective current thread in its payload."""

        session, _controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result({"variables": [{"name": "x", "value": "10"}]})],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.get_variables())

        assert result["status"] == "success"
        assert result["thread_id"] == 1
        assert result["frame"] == 0
        assert len(result["variables"]) == 1

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
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith("-data-list-register-values x\n")
        )

    def test_get_registers_with_thread_and_frame_restores_selection(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Register inspection should support stateless context overrides."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result()],
            [mi_result()],
            [mi_result({"register-values": [{"number": "0", "value": "0x1"}]})],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.get_registers(thread_id=2, frame=1))

        assert result["status"] == "success"
        assert len(result["registers"]) == 1
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-thread-info\n")
        assert written[1].endswith("-stack-info-frame\n")
        assert any("-thread-select 2" in command for command in written)
        assert any("-stack-select-frame 1" in command for command in written)
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")

    def test_get_registers_restores_selection_on_frame_switch_error(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Failed temporary frame selection should still restore context for register reads."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result()],
            [mi_result({"msg": "No frame 999"}, message="error")],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.get_registers(thread_id=2, frame=999))

        assert result["status"] == "error"
        assert result["message"] == "No frame 999"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")
        assert session.runtime.current_thread_id == 1
        assert session.runtime.current_frame == 0

    def test_get_registers_supports_number_filters_and_natural_format(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Register listing should pass explicit number filters and natural format token."""

        session, controller = scripted_running_session(
            [mi_result({"register-values": [{"number": "0", "value": "1"}, {"number": "3", "value": "2"}]})]
        )

        result = result_to_mapping(
            session.get_registers(register_numbers=[0, 3], value_format="natural")
        )

        assert result["status"] == "success"
        assert len(result["registers"]) == 2
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith("-data-list-register-values N 0 3\n")
        )

    def test_get_registers_resolves_name_filters(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Register-name filters should resolve to register numbers before value lookup."""

        session, controller = scripted_running_session(
            [mi_result({"register-names": ["rax", "rbx", "rip"]})],
            [mi_result({"register-values": [{"number": "2", "value": "0x4444"}]})],
        )

        result = result_to_mapping(session.get_registers(register_names=["rip"]))

        assert result["status"] == "success"
        assert result["registers"][0]["number"] == "2"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-data-list-register-names\n")
        assert written[1].endswith("-data-list-register-values x 2\n")

    def test_get_registers_can_omit_vector_registers(self, scripted_running_session, mi_result):
        """Vector/SIMD registers should be filtered when include_vector_registers is false."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "register-values": [
                            {"number": "0", "value": "0x1"},
                            {"number": "17", "value": "0x2"},
                        ]
                    }
                )
            ],
            [mi_result({"register-names": ["rax", "xmm0"]})],
        )

        result = result_to_mapping(session.get_registers(include_vector_registers=False))

        assert result["status"] == "success"
        assert len(result["registers"]) == 1
        assert result["registers"][0]["number"] == "0"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[1].endswith("-data-list-register-names 0 17\n")

    def test_get_registers_applies_max_registers_limit(self, scripted_running_session, mi_result):
        """Register payload should respect max_registers upper bounds."""

        session, _controller = scripted_running_session(
            [
                mi_result(
                    {
                        "register-values": [
                            {"number": "0", "value": "0x1"},
                            {"number": "1", "value": "0x2"},
                            {"number": "2", "value": "0x3"},
                        ]
                    }
                )
            ]
        )

        result = result_to_mapping(session.get_registers(max_registers=2))

        assert result["status"] == "success"
        assert len(result["registers"]) == 2

    def test_read_memory(self, scripted_running_session, mi_result):
        """Memory reads should surface captured blocks and byte counts."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "memory": [
                            {
                                "begin": "0x1000",
                                "offset": "0x0",
                                "end": "0x1004",
                                "contents": "01020304",
                            }
                        ]
                    }
                )
            ]
        )

        result = result_to_mapping(session.read_memory("&value", 4, offset=1))

        assert result["status"] == "success"
        assert result["address"] == "&value"
        assert result["count"] == 4
        assert result["offset"] == 1
        assert result["block_count"] == 1
        assert result["captured_bytes"] == 4
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith('-data-read-memory-bytes -o 1 "&value" 4\n')
        )

    def test_get_variables_restores_selection_on_frame_switch_error(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Failed temporary frame selection should still restore context for variable reads."""

        session, controller = scripted_running_session(
            [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
            [mi_result({"frame": {"level": "0", "func": "main"}})],
            [mi_result()],
            [mi_result({"msg": "No frame 999"}, message="error")],
            [mi_result()],
            [mi_result()],
        )

        result = result_to_mapping(session.get_variables(thread_id=2, frame=999))

        assert result["status"] == "error"
        assert result["message"] == "No frame 999"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[-2].endswith("-thread-select 1\n")
        assert written[-1].endswith("-stack-select-frame 0\n")
        assert session.runtime.current_thread_id == 1
        assert session.runtime.current_frame == 0
