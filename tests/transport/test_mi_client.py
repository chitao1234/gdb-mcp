"""Unit tests for the low-level MI transport client."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from gdb_mcp.transport.mi_client import MiClient
from gdb_mcp.transport.mi_commands import (
    build_read_memory_command,
    escape_mi_string,
    is_cli_command,
    wrap_cli_command,
)


class _FakeStdin:
    def __init__(self):
        self.writes: list[bytes] = []
        self.flush_count = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        self.flush_count += 1


class _FakeController:
    def __init__(self, responses: list[list[dict]]):
        self.io_manager = MagicMock()
        self.io_manager.stdin = _FakeStdin()
        self._responses = list(responses)
        self.exit_called = False
        self.gdb_process = MagicMock()

    def get_gdb_response(self, *, timeout_sec: float, raise_error_on_timeout: bool):
        if self._responses:
            return self._responses.pop(0)
        return []

    def exit(self) -> None:
        self.exit_called = True


class TestMiCommands:
    """Test MI command formatting helpers."""

    def test_is_cli_command(self):
        """Commands without a leading dash should be treated as CLI commands."""

        assert is_cli_command("info threads") is True
        assert is_cli_command("-thread-info") is False

    def test_escape_mi_string(self):
        """Backslashes and double quotes should be escaped for MI strings."""

        assert escape_mi_string('say "hello" \\ world') == 'say \\"hello\\" \\\\ world'

    def test_escape_mi_string_escapes_control_characters(self):
        """Control characters should be escaped instead of being written literally."""

        assert escape_mi_string("line1\nline2\r\t") == "line1\\nline2\\r\\t"

    def test_wrap_cli_command(self):
        """CLI commands should be wrapped with interpreter-exec."""

        wrapped = wrap_cli_command('print "hello"')
        assert wrapped == '-interpreter-exec console "print \\"hello\\""'

    def test_wrap_cli_command_escapes_newlines(self):
        """Wrapped CLI commands should not contain literal line breaks."""

        wrapped = wrap_cli_command('print "hello"\nshow version\r')
        assert wrapped == '-interpreter-exec console "print \\"hello\\"\\nshow version\\r"'

    def test_build_read_memory_command(self):
        """Structured memory reads should quote the address expression safely."""

        command = build_read_memory_command("&value + 4", 16, offset=2)
        assert command == '-data-read-memory-bytes -o 2 "&value + 4" 16'


class TestMiClient:
    """Test the extracted GDB/MI command transport behavior."""

    def _make_client(self, controller):
        client = MiClient(
            controller_factory=MagicMock(return_value=controller),
            initial_command_token=1000,
            poll_timeout_sec=0.01,
        )
        client.controller = controller
        return client

    def test_start_uses_controller_factory(self):
        """Starting the client should create and retain the controller."""

        controller = _FakeController([])
        controller_factory = MagicMock(return_value=controller)
        client = MiClient(
            controller_factory=controller_factory,
            initial_command_token=1000,
            poll_timeout_sec=0.01,
        )

        result = client.start(
            command=["gdb", "--quiet", "--interpreter=mi"],
            time_to_check_for_additional_output_sec=1.0,
        )

        assert result is controller
        assert client.controller is controller
        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi"],
            time_to_check_for_additional_output_sec=1.0,
        )

    def test_start_forwards_cwd_when_provided(self):
        """Starting with a working directory should forward cwd to the controller factory."""

        controller = _FakeController([])
        controller_factory = MagicMock(return_value=controller)
        client = MiClient(
            controller_factory=controller_factory,
            initial_command_token=1000,
            poll_timeout_sec=0.01,
        )

        client.start(
            command=["gdb", "--quiet", "--interpreter=mi"],
            time_to_check_for_additional_output_sec=1.0,
            cwd="/tmp/work",
        )

        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi"],
            time_to_check_for_additional_output_sec=1.0,
            cwd="/tmp/work",
        )

    def test_read_initial_output_drains_startup_records(self):
        """Startup output emitted before the first command should be retrievable."""

        controller = _FakeController(
            [[{"type": "log", "payload": "/missing/program: No such file or directory.\n"}], []]
        )
        client = self._make_client(controller)

        result = client.read_initial_output(timeout_sec=0.1)

        assert result == [
            {"type": "log", "payload": "/missing/program: No such file or directory.\n"}
        ]

    def test_send_command_collects_result_and_async_notifications(self):
        """A matching result record should end the read loop and retain async output."""

        controller = _FakeController(
            [
                [
                    {"type": "console", "payload": "Reading symbols...\n"},
                    {"type": "notify", "payload": {"msg": "thread-created"}},
                    {"type": "notify", "token": 999, "payload": {"msg": "old-command"}},
                    {"type": "result", "token": 1000, "message": "done", "payload": {"ok": True}},
                ]
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-gdb-version", timeout_sec=1.0)

        assert result.error is None
        assert result.timed_out is False
        assert controller.io_manager.stdin.writes == [b"1000-gdb-version\n"]
        assert controller.io_manager.stdin.flush_count == 1
        assert result.command_responses[0]["type"] == "console"
        assert result.command_responses[-1]["type"] == "result"
        assert result.async_notifications == [
            {"type": "notify", "payload": {"msg": "thread-created"}},
            {"type": "notify", "token": 999, "payload": {"msg": "old-command"}},
        ]

    def test_send_command_does_not_complete_exec_on_unrelated_tokenless_notify(self):
        """Token-less async notifications should not satisfy an exec stop wait unless stopped."""

        controller = _FakeController(
            [
                [{"type": "result", "token": 1000, "message": "running", "payload": None}],
                [{"type": "notify", "message": "thread-created", "payload": {"id": "2"}}],
                [],
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-exec-continue", timeout_sec=0.02)

        assert result.timed_out is True
        assert result.command_responses == [
            {"type": "result", "token": 1000, "message": "running", "payload": None}
        ]
        assert result.async_notifications == [
            {"type": "notify", "message": "thread-created", "payload": {"id": "2"}}
        ]

    def test_send_command_waits_for_stop_after_running_result(self):
        """Execution commands should not return until the later stopped notification drains."""

        controller = _FakeController(
            [
                [{"type": "result", "token": 1000, "message": "running", "payload": None}],
                [{"type": "notify", "message": "stopped", "payload": {"reason": "breakpoint-hit"}}],
                [],
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-exec-continue", timeout_sec=1.0)

        assert result.error is None
        assert result.timed_out is False
        assert [record["type"] for record in result.command_responses] == ["result", "notify"]
        assert result.command_responses[0]["message"] == "running"
        assert result.command_responses[1]["message"] == "stopped"

    def test_send_command_treats_tokenless_stopped_as_exec_completion(self):
        """The stop event for an exec command should still be attached when token-less."""

        controller = _FakeController(
            [
                [{"type": "result", "token": 1000, "message": "running", "payload": None}],
                [
                    {
                        "type": "notify",
                        "message": "stopped",
                        "payload": {"reason": "signal-received"},
                    }
                ],
                [],
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-exec-next", timeout_sec=1.0)

        assert result.timed_out is False
        assert [record["type"] for record in result.command_responses] == ["result", "notify"]
        assert result.command_responses[1]["message"] == "stopped"

    def test_send_command_treats_tokenless_stopped_as_cli_run_completion(self):
        """CLI commands that return a running result should also wait for stopped."""

        controller = _FakeController(
            [
                [{"type": "result", "token": 1000, "message": "running", "payload": None}],
                [{"type": "notify", "message": "stopped", "payload": {"reason": "breakpoint-hit"}}],
                [],
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt(
            '-interpreter-exec console "run"', timeout_sec=1.0
        )

        assert result.timed_out is False
        assert [record["type"] for record in result.command_responses] == ["result", "notify"]
        assert result.command_responses[1]["message"] == "stopped"

    def test_send_command_detects_fatal_error_and_cleans_up(self):
        """Fatal GDB output should stop the client and return a fatal response."""

        controller = _FakeController(
            [[{"type": "console", "payload": "A fatal error internal to GDB has been detected"}]]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-gdb-version", timeout_sec=1.0)

        assert result.fatal is True
        assert "fatal error" in result.error.lower()
        assert client.controller is None
        assert controller.exit_called is True

    def test_send_command_cleans_up_when_gdb_process_exits(self):
        """Unexpected child exit should be treated as a terminal transport failure."""

        controller = _FakeController([[]])
        client = self._make_client(controller)

        with patch("gdb_mcp.transport.mi_client._LIVENESS_CHECK_INTERVAL_SEC", 0.0):
            with patch.object(client, "is_alive", return_value=False):
                with patch.object(client, "_extract_exit_code", return_value=-9):
                    result = client.send_command_and_wait_for_prompt(
                        "-gdb-version", timeout_sec=0.1
                    )

        assert result.fatal is True
        assert "exited unexpectedly" in result.error
        assert client.controller is None
        assert controller.exit_called is True

    def test_send_command_uses_absolute_timeout_with_async_traffic(self):
        """Unrelated async traffic should not extend the command deadline indefinitely."""

        controller = _FakeController(
            [
                [{"type": "notify", "token": 999, "message": "thread-created", "payload": {}}],
                [{"type": "notify", "token": 999, "message": "library-loaded", "payload": {}}],
                [{"type": "notify", "token": 999, "message": "thread-created", "payload": {}}],
                [{"type": "result", "token": 1000, "message": "done", "payload": {"ok": True}}],
            ]
        )
        client = self._make_client(controller)

        class _FakeClock:
            def __init__(self):
                self._value = 0.0

            def monotonic(self) -> float:
                current = self._value
                self._value += 0.03
                return current

        fake_clock = _FakeClock()

        with patch("gdb_mcp.transport.mi_client.time.monotonic", side_effect=fake_clock.monotonic):
            result = client.send_command_and_wait_for_prompt("-thread-info", timeout_sec=0.1)

        assert result.timed_out is True
        assert result.error is None
        assert result.command_responses == []
        assert len(result.async_notifications) >= 1

    def test_send_command_completes_after_result_even_with_continuous_async_traffic(self):
        """Non-running commands should return once their result record is parsed."""

        class ContinuousAsyncController:
            def __init__(self):
                self.io_manager = MagicMock()
                self.io_manager.stdin = _FakeStdin()
                self.gdb_process = MagicMock()
                self._step = 0

            def get_gdb_response(self, *, timeout_sec: float, raise_error_on_timeout: bool):
                del timeout_sec, raise_error_on_timeout
                self._step += 1
                if self._step == 1:
                    return [
                        {"type": "result", "token": 1000, "message": "done", "payload": {"ok": True}},
                        {"type": "notify", "message": "thread-created", "payload": {"id": "2"}},
                    ]
                return [{"type": "notify", "message": "library-loaded", "payload": {"id": self._step}}]

            def exit(self) -> None:
                pass

        client = self._make_client(ContinuousAsyncController())

        result = client.send_command_and_wait_for_prompt("-thread-info", timeout_sec=0.02)

        assert result.timed_out is False
        assert result.error is None
        assert result.command_responses == [
            {"type": "result", "token": 1000, "message": "done", "payload": {"ok": True}}
        ]
        assert result.async_notifications == [
            {"type": "notify", "message": "thread-created", "payload": {"id": "2"}}
        ]

    def test_send_command_completes_running_command_after_stop_under_async_noise(self):
        """Running commands should return once stopped is observed, even if async noise continues."""

        class ContinuousAsyncController:
            def __init__(self):
                self.io_manager = MagicMock()
                self.io_manager.stdin = _FakeStdin()
                self.gdb_process = MagicMock()
                self._step = 0

            def get_gdb_response(self, *, timeout_sec: float, raise_error_on_timeout: bool):
                del timeout_sec, raise_error_on_timeout
                self._step += 1
                if self._step == 1:
                    return [
                        {"type": "result", "token": 1000, "message": "running", "payload": None},
                        {"type": "notify", "message": "stopped", "payload": {"reason": "breakpoint-hit"}},
                        {"type": "notify", "message": "thread-created", "payload": {"id": "2"}},
                    ]
                return [{"type": "notify", "message": "library-loaded", "payload": {"id": self._step}}]

            def exit(self) -> None:
                pass

        client = self._make_client(ContinuousAsyncController())

        result = client.send_command_and_wait_for_prompt("-exec-next", timeout_sec=0.02)

        assert result.timed_out is False
        assert result.error is None
        assert [record["type"] for record in result.command_responses] == ["result", "notify"]
        assert result.command_responses[0]["message"] == "running"
        assert result.command_responses[1]["message"] == "stopped"
        assert result.async_notifications == [
            {"type": "notify", "message": "thread-created", "payload": {"id": "2"}}
        ]

    def test_interrupt_rejects_when_command_is_in_progress(self):
        """Interrupt should fail fast instead of racing another in-flight command."""

        controller = _FakeController([])
        client = self._make_client(controller)

        assert client._command_lock.acquire(blocking=False) is True
        try:
            result = client.interrupt_and_wait_for_stop(
                send_interrupt=MagicMock(),
                timeout_sec=1.0,
            )
        finally:
            client._command_lock.release()

        assert result.error == "Cannot interrupt while another command is in progress"

    def test_wait_for_stop_returns_stopped_notification(self):
        """Passive stop waits should return once a stopped notification arrives."""

        controller = _FakeController(
            [[{"type": "notify", "message": "stopped", "payload": {"reason": "fork"}}]]
        )
        client = self._make_client(controller)

        result = client.wait_for_stop(timeout_sec=1.0)

        assert result.error is None
        assert result.timed_out is False
        assert result.command_responses[0]["message"] == "stopped"

    def test_wait_for_stop_rejects_when_command_is_in_progress(self):
        """Passive stop waits should not race another in-flight command."""

        controller = _FakeController([])
        client = self._make_client(controller)

        assert client._command_lock.acquire(blocking=False) is True
        try:
            result = client.wait_for_stop(timeout_sec=1.0)
        finally:
            client._command_lock.release()

        assert result.error == "Cannot wait for stop while another command is in progress"

    def test_exit_waits_for_inflight_command_before_clearing_controller(self):
        """Controller teardown should block until the active command finishes reading."""

        class BlockingController:
            def __init__(self):
                self.io_manager = MagicMock()
                self.io_manager.stdin = _FakeStdin()
                self.gdb_process = MagicMock()
                self._release_response = threading.Event()
                self._returned_result = False
                self.exit_called = False

            def get_gdb_response(self, *, timeout_sec: float, raise_error_on_timeout: bool):
                del timeout_sec, raise_error_on_timeout
                if not self._returned_result:
                    self._release_response.wait(timeout=1.0)
                    self._returned_result = True
                    return [{"type": "result", "token": 1000, "message": "done", "payload": None}]
                return []

            def exit(self) -> None:
                self.exit_called = True

        controller = BlockingController()
        client = self._make_client(controller)
        command_thread = threading.Thread(
            target=client.send_command_and_wait_for_prompt,
            args=("-thread-info",),
            kwargs={"timeout_sec": 1.0},
        )
        command_thread.start()

        while len(controller.io_manager.stdin.writes) < 1:
            time.sleep(0.01)

        exit_thread = threading.Thread(target=client.exit)
        exit_thread.start()

        time.sleep(0.05)
        assert exit_thread.is_alive() is True
        assert controller.exit_called is False
        assert client.controller is controller

        controller._release_response.set()
        command_thread.join()
        exit_thread.join()

        assert controller.exit_called is True
        assert client.controller is None

    def test_send_command_serializes_same_session_calls(self):
        """Concurrent commands on one client should not interleave writes."""

        class BlockingController:
            def __init__(self):
                self.io_manager = MagicMock()
                self.io_manager.stdin = _FakeStdin()
                self.gdb_process = MagicMock()
                self._release_first = threading.Event()
                self._first_command_done = False
                self._second_command_done = False

            def get_gdb_response(self, *, timeout_sec: float, raise_error_on_timeout: bool):
                del timeout_sec, raise_error_on_timeout
                write_count = len(self.io_manager.stdin.writes)
                if write_count == 1 and not self._first_command_done:
                    self._release_first.wait(timeout=1.0)
                    self._first_command_done = True
                    return [{"type": "result", "token": 1000, "message": "done", "payload": None}]
                if write_count == 1 and self._first_command_done:
                    return []
                if write_count == 2 and not self._second_command_done:
                    self._second_command_done = True
                    return [{"type": "result", "token": 1001, "message": "done", "payload": None}]
                return []

            def exit(self) -> None:
                pass

        controller = BlockingController()
        client = self._make_client(controller)
        results: list = []

        def run_command(command: str):
            results.append(client.send_command_and_wait_for_prompt(command, timeout_sec=1.0))

        thread_1 = threading.Thread(target=run_command, args=("-thread-info",))
        thread_1.start()

        while len(controller.io_manager.stdin.writes) < 1:
            time.sleep(0.01)

        thread_2 = threading.Thread(target=run_command, args=("-stack-info-frame",))
        thread_2.start()

        time.sleep(0.05)
        assert controller.io_manager.stdin.writes == [b"1000-thread-info\n"]

        controller._release_first.set()
        thread_1.join()
        thread_2.join()

        assert controller.io_manager.stdin.writes == [
            b"1000-thread-info\n",
            b"1001-stack-info-frame\n",
        ]
        assert len(results) == 2
