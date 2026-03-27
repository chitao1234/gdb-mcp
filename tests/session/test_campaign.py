"""Tests for the repeat-until-failure campaign runner."""

from __future__ import annotations

from unittest.mock import Mock

from gdb_mcp.domain import (
    BatchExecutionInfo,
    CaptureBundleInfo,
    CommandExecutionInfo,
    MemoryCaptureRange,
    OperationSuccess,
    RunUntilFailureInfo,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
    StopEvent,
)
from gdb_mcp.session.campaign import (
    RunUntilFailureCaptureRequest,
    RunUntilFailureCriteria,
    RunUntilFailureRequest,
    RunUntilFailureService,
)
from gdb_mcp.session.workflow import BatchStepTemplate


class TestRunUntilFailureService:
    """Test campaign loop behavior independently of the MCP handler layer."""

    def test_matches_default_signal_stop_and_captures_bundle(self):
        """Signal stops should match the default failure criteria and trigger capture."""

        session = Mock()
        session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
        session.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="paused",
                stop_reason="signal-received",
            )
        )
        session.last_stop_event = StopEvent(execution_state="paused", reason="signal-received")
        session.capture_bundle.return_value = OperationSuccess(
            CaptureBundleInfo(
                message="bundle",
                bundle_dir="/tmp/bundle",
                bundle_name="bundle",
                manifest_path="/tmp/bundle/manifest.json",
                artifacts=[],
                artifact_count=0,
            )
        )
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        session.runtime.workflow_lock = Mock()
        session.runtime.workflow_lock.__enter__ = Mock(return_value=None)
        session.runtime.workflow_lock.__exit__ = Mock(return_value=None)

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
        assert result.value.matched_failure is True
        assert result.value.failure_iteration == 1
        assert result.value.trigger == "stop_reason:signal-received"
        assert result.value.capture_bundle is not None
        assert result.value.capture_bundle.bundle_name == "bundle"
        session.capture_bundle.assert_called_once()
        assert session.capture_bundle.call_args.kwargs["memory_ranges"] == [
            MemoryCaptureRange(address="&value", count=4)
        ]
        session.stop.assert_called_once()

    def test_returns_success_when_no_failure_matches(self):
        """Campaigns should complete cleanly when no predicate matches."""

        session_1 = Mock()
        session_1.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
        session_1.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        session_1.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="exited",
                stop_reason="exited-normally",
                exit_code=0,
            )
        )
        session_1.last_stop_event = StopEvent(
            execution_state="exited",
            reason="exited-normally",
            exit_code=0,
        )
        session_1.controller = object()
        session_1.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        session_1.runtime.workflow_lock = Mock()
        session_1.runtime.workflow_lock.__enter__ = Mock(return_value=None)
        session_1.runtime.workflow_lock.__exit__ = Mock(return_value=None)

        session_2 = Mock()
        session_2.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
        session_2.run.return_value = OperationSuccess(CommandExecutionInfo(command="-exec-run"))
        session_2.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="exited",
                stop_reason="exited-normally",
                exit_code=0,
            )
        )
        session_2.last_stop_event = StopEvent(
            execution_state="exited",
            reason="exited-normally",
            exit_code=0,
        )
        session_2.controller = object()
        session_2.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        session_2.runtime.workflow_lock = Mock()
        session_2.runtime.workflow_lock.__enter__ = Mock(return_value=None)
        session_2.runtime.workflow_lock.__exit__ = Mock(return_value=None)

        service = RunUntilFailureService(Mock(side_effect=[session_1, session_2]))
        result = service.run_until_failure(
            RunUntilFailureRequest(
                program="/tmp/a.out",
                max_iterations=2,
                capture=RunUntilFailureCaptureRequest(enabled=False),
            )
        )

        assert isinstance(result, OperationSuccess)
        assert result.value.matched_failure is False
        assert result.value.iterations_completed == 2
        assert result.value.failure_iteration is None
        assert result.value.execution_state == "exited"
        assert result.value.stop_reason == "exited-normally"
        assert result.value.exit_code == 0
        session_1.stop.assert_called_once()
        session_2.stop.assert_called_once()

    def test_setup_batch_failure_stops_before_run(self):
        """Setup-step failures should stop the campaign before gdb_run executes."""

        session = Mock()
        session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
        session.execute_batch_templates.return_value = OperationSuccess(
            BatchExecutionInfo(
                steps=[],
                count=1,
                completed_steps=1,
                error_count=1,
                stopped_early=True,
                failure_step_index=0,
            )
        )
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="not_started",
            )
        )
        session.last_stop_event = None
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        session.runtime.workflow_lock = Mock()
        session.runtime.workflow_lock.__enter__ = Mock(return_value=None)
        session.runtime.workflow_lock.__exit__ = Mock(return_value=None)

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
        assert result.value.matched_failure is True
        assert result.value.trigger == "setup_error"
        session.run.assert_not_called()
