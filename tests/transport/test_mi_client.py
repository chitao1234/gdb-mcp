"""Unit tests for the low-level MI transport client."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from unittest.mock import MagicMock, patch

from gdb_mcp.transport.mi_client import MiClient
from gdb_mcp.transport.mi_models import MiRecord, MiTransportResponse
from gdb_mcp.transport.protocols import (
    ControllerIoManagerProtocol,
    GdbControllerProtocol,
    GdbProcessProtocol,
)

# Locks expose no "waiter is blocked" callback, so give a contending thread a
# moment to reach the lock before asserting that it has not written yet.
_LOCK_CONTENTION_GRACE_SEC = 0.05


class _FakeStdin:
    """Capture writes and optionally signal when one has been recorded."""

    def __init__(self, write_event: threading.Event | None = None) -> None:
        self.writes: list[bytes] = []
        self.flush_count = 0
        self._write_event = write_event

    def write(self, data: bytes) -> int | None:
        self.writes.append(data)
        if self._write_event is not None:
            self._write_event.set()
        return None

    def flush(self) -> None:
        self.flush_count += 1


class _FakeIoManager(ControllerIoManagerProtocol):
    """Typed stand-in for pygdbmi's IO manager."""

    def __init__(self, write_event: threading.Event | None = None) -> None:
        self.stdin = _FakeStdin(write_event)


class _FakeProcess(GdbProcessProtocol):
    """Typed stand-in for the GDB child process."""

    def __init__(self) -> None:
        self.pid = 4242

    def poll(self) -> int | None:
        return None


class _FakeController(GdbControllerProtocol):
    """Scripted controller that returns preloaded response batches."""

    def __init__(self, responses: list[list[MiRecord]]) -> None:
        self.io_manager = _FakeIoManager()
        self._responses = list(responses)
        self.exit_called = False
        self.gdb_process = _FakeProcess()

    def get_gdb_response(
        self, *, timeout_sec: float, raise_error_on_timeout: bool
    ) -> list[MiRecord]:
        if self._responses:
            return self._responses.pop(0)
        return []

    def exit(self) -> None:
        self.exit_called = True


class ContinuousAsyncController(GdbControllerProtocol):
    """Return one scripted batch, then endless async notifications."""

    def __init__(self, first_response: list[MiRecord]) -> None:
        self.io_manager = _FakeIoManager()
        self.gdb_process = _FakeProcess()
        self._first_response = first_response
        self._step = 0

    def get_gdb_response(
        self, *, timeout_sec: float, raise_error_on_timeout: bool
    ) -> list[MiRecord]:
        del timeout_sec, raise_error_on_timeout
        self._step += 1
        if self._step == 1:
            return self._first_response
        return [{"type": "notify", "message": "library-loaded", "payload": {"id": self._step}}]

    def exit(self) -> None:
        pass


class BlockingController(GdbControllerProtocol):
    """Controller whose response depends on the number of writes so far.

    The handler receives the current write count and may block on test-owned
    events before returning records.
    """

    def __init__(
        self,
        handler: Callable[[int], list[MiRecord]],
        *,
        write_event: threading.Event | None = None,
    ) -> None:
        self.io_manager = _FakeIoManager(write_event)
        self.gdb_process = _FakeProcess()
        self._handler = handler
        self.exit_called = False

    def get_gdb_response(
        self, *, timeout_sec: float, raise_error_on_timeout: bool
    ) -> list[MiRecord]:
        del timeout_sec, raise_error_on_timeout
        return self._handler(len(self.io_manager.stdin.writes))

    def exit(self) -> None:
        self.exit_called = True


class TestMiClient:
    """Test the extracted GDB/MI command transport behavior."""

    def _make_client(self, controller: GdbControllerProtocol) -> MiClient:
        client = MiClient(
            controller_factory=MagicMock(return_value=controller),
            initial_command_token=1000,
            poll_timeout_sec=0.01,
        )
        client.start(
            command=["gdb", "--quiet", "--interpreter=mi"],
            time_to_check_for_additional_output_sec=1.0,
        )
        return client

    def test_start_uses_controller_factory(self) -> None:
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

    def test_start_forwards_cwd_when_provided(self) -> None:
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

    def test_is_alive_serializes_with_the_command_lock(self) -> None:
        """Liveness checks should take the command lock instead of racing teardown."""

        controller = _FakeController([])
        client = self._make_client(controller)
        finished = threading.Event()

        def check() -> None:
            client.is_alive()
            finished.set()

        with client._command_lock:
            worker = threading.Thread(target=check)
            worker.start()
            assert not finished.wait(timeout=0.2)

        worker.join(timeout=1.0)
        assert finished.is_set()

    def test_read_initial_output_drains_startup_records(self) -> None:
        """Startup output emitted before the first command should be retrievable."""

        controller = _FakeController(
            [[{"type": "log", "payload": "/missing/program: No such file or directory.\n"}], []]
        )
        client = self._make_client(controller)

        result = client.read_initial_output(timeout_sec=0.1)

        assert result == [
            {"type": "log", "payload": "/missing/program: No such file or directory.\n"}
        ]

    def test_send_command_collects_result_and_async_notifications(self) -> None:
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

    def test_send_command_does_not_complete_exec_on_unrelated_tokenless_notify(self) -> None:
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

    def test_send_command_waits_for_stop_after_running_result(self) -> None:
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

    def test_send_command_treats_tokenless_stopped_as_exec_completion(self) -> None:
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

    def test_send_command_treats_tokenless_stopped_as_cli_run_completion(self) -> None:
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

    def test_send_command_detects_fatal_error_and_cleans_up(self) -> None:
        """Fatal GDB output should stop the client and return a fatal response."""

        controller = _FakeController(
            [[{"type": "console", "payload": "A fatal error internal to GDB has been detected"}]]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-gdb-version", timeout_sec=1.0)

        assert result.fatal is True
        error = result.error
        assert error is not None
        assert "fatal error" in error.lower()
        assert client.controller is None
        assert controller.exit_called is True

    def test_send_command_cleans_up_when_gdb_process_exits(self) -> None:
        """Unexpected child exit should be treated as a terminal transport failure."""

        controller = _FakeController([[]])
        client = self._make_client(controller)

        with (
            patch("gdb_mcp.transport.mi_client._LIVENESS_CHECK_INTERVAL_SEC", 0.0),
            patch.object(client, "_is_alive_unlocked", return_value=False),
            patch.object(client, "_extract_exit_code", return_value=-9),
        ):
            result = client.send_command_and_wait_for_prompt("-gdb-version", timeout_sec=0.1)

        assert result.fatal is True
        error = result.error
        assert error is not None
        assert "exited unexpectedly" in error
        assert client.controller is None
        assert controller.exit_called is True

    def test_send_command_uses_absolute_timeout_with_async_traffic(self) -> None:
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
            def __init__(self) -> None:
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

    def test_send_command_completes_after_result_even_with_continuous_async_traffic(self) -> None:
        """Non-running commands should return once their result record is parsed."""

        controller = ContinuousAsyncController(
            [
                {
                    "type": "result",
                    "token": 1000,
                    "message": "done",
                    "payload": {"ok": True},
                },
                {"type": "notify", "message": "thread-created", "payload": {"id": "2"}},
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-thread-info", timeout_sec=0.02)

        assert result.timed_out is False
        assert result.error is None
        assert result.command_responses == [
            {"type": "result", "token": 1000, "message": "done", "payload": {"ok": True}}
        ]
        assert result.async_notifications == [
            {"type": "notify", "message": "thread-created", "payload": {"id": "2"}}
        ]

    def test_send_command_completes_running_command_after_stop_under_async_noise(self) -> None:
        """Running commands should return once stopped is observed, even if async noise continues."""

        controller = ContinuousAsyncController(
            [
                {"type": "result", "token": 1000, "message": "running", "payload": None},
                {
                    "type": "notify",
                    "message": "stopped",
                    "payload": {"reason": "breakpoint-hit"},
                },
                {"type": "notify", "message": "thread-created", "payload": {"id": "2"}},
            ]
        )
        client = self._make_client(controller)

        result = client.send_command_and_wait_for_prompt("-exec-next", timeout_sec=0.02)

        assert result.timed_out is False
        assert result.error is None
        assert [record["type"] for record in result.command_responses] == ["result", "notify"]
        assert result.command_responses[0]["message"] == "running"
        assert result.command_responses[1]["message"] == "stopped"
        assert result.async_notifications == [
            {"type": "notify", "message": "thread-created", "payload": {"id": "2"}}
        ]

    def test_interrupt_rejects_when_command_is_in_progress(self) -> None:
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

    def test_wait_for_stop_returns_stopped_notification(self) -> None:
        """Passive stop waits should return once a stopped notification arrives."""

        controller = _FakeController(
            [[{"type": "notify", "message": "stopped", "payload": {"reason": "fork"}}]]
        )
        client = self._make_client(controller)

        result = client.wait_for_stop(timeout_sec=1.0)

        assert result.error is None
        assert result.timed_out is False
        assert result.command_responses[0]["message"] == "stopped"

    def test_wait_for_stop_rejects_when_command_is_in_progress(self) -> None:
        """Passive stop waits should not race another in-flight command."""

        controller = _FakeController([])
        client = self._make_client(controller)

        assert client._command_lock.acquire(blocking=False) is True
        try:
            result = client.wait_for_stop(timeout_sec=1.0)
        finally:
            client._command_lock.release()

        assert result.error == "Cannot wait for stop while another command is in progress"

    def test_exit_waits_for_inflight_command_before_clearing_controller(self) -> None:
        """Controller teardown should block until the active command finishes reading."""

        release_response = threading.Event()
        returned_result = False

        def respond(_write_count: int) -> list[MiRecord]:
            nonlocal returned_result
            if not returned_result:
                release_response.wait(timeout=1.0)
                returned_result = True
                return [{"type": "result", "token": 1000, "message": "done", "payload": None}]
            return []

        first_write = threading.Event()
        controller = BlockingController(respond, write_event=first_write)
        client = self._make_client(controller)
        command_thread = threading.Thread(
            target=client.send_command_and_wait_for_prompt,
            args=("-thread-info",),
            kwargs={"timeout_sec": 1.0},
        )
        command_thread.start()

        assert first_write.wait(timeout=1.0)

        exit_attempted = threading.Event()

        def run_exit() -> None:
            exit_attempted.set()
            client.exit()

        exit_thread = threading.Thread(target=run_exit)
        exit_thread.start()
        assert exit_attempted.wait(timeout=1.0)

        time.sleep(_LOCK_CONTENTION_GRACE_SEC)
        assert exit_thread.is_alive() is True
        assert controller.exit_called is False
        assert client.controller is controller

        release_response.set()
        command_thread.join()
        exit_thread.join()

        assert controller.exit_called is True
        assert client.controller is None

    def test_send_command_serializes_same_session_calls(self) -> None:
        """Concurrent commands on one client should not interleave writes."""

        release_first = threading.Event()
        first_command_done = False
        second_command_done = False

        def respond(write_count: int) -> list[MiRecord]:
            nonlocal first_command_done, second_command_done
            if write_count == 1:
                if first_command_done:
                    return []
                release_first.wait(timeout=1.0)
                first_command_done = True
                return [{"type": "result", "token": 1000, "message": "done", "payload": None}]
            if write_count == 2 and not second_command_done:
                second_command_done = True
                return [{"type": "result", "token": 1001, "message": "done", "payload": None}]
            return []

        first_write = threading.Event()
        controller = BlockingController(respond, write_event=first_write)
        client = self._make_client(controller)
        results: list[MiTransportResponse] = []

        def run_command(command: str) -> None:
            results.append(client.send_command_and_wait_for_prompt(command, timeout_sec=1.0))

        thread_1 = threading.Thread(target=run_command, args=("-thread-info",))
        thread_1.start()

        assert first_write.wait(timeout=1.0)

        second_started = threading.Event()

        def run_second_command() -> None:
            second_started.set()
            run_command("-stack-info-frame")

        thread_2 = threading.Thread(target=run_second_command)
        thread_2.start()
        assert second_started.wait(timeout=1.0)

        time.sleep(_LOCK_CONTENTION_GRACE_SEC)
        assert controller.io_manager.stdin.writes == [b"1000-thread-info\n"]

        release_first.set()
        thread_1.join()
        thread_2.join()

        assert controller.io_manager.stdin.writes == [
            b"1000-thread-info\n",
            b"1001-stack-info-frame\n",
        ]
        assert len(results) == 2
