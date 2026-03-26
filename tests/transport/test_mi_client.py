"""Unit tests for the low-level MI transport client."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from gdb_mcp.transport.mi_client import MiClient
from gdb_mcp.transport.mi_commands import escape_mi_string, is_cli_command, wrap_cli_command


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

    def test_wrap_cli_command(self):
        """CLI commands should be wrapped with interpreter-exec."""

        wrapped = wrap_cli_command('print "hello"')
        assert wrapped == '-interpreter-exec console "print \\"hello\\""'


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
        assert result.async_notifications == [{"type": "notify", "token": 999, "payload": {"msg": "old-command"}}]

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
