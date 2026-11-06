"""Tests for the repeat-until-failure campaign runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

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
from gdb_mcp.mcp.serializer import serialize_result
from gdb_mcp.session.campaign import (
    RunUntilFailureCaptureRequest,
    RunUntilFailureCriteria,
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

    @pytest.mark.parametrize(
        ("message", "on_timeout", "on_error", "expected_trigger"),
        [
            ("Timeout waiting for command response", True, True, "timeout"),
            ("unrecognized debugger failure", True, True, "run_error"),
            ("Timeout waiting for command response", False, True, "run_error"),
            ("Timeout waiting for command response", True, False, "timeout"),
            ("Timeout waiting for command response", False, False, None),
            ("Timed out waiting for RPCServer.Command", True, False, "timeout"),
        ],
    )
    def test_run_errors_respect_failure_predicates_and_keep_the_debugger_payload(
        self,
        message: str,
        on_timeout: bool,
        on_error: bool,
        expected_trigger: str | None,
    ) -> None:
        status = SessionStatusSnapshot(
            debugger_alive=True,
            target_loaded=True,
            session_state=SessionState.READY,
            execution_state="exited",
        )
        session = _fake_session(status=status)
        session.run.return_value = OperationError(
            message=message,
            details={"future-field": {"nested-key": "kept"}},
        )
        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(
                max_iterations=1,
                failure=RunUntilFailureCriteria(
                    failure_on_timeout=on_timeout, failure_on_error=on_error
                ),
                capture=RunUntilFailureCaptureRequest(enabled=False),
            )
        )

        iteration = result.value.iterations[0]
        assert iteration.trigger == expected_trigger
        assert iteration.matched_failure is (expected_trigger is not None)
        assert iteration.result["status"] == "error"
        assert iteration.result["message"] == message
        assert iteration.result["details"] == {"future-field": {"nested-key": "kept"}}

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

    @pytest.mark.parametrize("interrupt_fails", [False, True])
    def test_timeout_stops_the_inferior_before_capture(self, interrupt_fails: bool) -> None:
        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="running",
            ),
            capture=_bundle(),
        )
        session.run.return_value = OperationError(
            message="Deadline reached", code="command_timeout"
        )
        session.interrupt.return_value = (
            OperationError(message="Interrupt timed out")
            if interrupt_fails
            else OperationSuccess(SessionMessage(message="stopped"))
        )
        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(run_timeout_sec=1, max_iterations=2)
        )

        assert result.value.iterations[0].trigger == "timeout"
        assert len(result.value.iterations) == 1
        session.interrupt.assert_called_once()
        if interrupt_fails:
            session.capture_bundle.assert_not_called()
            assert result.value.capture_error is not None
            assert result.warnings
        else:
            calls = [call[0] for call in session.method_calls]
            assert calls.index("interrupt") < calls.index("capture_bundle")
            assert result.value.capture_bundle is not None
        session.stop.assert_called_once()

    def test_large_campaign_response_fits_the_clients_transport_buffer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "gdb_mcp.session.campaign_history.tempfile.mkdtemp", lambda **_: str(tmp_path)
        )
        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="exited",
                stop_reason="exited-normally",
                exit_code=0,
            )
        )
        output = 'output with escapes: \\"\n雪' * 12000
        session.run.return_value = OperationSuccess(
            CommandExecutionInfo(
                command="-exec-run",
                backend_data=BackendData(debugger=DEBUGGER_GDB, console_output=output, data={}),
            )
        )
        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(
                max_iterations=110,
                capture=RunUntilFailureCaptureRequest(enabled=False),
            )
        )

        wire = serialize_result(result).model_dump_json().encode()
        assert len(wire) < 10 * 1024 * 1024
        assert len(result.value.iterations) == 110
        assert result.value.iterations_omitted == 0
        assert result.value.iterations[0].iteration == 1
        assert result.value.iterations[-1].iteration == 110
        assert all(item.result_truncated for item in result.value.iterations)
        assert result.value.iterations_path is not None
        full_iterations = [
            json.loads(line) for line in Path(result.value.iterations_path).read_text().splitlines()
        ]
        assert len(full_iterations) == 110
        assert full_iterations[0]["iteration"] == 1
        assert full_iterations[-1]["result"]["result"]["backend_data"]["console_output"] == output

    def test_small_iteration_history_spills_when_the_retention_limit_is_reached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "gdb_mcp.session.campaign_history.tempfile.mkdtemp", lambda **_: str(tmp_path)
        )
        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="exited",
            )
        )
        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(
                max_iterations=1001, capture=RunUntilFailureCaptureRequest(enabled=False)
            )
        )
        assert result.value.iterations_omitted == 1
        assert result.value.iterations_path is not None
        assert len(result.value.iterations) == 1000
        assert len(Path(result.value.iterations_path).read_text().splitlines()) == 1001
        assert not any(item.result_truncated for item in result.value.iterations)

    @pytest.mark.parametrize(("count", "output_size"), [(1, 64 * 1024), (1000, 1024)])
    def test_larger_results_and_longer_history_stay_inline_when_they_fit(
        self, count: int, output_size: int
    ) -> None:
        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="exited",
            )
        )
        session.run.return_value = OperationSuccess(
            CommandExecutionInfo(
                command="-exec-run",
                backend_data=BackendData(
                    debugger=DEBUGGER_GDB, console_output="x" * output_size, data={}
                ),
            )
        )
        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(
                max_iterations=count, capture=RunUntilFailureCaptureRequest(enabled=False)
            )
        )

        assert len(result.value.iterations) == count
        assert result.value.iterations_omitted == 0
        assert result.value.iterations_path is None
        assert not any(item.result_truncated for item in result.value.iterations)
        assert len(serialize_result(result).model_dump_json().encode()) < 10 * 1024 * 1024

    @pytest.mark.parametrize("output", ["x" * (64 * 1024), '\\"\n雪' * 12000])
    def test_aggregate_wire_budget_spills_history_before_the_client_buffer_overflows(
        self, output: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "gdb_mcp.session.campaign_history.tempfile.mkdtemp", lambda **_: str(tmp_path)
        )
        session = _fake_session(
            status=SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="exited",
            )
        )
        session.run.return_value = OperationSuccess(
            CommandExecutionInfo(
                command="-exec-run",
                backend_data=BackendData(debugger=DEBUGGER_GDB, console_output=output, data={}),
            )
        )
        result = RunUntilFailureService(lambda: session).run_until_failure(
            RunUntilFailureRequest(
                max_iterations=100, capture=RunUntilFailureCaptureRequest(enabled=False)
            )
        )

        assert len(serialize_result(result).model_dump_json().encode()) < 10 * 1024 * 1024
        assert 0 < len(result.value.iterations) < 100
        assert result.value.iterations_omitted + len(result.value.iterations) == 100
        assert result.value.iterations[-1].iteration == 100
        assert not any(item.result_truncated for item in result.value.iterations)
        assert result.value.iterations_path is not None
        full_iterations = [
            json.loads(line) for line in Path(result.value.iterations_path).read_text().splitlines()
        ]
        assert len(full_iterations) == 100
        assert full_iterations[0]["iteration"] == 1
        assert full_iterations[-1]["result"]["result"]["backend_data"]["console_output"] == output

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
