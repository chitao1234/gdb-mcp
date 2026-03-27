"""Unit tests for the extracted SessionService layer."""

import threading
import time
from unittest.mock import MagicMock, patch

from gdb_mcp.domain import OperationError, OperationSuccess, SessionStartInfo
from gdb_mcp.session.service import SessionService, SessionState


class TestSessionService:
    """Basic tests for the new session service boundary."""

    def test_initialization_uses_injected_dependencies(self):
        """SessionService should start with no controller and CREATED state."""

        service = SessionService(
            controller_factory=MagicMock(),
            os_module=MagicMock(),
            time_module=MagicMock(),
        )

        assert service.controller is None
        assert service.state is SessionState.CREATED
        assert service.config is None

    def test_start_success_uses_injected_controller_factory(self):
        """Startup should use the injected controller factory rather than importing one directly."""

        controller_factory = MagicMock(return_value=MagicMock())
        fake_os = MagicMock()
        fake_os.environ.get.return_value = "gdb"

        service = SessionService(
            controller_factory=controller_factory,
            os_module=fake_os,
            time_module=MagicMock(),
        )

        with patch.object(
            service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            with patch.object(service._lifecycle, "_probe_target_loaded", return_value=True):
                result = service.start(program="/bin/ls")

        assert isinstance(result, OperationSuccess)
        assert isinstance(result.value, SessionStartInfo)
        assert result.value.message == "GDB session started"
        assert result.value.program == "/bin/ls"
        assert result.value.target_loaded is True
        assert service.state is SessionState.READY
        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi", "/bin/ls"],
            time_to_check_for_additional_output_sec=1.0,
        )

    def test_start_with_working_dir_uses_process_cwd_without_chdir(self):
        """working_dir should be forwarded into process startup instead of mutating global cwd."""

        controller_factory = MagicMock(return_value=MagicMock())
        fake_os = MagicMock()
        fake_os.environ.get.return_value = "gdb"
        fake_os.path.isdir.return_value = True

        service = SessionService(
            controller_factory=controller_factory,
            os_module=fake_os,
            time_module=MagicMock(),
        )

        with patch.object(
            service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            with patch.object(service._lifecycle, "_probe_target_loaded", return_value=True):
                result = service.start(program="/bin/ls", working_dir="/tmp/work")

        assert isinstance(result, OperationSuccess)
        assert result.value.program == "/bin/ls"
        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi", "/bin/ls"],
            time_to_check_for_additional_output_sec=1.0,
            cwd="/tmp/work",
        )
        fake_os.chdir.assert_not_called()

    def test_start_serializes_concurrent_calls(self):
        """Concurrent start attempts should not create multiple controllers."""

        controller_factory_calls = 0
        controller_factory_lock = threading.Lock()
        start_barrier = threading.Barrier(3)

        def controller_factory(**kwargs):
            del kwargs
            nonlocal controller_factory_calls
            with controller_factory_lock:
                controller_factory_calls += 1
            time.sleep(0.05)
            return MagicMock()

        fake_os = MagicMock()
        fake_os.environ.get.return_value = "gdb"

        service = SessionService(
            controller_factory=controller_factory,
            os_module=fake_os,
            time_module=MagicMock(),
        )
        results: list[OperationSuccess[SessionStartInfo] | OperationError] = []

        def run_start() -> None:
            start_barrier.wait()
            results.append(service.start(program="/bin/ls"))

        with patch.object(
            service._command_runner,
            "send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            thread_1 = threading.Thread(target=run_start)
            thread_2 = threading.Thread(target=run_start)
            with patch.object(service._lifecycle, "_probe_target_loaded", return_value=True):
                thread_1.start()
                thread_2.start()
                start_barrier.wait()
                thread_1.join()
                thread_2.join()

        assert controller_factory_calls == 1
        assert len(results) == 2
        assert sum(isinstance(result, OperationSuccess) for result in results) == 1
        assert sum(isinstance(result, OperationError) for result in results) == 1
        errors = [result for result in results if isinstance(result, OperationError)]
        assert len(errors) == 1
        assert "already running" in errors[0].message.lower()
