"""Unit tests for the extracted SessionService layer."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from gdb_mcp.domain import OperationError, OperationSuccess, SessionStartupInfo
from gdb_mcp.session.gdb_backend import GdbMiBackend
from gdb_mcp.session.protocols import OsModuleProtocol, TimeModuleProtocol
from gdb_mcp.session.service import SessionService
from gdb_mcp.session.state import SessionState
from gdb_mcp.transport.protocols import GdbControllerFactoryProtocol, GdbControllerProtocol
from tests.support import gdb_backend

from .conftest import PromptResponseBuilder


def _os_module() -> MagicMock:
    """Build an OS double whose gdb lookup resolves and whose cwd is not queried."""

    fake_os = MagicMock()
    fake_os.environ.get.return_value = "gdb"
    return fake_os


def _service(
    *,
    controller_factory: GdbControllerFactoryProtocol | None = None,
    os_module: OsModuleProtocol | None = None,
) -> SessionService:
    """Build a SessionService with injected doubles instead of real dependencies."""

    return SessionService(
        backend=GdbMiBackend(
            controller_factory=controller_factory or MagicMock(spec=GdbControllerFactoryProtocol),
            os_module=os_module or _os_module(),
            time_module=MagicMock(spec=TimeModuleProtocol),
        )
    )


class TestSessionService:
    """Basic tests for the new session service boundary."""

    def test_initialization_uses_injected_dependencies(self) -> None:
        """SessionService should start with no controller and CREATED state."""

        service = _service()

        assert service.has_controller is False
        assert service.state is SessionState.CREATED
        assert service.config is None
        assert service.last_stop_event is None
        assert service.stop_history == ()
        assert service.command_transcript == ()
        assert service.runtime.workflow_lock is not None

    def test_start_success_uses_injected_controller_factory(
        self, prompt_response: PromptResponseBuilder
    ) -> None:
        """Startup should use the injected controller factory rather than importing one directly."""

        controller_factory = MagicMock(
            spec=GdbControllerFactoryProtocol,
            return_value=MagicMock(spec=GdbControllerProtocol),
        )
        service = _service(controller_factory=controller_factory)

        with (
            patch.object(
                gdb_backend(service)._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(),
            ),
            patch.object(
                gdb_backend(service)._lifecycle, "_probe_target_loaded", return_value=True
            ),
        ):
            result = service.start(program="/bin/ls")

        assert isinstance(result, OperationSuccess)
        assert isinstance(result.value, SessionStartupInfo)
        assert result.value.message == "GDB session started"
        assert result.value.program == "/bin/ls"
        assert result.value.target_loaded is True
        assert service.state is SessionState.READY
        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi", "/bin/ls"],
            time_to_check_for_additional_output_sec=1.0,
        )

    def test_start_with_working_dir_uses_process_cwd_without_chdir(
        self, prompt_response: PromptResponseBuilder
    ) -> None:
        """working_dir should be forwarded into process startup instead of mutating global cwd."""

        controller_factory = MagicMock(
            spec=GdbControllerFactoryProtocol,
            return_value=MagicMock(spec=GdbControllerProtocol),
        )
        fake_os = _os_module()
        fake_os.path.isdir.return_value = True
        service = _service(controller_factory=controller_factory, os_module=fake_os)

        with (
            patch.object(
                gdb_backend(service)._command_runner,
                "send_command_and_wait_for_prompt",
                return_value=prompt_response(),
            ),
            patch.object(
                gdb_backend(service)._lifecycle, "_probe_target_loaded", return_value=True
            ),
        ):
            result = service.start(program="/bin/ls", working_dir="/tmp/work")

        assert isinstance(result, OperationSuccess)
        assert result.value.program == "/bin/ls"
        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi", "/bin/ls"],
            time_to_check_for_additional_output_sec=1.0,
            cwd="/tmp/work",
        )
        fake_os.chdir.assert_not_called()

    def test_start_serializes_concurrent_calls(self) -> None:
        """Concurrent start attempts should not create multiple controllers."""

        controller_factory_calls = 0
        controller_factory_lock = threading.Lock()
        start_barrier = threading.Barrier(3)

        def controller_factory(**kwargs: object) -> GdbControllerProtocol:
            del kwargs
            nonlocal controller_factory_calls
            with controller_factory_lock:
                controller_factory_calls += 1
            # Widen the race window so a missing workflow lock would create two controllers.
            time.sleep(0.05)
            return MagicMock()

        fake_os = MagicMock()
        fake_os.environ.get.return_value = "gdb"
        service = _service(controller_factory=controller_factory, os_module=fake_os)
        results: list[OperationSuccess[SessionStartupInfo] | OperationError] = []

        def run_start() -> None:
            start_barrier.wait()
            results.append(service.start(program="/bin/ls"))

        with patch.object(
            gdb_backend(service)._command_runner,
            "send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            thread_1 = threading.Thread(target=run_start)
            thread_2 = threading.Thread(target=run_start)
            with patch.object(
                gdb_backend(service)._lifecycle, "_probe_target_loaded", return_value=True
            ):
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
