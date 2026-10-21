"""Tests for the repeat-until-failure campaign runner."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

from gdb_mcp.contracts import DEBUGGER_GDB, SessionState
from gdb_mcp.domain import (
    BackendData,
    BatchExecutionInfo,
    BatchStepResult,
    CaptureBundleInfo,
    CommandExecutionInfo,
    MemoryCaptureRange,
    OperationError,
    OperationSuccess,
    RunUntilFailureInfo,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
    StartupDiagnostics,
    StopEvent,
)
from gdb_mcp.session.campaign import (
    RunUntilFailureCaptureRequest,
    RunUntilFailureRequest,
    RunUntilFailureService,
)
from gdb_mcp.session.service import SessionService
from gdb_mcp.session.workflow import BatchStepTemplate


def _fake_session(
    *,
    status: SessionStatusSnapshot | None = None,
    stop_event: StopEvent | None = None,
    capture: OperationSuccess[CaptureBundleInfo] | None = None,
) -> Mock:
    """Build a session double with the surface the campaign runner drives."""

    session = Mock(spec=SessionService)
    session.start.return_value = OperationSuccess(
        SessionStartupInfo(
            message="started",
            diagnostics=StartupDiagnostics(
                environment_results=[],
                initialization_results=[],
                backend_data=BackendData(DEBUGGER_GDB, "", {}),
            ),
        )
    )
    session.run.return_value = OperationSuccess(
        CommandExecutionInfo(
            command="-exec-run",
            backend_data=BackendData(debugger=DEBUGGER_GDB, console_output="", data={}),
        )
    )
    if status is not None:
        session.get_status.return_value = OperationSuccess(status)
    session.last_stop_event = stop_event
    if capture is not None:
        session.capture_bundle.return_value = capture
    session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
    session.runtime.workflow_lock = MagicMock()
    return session


def _bundle() -> OperationSuccess[CaptureBundleInfo]:
    return OperationSuccess(
        CaptureBundleInfo(
            message="bundle",
            bundle_dir="/tmp/bundle",
            bundle_name="bundle",
            manifest_path="/tmp/bundle/manifest.json",
            artifacts=[],
        )
    )


class TestRunUntilFailureService:
    """Test campaign loop behavior independently of the MCP handler layer."""

    def test_startup_failure_keeps_the_original_error_on_its_iteration(self) -> None:
        session = _fake_session()
        session.start.return_value = OperationError(message="startup failed", code="not_started")

        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(max_iterations=3)
        )

        assert len(result.value.iterations) == 1
        iteration = result.value.iterations[0]
        assert iteration.phase == "startup"
        assert iteration.trigger == "startup_error"
        assert iteration.result == {
            "status": "error",
            "code": "not_started",
            "message": "startup failed",
        }
        assert iteration.execution_state is None
        session.run.assert_not_called()

    def test_matches_default_signal_stop_and_captures_bundle(self) -> None:
        """Signal stops should match the default failure criteria and trigger capture."""

        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="paused",
                stop_reason="signal-received",
            ),
            stop_event=StopEvent(execution_state="paused", reason="signal-received"),
            capture=_bundle(),
        )

        service = RunUntilFailureService(lambda: session)
        result = service.run_until_failure(
            RunUntilFailureRequest(
                program="/tmp/a.out",
                max_iterations=3,
                capture=RunUntilFailureCaptureRequest(
                    output_dir="/tmp",
                    bundle_name_prefix="failure",
                    memory_ranges=(MemoryCaptureRange(address="&value", count=4),),
                ),
            )
        )

        assert isinstance(result, OperationSuccess)
        assert isinstance(result.value, RunUntilFailureInfo)
        assert result.value.iterations[-1].matched_failure is True
        assert result.value.iterations[-1].iteration == 1
        assert result.value.iterations[-1].trigger == "stop_reason:signal-received"
        assert result.value.iterations[-1].phase == "run"
        assert result.value.iterations[-1].result["status"] == "success"
        assert result.value.capture_bundle is not None
        assert result.value.capture_bundle.bundle_name == "bundle"
        session.capture_bundle.assert_called_once()
        assert session.capture_bundle.call_args.kwargs["memory_ranges"] == [
            MemoryCaptureRange(address="&value", count=4)
        ]
        session.stop.assert_called_once()

    def test_returns_success_when_no_failure_matches(self) -> None:
        """Campaigns should complete cleanly when no predicate matches."""

        exited = SessionStatusSnapshot(
            debugger_alive=True,
            target_loaded=True,
            session_state=SessionState.READY,
            execution_state="exited",
            stop_reason="exited-normally",
            exit_code=0,
        )
        stop_event = StopEvent(
            execution_state="exited",
            reason="exited-normally",
            exit_code=0,
        )
        session_1 = _fake_session(status=exited, stop_event=stop_event)
        session_2 = _fake_session(status=exited, stop_event=stop_event)

        service = RunUntilFailureService(Mock(side_effect=[session_1, session_2]))
        result = service.run_until_failure(
            RunUntilFailureRequest(
                program="/tmp/a.out",
                max_iterations=2,
                capture=RunUntilFailureCaptureRequest(enabled=False),
            )
        )

        assert isinstance(result, OperationSuccess)
        assert not any(item.matched_failure for item in result.value.iterations)
        assert len(result.value.iterations) == 2
        assert result.value.iterations[-1].execution_state == "exited"
        assert result.value.iterations[-1].stop_reason == "exited-normally"
        assert result.value.iterations[-1].exit_code == 0
        session_1.stop.assert_called_once()
        session_2.stop.assert_called_once()

    def test_setup_batch_failure_stops_before_run(self) -> None:
        """Setup-step failures should stop the campaign before gdb_run executes."""

        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="not_started",
            ),
        )
        session.execute_batch_templates.return_value = OperationSuccess(
            BatchExecutionInfo(
                steps=[
                    BatchStepResult(
                        index=0,
                        tool="gdb_execute_command",
                        result={"status": "error", "code": "error", "message": "setup failed"},
                    )
                ],
                requested_steps=1,
            )
        )

        service = RunUntilFailureService(lambda: session)
        result = service.run_until_failure(
            RunUntilFailureRequest(
                program="/tmp/a.out",
                max_iterations=5,
                setup_steps=(
                    BatchStepTemplate(
                        tool="gdb_get_status",
                        execute=lambda _: OperationSuccess(SessionMessage(message="ok")),
                    ),
                ),
                capture=RunUntilFailureCaptureRequest(enabled=False),
            )
        )

        assert isinstance(result, OperationSuccess)
        assert result.value.iterations[-1].matched_failure is True
        assert result.value.iterations[-1].trigger == "setup_error"
        assert result.value.iterations[-1].phase == "setup"
        assert result.value.iterations[-1].result["status"] == "success"
        session.run.assert_not_called()
