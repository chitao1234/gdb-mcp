"""Execution-focused session API tests."""

from __future__ import annotations

from unittest.mock import patch

from gdb_mcp.domain import StopEvent, result_to_mapping


class TestExecutionApi:
    """Test execution and command-control behavior through the public API."""

    def test_execute_command_no_session(self, session_service):
        """Command execution without an active controller should fail."""

        result = result_to_mapping(session_service.execute_command("info threads"))

        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    def test_continue_execution(self, scripted_running_session, mi_result, mi_notify):
        """Continue should emit the MI exec-continue command."""

        session, controller = scripted_running_session(
            [
                mi_result(message="running"),
                mi_notify(
                    "stopped",
                    {
                        "reason": "breakpoint-hit",
                        "thread-id": "2",
                        "bkptno": "1",
                        "frame": {
                            "level": "0",
                            "func": "main",
                            "file": "app.c",
                            "line": "12",
                        },
                    },
                ),
            ]
        )

        result = result_to_mapping(session.continue_execution())

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["execution_state"] == "paused"
        assert status["stop_reason"] == "breakpoint-hit"
        stop_event = session.last_stop_event
        assert stop_event is not None
        assert stop_event.execution_state == "paused"
        assert stop_event.reason == "breakpoint-hit"
        assert stop_event.thread_id == 2
        assert stop_event.breakpoint_number == "1"
        assert stop_event.command == "-exec-continue"
        assert stop_event.frame is not None
        assert stop_event.frame["func"] == "main"
        assert session.runtime.stop_history[-1] == stop_event
        transcript_entry = session.command_transcript[-1]
        assert transcript_entry.command == "-exec-continue"
        assert transcript_entry.status == "success"
        assert transcript_entry.execution_state == "paused"
        assert transcript_entry.stop_reason == "breakpoint-hit"
        assert controller.io_manager.stdin.writes[0].decode().endswith("-exec-continue\n")

    def test_continue_execution_returns_running_when_no_stop_before_timeout(
        self,
        running_session,
        mi_result,
        prompt_response,
    ):
        """Continue should return a running success when no stop arrives before timeout."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(
                command_responses=[mi_result(message="running")],
                timed_out=True,
            ),
        ):
            result = result_to_mapping(running_session.continue_execution())

        assert result["status"] == "success"
        assert result["result"]["result_class"] == "running"
        assert "warnings" in result
        status = result_to_mapping(running_session.get_status())
        assert status["execution_state"] == "running"
        transcript_entry = running_session.command_transcript[-1]
        assert transcript_entry.command == "-exec-continue"
        assert transcript_entry.status == "success"
        assert transcript_entry.timed_out is True

    def test_continue_execution_when_already_running_returns_deterministic_error(
        self,
        running_session,
    ):
        """Continuing an already-running inferior should fail immediately and clearly."""

        running_session.runtime.mark_inferior_running()

        result = result_to_mapping(running_session.continue_execution())

        assert result["status"] == "error"
        assert "already running" in result["message"].lower()

    def test_continue_execution_tracks_stopped_inferior_from_thread_group(
        self,
        scripted_running_session,
        mi_result,
        mi_notify,
    ):
        """Stopped notifications should populate stop-event inferior identity."""

        session, _controller = scripted_running_session(
            [
                mi_result(message="running"),
                mi_notify(
                    "stopped",
                    {
                        "reason": "breakpoint-hit",
                        "thread-group": "i2",
                        "thread-id": "4",
                    },
                ),
            ]
        )
        session.runtime.mark_inferior_selected(2)

        result = result_to_mapping(session.continue_execution())

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["execution_state"] == "paused"
        assert status["current_inferior_id"] == 2
        assert status["inferior_states"] is not None
        inferior_state = next(
            record for record in status["inferior_states"] if record["inferior_id"] == 2
        )
        assert inferior_state["execution_state"] == "paused"
        assert inferior_state["stop_reason"] == "breakpoint-hit"
        assert session.last_stop_event is not None
        assert session.last_stop_event.inferior_id == 2

    def test_execute_command_tracks_thread_group_exit_notifications(
        self,
        scripted_running_session,
        mi_result,
        mi_notify,
    ):
        """Non-stop async thread-group exits should update per-inferior status."""

        session, _controller = scripted_running_session(
            [
                mi_notify("thread-group-started", {"id": "i2", "pid": "4321"}),
                mi_notify("thread-group-exited", {"id": "i2", "exit-code": "03"}),
                mi_result(),
            ]
        )
        session.runtime.mark_inferior_selected(2)

        result = result_to_mapping(session.execute_command("info threads"))

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["execution_state"] == "exited"
        assert status["stop_reason"] == "thread-group-exited"
        assert status["exit_code"] == 3
        assert status["inferior_states"] is not None
        inferior_state = next(
            record for record in status["inferior_states"] if record["inferior_id"] == 2
        )
        assert inferior_state["execution_state"] == "exited"
        assert inferior_state["exit_code"] == 3

    def test_execute_command_reselects_when_current_thread_group_removed(
        self,
        scripted_running_session,
        mi_result,
        mi_notify,
    ):
        """Removing the selected inferior should select a remaining inferior deterministically."""

        session, _controller = scripted_running_session(
            [
                mi_notify("thread-group-removed", {"id": "i2"}),
                mi_result(),
            ]
        )
        session.runtime.mark_inferior_running(inferior_id=1)
        session.runtime.mark_inferior_paused("signal-received", inferior_id=2)
        session.runtime.mark_inferior_selected(2)

        result = result_to_mapping(session.execute_command("info inferiors"))

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["current_inferior_id"] == 1
        assert status["execution_state"] == "running"
        assert status["inferior_states"] is not None
        inferior_ids = [record["inferior_id"] for record in status["inferior_states"]]
        assert inferior_ids == [1]

    def test_step(self, scripted_running_session, mi_result, mi_notify):
        """Step should emit the MI exec-step command."""

        session, controller = scripted_running_session(
            [
                mi_result(message="running"),
                mi_notify("stopped", {"reason": "end-stepping-range"}),
            ]
        )

        result = result_to_mapping(session.step())

        assert result["status"] == "success"
        assert controller.io_manager.stdin.writes[0].decode().endswith("-exec-step\n")

    def test_next(self, scripted_running_session, mi_result, mi_notify):
        """Next should emit the MI exec-next command."""

        session, controller = scripted_running_session(
            [
                mi_result(message="running"),
                mi_notify("stopped", {"reason": "end-stepping-range"}),
            ]
        )

        result = result_to_mapping(session.next())

        assert result["status"] == "success"
        assert controller.io_manager.stdin.writes[0].decode().endswith("-exec-next\n")

    def test_finish_surfaces_return_value_and_frame(self, scripted_running_session, mi_result):
        """Finish should return structured caller-frame and return-value details."""

        session, _controller = scripted_running_session(
            [
                mi_result(
                    {
                        "gdb-result-var": "$1",
                        "return-value": "42",
                        "frame": {"level": "0", "func": "caller", "file": "main.c", "line": "12"},
                    }
                )
            ]
        )

        result = result_to_mapping(session.finish(timeout_sec=9))

        assert result["status"] == "success"
        assert result["return_value"] == "42"
        assert result["gdb_result_var"] == "$1"
        assert result["frame"]["func"] == "caller"

    def test_wait_for_stop_returns_existing_stop_immediately(self, session_service):
        """Waiting on an already paused inferior should return the current stop state."""

        session_service.runtime.controller = object()
        session_service.runtime.mark_ready()
        session_service.runtime.mark_inferior_paused("breakpoint-hit")
        session_service.runtime.record_stop_event(
            StopEvent(
                execution_state="paused",
                reason="breakpoint-hit",
                command="-exec-run",
            )
        )

        result = result_to_mapping(session_service.wait_for_stop(stop_reasons=("breakpoint-hit",)))

        assert result["status"] == "success"
        assert result["matched"] is True
        assert result["source"] == "existing"
        assert result["stop_reason"] == "breakpoint-hit"

    def test_wait_for_stop_waits_for_notification(self, running_session):
        """Waiting should update runtime state from the next stopped notification."""

        running_session.runtime.mark_inferior_running()
        with patch.object(
            running_session._command_runner,
            "wait_for_stop",
            return_value={
                "command_responses": [
                    {
                        "type": "notify",
                        "message": "stopped",
                        "payload": {
                            "reason": "signal-received",
                            "thread-id": "2",
                            "frame": {"level": "0", "func": "main"},
                        },
                    }
                ],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(
                running_session.wait_for_stop(timeout_sec=5, stop_reasons=("signal-received",))
            )

        assert result["status"] == "success"
        assert result["matched"] is True
        assert result["source"] == "waited"
        assert result["stop_reason"] == "signal-received"
        assert running_session.runtime.execution_state == "paused"
        assert running_session.last_stop_event is not None
        assert running_session.last_stop_event.command == "wait_for_stop"
        assert running_session.last_stop_event.breakpoint_number is None

    def test_wait_for_stop_times_out(self, running_session):
        """Timeouts should be reported as structured non-matching wait results."""

        running_session.runtime.mark_inferior_running()
        with patch.object(
            running_session._command_runner,
            "wait_for_stop",
            return_value={"command_responses": [], "timed_out": True},
        ):
            result = result_to_mapping(running_session.wait_for_stop(timeout_sec=2))

        assert result["status"] == "success"
        assert result["matched"] is False
        assert result["timed_out"] is True
        assert running_session.runtime.execution_state == "running"

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

        with patch.object(running_session.runtime.os_module, "kill") as mock_kill:
            result = result_to_mapping(running_session.interrupt())

        assert result["status"] == "success"
        assert "interrupted" in result["message"].lower()
        status = result_to_mapping(running_session.get_status())
        assert status["execution_state"] == "paused"
        assert status["stop_reason"] == "signal-received"
        stop_event = running_session.last_stop_event
        assert stop_event is not None
        assert stop_event.command == "interrupt"
        assert stop_event.reason == "signal-received"
        assert stop_event.execution_state == "paused"
        transcript_entry = running_session.command_transcript[-1]
        assert transcript_entry.command == "interrupt"
        assert transcript_entry.status == "success"
        assert transcript_entry.stop_reason == "signal-received"
        mock_kill.assert_called_once()

    def test_interrupt_no_stopped_notification(self, running_session):
        """Interrupt should warn when no stopped notification is received."""

        running_session.controller.gdb_process.pid = 12345
        running_session.controller.get_gdb_response.return_value = []

        with patch.object(running_session.runtime.os_module, "kill") as mock_kill:
            result = result_to_mapping(running_session.interrupt())

        assert result["status"] == "warning"
        assert "no stopped notification" in result["message"].lower()
        status = result_to_mapping(running_session.get_status())
        assert status["execution_state"] == "running"
        mock_kill.assert_called_once()

    def test_execute_command_cli(self, scripted_running_session, mi_console, mi_result):
        """CLI commands should surface console output."""

        session, controller = scripted_running_session(
            [
                mi_console("Thread 1 (main)\n"),
                mi_result(),
            ]
        )

        result = result_to_mapping(session.execute_command("info threads"))

        assert result["status"] == "success"
        assert "Thread 1" in result["output"]
        assert (
            '-interpreter-exec console "info threads"'
            in controller.io_manager.stdin.writes[0].decode()
        )

    def test_execute_command_accepts_timeout_override(self, scripted_running_session, mi_result):
        """Command execution should pass the requested timeout through."""

        session, _controller = scripted_running_session([mi_result()])

        with patch.object(session._command_runner, "execute_command_result") as mock_execute:
            mock_execute.return_value = object()
            session.execute_command("info threads", timeout_sec=9)

        mock_execute.assert_called_once_with("info threads", 9)

    def test_execute_command_mi(self, scripted_running_session, mi_result):
        """MI commands should surface parsed result payloads."""

        session, controller = scripted_running_session([mi_result({"threads": []})])

        result = result_to_mapping(session.execute_command("-thread-info"))

        assert result["status"] == "success"
        assert "result" in result
        assert controller.io_manager.stdin.writes[0].decode().endswith("-thread-info\n")

    def test_execute_command_mi_error_result(self, scripted_running_session, mi_result):
        """MI error result records should remain visible as errors."""

        session, controller = scripted_running_session(
            [mi_result({"msg": "Thread ID 999 not known"}, message="error")]
        )

        result = result_to_mapping(session.execute_command("-thread-select 999"))

        assert result["status"] == "error"
        assert result["command"] == "-thread-select 999"
        assert "Thread ID 999 not known" in result["message"]
        assert controller.io_manager.stdin.writes[0].decode().endswith("-thread-select 999\n")

    def test_execute_command_marks_dead_transport_inactive(self, running_session):
        """Dead GDB children should transition the session out of running state."""

        with patch.object(running_session._command_runner, "is_gdb_alive", return_value=False):
            result = result_to_mapping(running_session.execute_command("info threads"))

        assert result["status"] == "error"
        assert "session is no longer active" in result["message"]
        assert running_session.controller is None
        assert running_session.is_running is False
        assert running_session.target_loaded is False

    def test_run_sets_execution_state_to_paused_when_stopped(
        self, scripted_running_session, mi_result, mi_notify
    ):
        """Structured run should update status when execution stops."""

        session, controller = scripted_running_session(
            [mi_result(message="running"), mi_notify("stopped", {"reason": "breakpoint-hit"})],
        )

        result = result_to_mapping(session.run(timeout_sec=5))

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["execution_state"] == "paused"
        assert status["stop_reason"] == "breakpoint-hit"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[-1].endswith("-exec-run\n")

    def test_run_with_args_preserves_argument_boundaries(
        self,
        scripted_running_session,
        mi_result,
        mi_notify,
    ):
        """run(args) should quote arguments instead of flattening them unsafely."""

        session, controller = scripted_running_session(
            [mi_result()],
            [
                mi_result(message="running"),
                mi_notify("stopped", {"reason": "exited-normally"}),
            ],
        )

        result = result_to_mapping(session.run(args=["--name", "hello world", 'quote"value']))

        assert result["status"] == "success"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert '-exec-arguments "--name" "hello world" "quote\\"value"\n' in written[0]
        assert written[1].endswith("-exec-run\n")

    def test_run_wait_for_stop_false_returns_running_success(
        self,
        scripted_running_session,
        mi_result,
    ):
        """Non-blocking run should return success once execution is acknowledged as running."""

        session, _controller = scripted_running_session([mi_result(message="running")])

        result = result_to_mapping(session.run(wait_for_stop=False, timeout_sec=1))

        assert result["status"] == "success"
        assert result["command"] == "-exec-run"
        assert session.runtime.execution_state == "running"

    def test_add_inferior_updates_inventory(self, scripted_running_session, mi_result, mi_console):
        """Adding an inferior should refresh and return the structured inferior inventory."""

        session, _controller = scripted_running_session(
            [mi_result({"inferior": "i2"})],
            [
                mi_console("  Num  Description       Connection           Executable        \n"),
                mi_console("* 1    <null>                                 /tmp/app \n"),
                mi_console("  2    <null>                                                   \n"),
                mi_result(),
            ],
        )

        result = result_to_mapping(session.add_inferior())

        assert result["status"] == "success"
        assert result["inferior_id"] == 2
        assert result["inferior_count"] == 2

    def test_remove_inferior_updates_inventory(
        self,
        scripted_running_session,
        mi_result,
        mi_console,
    ):
        """Removing an inferior should refresh the inventory and current selection."""

        session, _controller = scripted_running_session(
            [mi_result()],
            [
                mi_console("  Num  Description       Connection           Executable        \n"),
                mi_console("* 1    <null>                                 /tmp/app \n"),
                mi_result(),
            ],
        )
        session.runtime.update_inferior_inventory(current_inferior_id=2, count=2, inferior_ids=(1, 2))
        session.runtime.mark_inferior_selected(2)

        result = result_to_mapping(session.remove_inferior(2))

        assert result["status"] == "success"
        assert result["inferior_id"] == 2
        assert result["current_inferior_id"] == 1
        assert result["inferior_count"] == 1

    def test_run_parses_zero_padded_exit_code(self, scripted_running_session, mi_result, mi_notify):
        """Exit-code parsing should preserve decimal values from zero-padded fields."""

        session, _controller = scripted_running_session(
            [
                mi_result(message="running"),
                mi_notify(
                    "stopped",
                    {
                        "reason": "exited",
                        "exit-code": "07",
                    },
                ),
            ]
        )

        result = result_to_mapping(session.run(timeout_sec=5))

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["execution_state"] == "exited"
        assert status["stop_reason"] == "exited"
        assert status["exit_code"] == 7
        assert session.last_stop_event is not None
        assert session.last_stop_event.exit_code == 7

    def test_run_parses_hex_exit_code(self, scripted_running_session, mi_result, mi_notify):
        """Exit-code parsing should normalize hexadecimal payload values."""

        session, _controller = scripted_running_session(
            [
                mi_result(message="running"),
                mi_notify(
                    "stopped",
                    {
                        "reason": "exited",
                        "exit-code": "0x0A",
                    },
                ),
            ]
        )

        result = result_to_mapping(session.run(timeout_sec=5))

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["execution_state"] == "exited"
        assert status["stop_reason"] == "exited"
        assert status["exit_code"] == 10
        assert session.last_stop_event is not None
        assert session.last_stop_event.exit_code == 10

    def test_wait_for_stop_parses_hex_exit_code(self, running_session):
        """wait_for_stop should propagate parsed hexadecimal exit-code fields."""

        running_session.runtime.mark_inferior_running()
        with patch.object(
            running_session._command_runner,
            "wait_for_stop",
            return_value={
                "command_responses": [
                    {
                        "type": "notify",
                        "message": "stopped",
                        "payload": {
                            "reason": "exited",
                            "exit-code": "0x0B",
                            "thread-id": "1",
                        },
                    }
                ],
                "timed_out": False,
            },
        ):
            result = result_to_mapping(running_session.wait_for_stop(timeout_sec=5))

        assert result["status"] == "success"
        assert result["matched"] is True
        assert running_session.last_stop_event is not None
        assert running_session.last_stop_event.exit_code == 11
        status = result_to_mapping(running_session.get_status())
        assert status["execution_state"] == "exited"
        assert status["exit_code"] == 11

    def test_attach_process(self, scripted_running_session, mi_console, mi_result, mi_notify):
        """Attach should route through the CLI attach command and mark the target loaded."""

        session, controller = scripted_running_session(
            [
                mi_console("Attaching to process 1234\n"),
                mi_result(message="running"),
                mi_notify("stopped", {"reason": "signal-received"}),
            ]
        )

        result = result_to_mapping(session.attach_process(pid=1234, timeout_sec=8))

        assert result["status"] == "success"
        assert (
            '-interpreter-exec console "attach 1234"'
            in controller.io_manager.stdin.writes[0].decode()
        )
        status = result_to_mapping(session.get_status())
        assert status["target_loaded"] is True
        assert status["execution_state"] == "paused"

    def test_attach_process_overrides_not_started_state(
        self, scripted_running_session, mi_console, mi_result
    ):
        """Attach should transition not_started state to paused after success."""

        session, _controller = scripted_running_session(
            [
                mi_console("Attaching to process 1234\n"),
                mi_result(),
            ]
        )
        session.runtime.mark_inferior_not_started()

        result = result_to_mapping(session.attach_process(pid=1234, timeout_sec=8))

        assert result["status"] == "success"
        status = result_to_mapping(session.get_status())
        assert status["target_loaded"] is True
        assert status["execution_state"] == "paused"
        assert status["stop_reason"] == "attached"

    def test_set_follow_fork_mode(self, scripted_running_session, mi_result):
        """Follow-fork-mode should execute the expected CLI command and update runtime state."""

        session, controller = scripted_running_session([mi_result()])

        result = result_to_mapping(session.set_follow_fork_mode("child"))

        assert result["status"] == "success"
        assert result["mode"] == "child"
        assert session.runtime.follow_fork_mode == "child"
        assert (
            '-interpreter-exec console "set follow-fork-mode child"'
            in controller.io_manager.stdin.writes[0].decode()
        )

    def test_set_detach_on_fork(self, scripted_running_session, mi_result):
        """Detach-on-fork should execute the expected CLI command and update runtime state."""

        session, controller = scripted_running_session([mi_result()])

        result = result_to_mapping(session.set_detach_on_fork(False))

        assert result["status"] == "success"
        assert result["enabled"] is False
        assert session.runtime.detach_on_fork is False
        assert (
            '-interpreter-exec console "set detach-on-fork off"'
            in controller.io_manager.stdin.writes[0].decode()
        )


class TestCallFunctionApi:
    """Test function-call behavior through the public API."""

    def test_call_function_no_session(self, session_service):
        """Function calls without an active session should fail."""

        result = result_to_mapping(session_service.call_function('printf("hello")'))

        assert result["status"] == "error"
        assert "No active GDB session" in result["message"]

    def test_call_function_success(self, scripted_running_session, mi_console, mi_result):
        """Function calls should surface console return values."""

        session, controller = scripted_running_session(
            [
                mi_console("$1 = 5\n"),
                mi_result(),
            ]
        )

        result = result_to_mapping(session.call_function('strlen("hello")'))

        assert result["status"] == "success"
        assert result["function_call"] == 'strlen("hello")'
        assert "$1 = 5" in result["result"]
        assert 'call strlen(\\"hello\\")' in controller.io_manager.stdin.writes[0].decode()

    def test_call_function_timeout(self, running_session, prompt_response):
        """Function calls should report timeout failures."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(timed_out=True),
        ):
            result = result_to_mapping(running_session.call_function("some_slow_function()"))

        assert result["status"] == "error"
        assert "Timeout" in result["message"]

    def test_call_function_accepts_timeout_override(self, running_session):
        """Function calls should pass through the requested timeout."""

        with patch.object(
            running_session._command_runner, "send_command_and_wait_for_prompt"
        ) as mock_send:
            mock_send.return_value = {
                "command_responses": [],
                "async_notifications": [],
                "timed_out": False,
            }
            with patch("gdb_mcp.session.execution.parse_mi_responses") as mock_parse:
                mock_parse.return_value.is_error_result.return_value = False
                mock_parse.return_value.console = []
                running_session.call_function("some_func()", timeout_sec=11)

        assert mock_send.call_args.args[1] == 11

    def test_call_function_error(self, running_session, prompt_response):
        """Transport-level call failures should surface their message."""

        with patch.object(
            running_session._command_runner,
            "send_command_and_wait_for_prompt",
            return_value=prompt_response(error="No symbol table loaded"),
        ):
            result = result_to_mapping(running_session.call_function("unknown_func()"))

        assert result["status"] == "error"
        assert "No symbol table" in result["message"]

    def test_call_function_marks_dead_transport_inactive(self, running_session):
        """Dead GDB children should invalidate later function calls too."""

        with patch.object(running_session._command_runner, "is_gdb_alive", return_value=False):
            result = result_to_mapping(running_session.call_function("some_func()"))

        assert result["status"] == "error"
        assert "session is no longer active" in result["message"]
        assert running_session.controller is None
