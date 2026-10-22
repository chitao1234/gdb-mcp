"""Repeat-until-failure campaign helpers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern

from ..contracts import DEBUGGER_GDB
from ..domain import (
    CaptureBundleInfo,
    CommandExecutionInfo,
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    RunUntilFailureInfo,
    RunUntilFailureIterationInfo,
    SessionStatusSnapshot,
    StopEvent,
    StructuredPayload,
    result_to_envelope,
)
from .locking import session_workflow_context
from .service import SessionService
from .workflow import BatchStepTemplate

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RunUntilFailureCriteria:
    """Failure predicates evaluated after each iteration."""

    failure_on_error: bool = True
    failure_on_timeout: bool = True
    stop_reasons: tuple[str, ...] = ("signal-received", "exited-signalled")
    execution_states: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()
    result_text_regex: str | None = None


@dataclass(slots=True, frozen=True)
class RunUntilFailureCaptureRequest:
    """Bundle-capture configuration used only for the matching iteration."""

    enabled: bool = True
    output_dir: str | None = None
    bundle_name_prefix: str | None = None
    bundle_name: str | None = None
    expressions: tuple[str, ...] = ()
    memory_ranges: tuple[MemoryCaptureRange, ...] = ()
    max_frames: int = 100
    include_threads: bool = True
    include_backtraces: bool = True
    include_frame: bool = True
    include_variables: bool = True
    include_registers: bool = True
    include_transcript: bool = True
    include_stop_history: bool = True


@dataclass(slots=True, frozen=True)
class RunUntilFailureRequest:
    """Validated campaign configuration for the repeat-until-failure runner."""

    program: str | None = None
    debugger: str = DEBUGGER_GDB
    debugger_args: tuple[str, ...] = ()
    rr_replay: str | None = None
    args: tuple[str, ...] = ()
    init_commands: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    debugger_path: str | None = None
    working_dir: str | None = None
    core: str | None = None
    setup_steps: tuple[BatchStepTemplate, ...] = ()
    run_args: tuple[str, ...] = ()
    run_timeout_sec: int = 30
    max_iterations: int = 1
    failure: RunUntilFailureCriteria = field(default_factory=RunUntilFailureCriteria)
    capture: RunUntilFailureCaptureRequest = field(default_factory=RunUntilFailureCaptureRequest)


class RunUntilFailureService:
    """Run repeated fresh-session attempts until a failure predicate matches."""

    def __init__(self, session_factory: Callable[[], SessionService]) -> None:
        self._session_factory = session_factory

    def run_until_failure(
        self,
        request: RunUntilFailureRequest,
    ) -> OperationSuccess[RunUntilFailureInfo]:
        """Execute fresh-session attempts until a failure predicate matches or attempts run out."""

        compiled_regex = (
            re.compile(request.failure.result_text_regex)
            if request.failure.result_text_regex is not None
            else None
        )

        iteration_summaries: list[RunUntilFailureIterationInfo] = []
        matched_iteration: RunUntilFailureIterationInfo | None = None
        capture_bundle: CaptureBundleInfo | None = None
        capture_error: str | None = None

        for iteration in range(1, request.max_iterations + 1):
            session = self._session_factory()

            try:
                startup_result = session.start(
                    program=request.program,
                    debugger=request.debugger,
                    args=list(request.args) or None,
                    init_commands=list(request.init_commands) or None,
                    env=dict(request.env) or None,
                    debugger_path=request.debugger_path,
                    debugger_args=list(request.debugger_args) or None,
                    working_dir=request.working_dir,
                    core=request.core,
                    rr_replay=request.rr_replay,
                )
                if isinstance(startup_result, OperationError):
                    matched_iteration = RunUntilFailureIterationInfo(
                        iteration=iteration,
                        phase="startup",
                        result=result_to_envelope(startup_result),
                        matched_failure=True,
                        trigger="startup_error",
                    )
                    iteration_summaries.append(matched_iteration)
                    break

                if request.setup_steps:
                    setup_result = session.execute_batch_templates(
                        request.setup_steps,
                        fail_fast=True,
                        capture_stop_events=True,
                    )
                    if setup_result.value.has_errors:
                        setup_status = self._status_snapshot(session)
                        matched_iteration = RunUntilFailureIterationInfo(
                            iteration=iteration,
                            phase="setup",
                            result=result_to_envelope(setup_result),
                            trigger="setup_error",
                            matched_failure=True,
                            execution_state=setup_status.execution_state,
                            stop_reason=setup_status.stop_reason,
                            stop_kind=setup_status.stop_kind,
                            exit_code=setup_status.exit_code,
                        )
                        iteration_summaries.append(matched_iteration)
                        break

                run_result = session.run(
                    args=list(request.run_args) or None,
                    timeout_sec=request.run_timeout_sec,
                    wait_for_stop=True,
                )
                run_payload = result_to_envelope(run_result)
                status_snapshot = self._status_snapshot(session)

                matched, trigger = self._matches_failure(
                    request.failure,
                    result=run_result,
                    payload=run_payload,
                    status=status_snapshot,
                    stop_event=session.last_stop_event,
                    compiled_regex=compiled_regex,
                )

                if matched and request.capture.enabled:
                    capture_result = session.capture_bundle(
                        output_dir=request.capture.output_dir,
                        bundle_name=self._bundle_name(
                            request.capture.bundle_name,
                            request.capture.bundle_name_prefix,
                            iteration,
                        ),
                        expressions=list(request.capture.expressions),
                        memory_ranges=list(request.capture.memory_ranges),
                        max_frames=request.capture.max_frames,
                        include_threads=request.capture.include_threads,
                        include_backtraces=request.capture.include_backtraces,
                        include_frame=request.capture.include_frame,
                        include_variables=request.capture.include_variables,
                        include_registers=request.capture.include_registers,
                        include_transcript=request.capture.include_transcript,
                        include_stop_history=request.capture.include_stop_history,
                    )
                    if isinstance(capture_result, OperationSuccess):
                        capture_bundle = capture_result.value
                    else:
                        capture_error = capture_result.message

                iteration_summary = RunUntilFailureIterationInfo(
                    iteration=iteration,
                    phase="run",
                    result=run_payload,
                    execution_state=status_snapshot.execution_state,
                    stop_reason=status_snapshot.stop_reason,
                    stop_kind=status_snapshot.stop_kind,
                    exit_code=status_snapshot.exit_code,
                    matched_failure=matched,
                    trigger=trigger,
                )
                iteration_summaries.append(iteration_summary)

                if matched:
                    matched_iteration = iteration_summary
                    break
            finally:
                self._cleanup_session(session)

        warnings: tuple[str, ...] = ()
        if capture_error is not None:
            warnings = (f"Capture bundle failed: {capture_error}",)

        if matched_iteration is None:
            message = f"No failure matched after {len(iteration_summaries)} iterations"
        else:
            message = f"Failure matched on iteration {matched_iteration.iteration}: {matched_iteration.trigger}"

        return OperationSuccess(
            RunUntilFailureInfo(
                message=message,
                iterations_requested=request.max_iterations,
                capture_bundle=capture_bundle,
                capture_error=capture_error,
                iterations=iteration_summaries,
            ),
            warnings=warnings,
        )

    @staticmethod
    def _matches_failure(
        criteria: RunUntilFailureCriteria,
        *,
        result: OperationResult[CommandExecutionInfo],
        payload: StructuredPayload,
        status: SessionStatusSnapshot,
        stop_event: StopEvent | None,
        compiled_regex: Pattern[str] | None,
    ) -> tuple[bool, str | None]:
        """Return whether one iteration matches the configured failure predicates."""

        if isinstance(result, OperationError):
            if criteria.failure_on_timeout and "timeout" in result.message.lower():
                return True, "timeout"
            if criteria.failure_on_error:
                return True, "run_error"

        stop_reason = stop_event.reason if stop_event is not None else status.stop_reason
        if stop_reason is not None and stop_reason in criteria.stop_reasons:
            return True, f"stop_reason:{stop_reason}"

        if status.execution_state in criteria.execution_states:
            return True, f"execution_state:{status.execution_state}"

        if status.exit_code is not None and status.exit_code in criteria.exit_codes:
            return True, f"exit_code:{status.exit_code}"

        if compiled_regex is not None:
            payload_text = json.dumps(payload, sort_keys=True)
            if compiled_regex.search(payload_text):
                return True, "result_text_regex"

        return False, None

    @staticmethod
    def _bundle_name(
        bundle_name: str | None,
        prefix: str | None,
        iteration: int,
    ) -> str | None:
        """Return the capture bundle name for a matching iteration when requested."""

        if bundle_name is not None:
            return bundle_name
        if prefix is None:
            return None
        return f"{prefix}-iter-{iteration:04d}"

    @staticmethod
    def _status_snapshot(session: SessionService) -> SessionStatusSnapshot:
        """Return the latest structured status snapshot for one session."""

        return session.get_status().value

    @staticmethod
    def _cleanup_session(session: SessionService) -> None:
        """Best-effort cleanup for one temporary session."""

        if not session.has_controller:
            return
        with session_workflow_context(session):
            try:
                session.stop()
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("Ignoring failure while cleaning up a temporary session: %s", exc)
                return
