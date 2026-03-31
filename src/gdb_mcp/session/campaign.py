"""Repeat-until-failure campaign helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Pattern

from ..domain import (
    CaptureBundleInfo,
    MemoryCaptureRange,
    OperationError,
    OperationSuccess,
    RunUntilFailureInfo,
    RunUntilFailureIterationInfo,
    SessionStatusSnapshot,
    StopEvent,
    StructuredPayload,
    result_to_mapping,
)
from .locking import session_workflow_context
from .service import SessionService
from .workflow import BatchStepTemplate


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
    args: tuple[str, ...] = ()
    init_commands: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    gdb_path: str | None = None
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

    def __init__(self, session_factory):
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
        matched_info: _MatchedFailure | None = None
        last_payload: StructuredPayload | None = None
        last_status_snapshot: SessionStatusSnapshot | None = None

        for iteration in range(1, request.max_iterations + 1):
            session = self._session_factory()
            run_payload: StructuredPayload | None = None
            status_snapshot: SessionStatusSnapshot | None = None
            capture_bundle: CaptureBundleInfo | None = None
            capture_error: str | None = None

            try:
                startup_result = session.start(
                    program=request.program,
                    args=list(request.args) or None,
                    init_commands=list(request.init_commands) or None,
                    env=dict(request.env) or None,
                    gdb_path=request.gdb_path,
                    working_dir=request.working_dir,
                    core=request.core,
                )
                if isinstance(startup_result, OperationError):
                    run_payload = result_to_mapping(startup_result)
                    last_payload = run_payload
                    matched_info = _MatchedFailure(
                        iteration=iteration,
                        trigger="startup_error",
                        run_payload=run_payload,
                        status_snapshot=None,
                        capture_bundle=None,
                        capture_error=None,
                    )
                    iteration_summaries.append(
                        RunUntilFailureIterationInfo(
                            iteration=iteration,
                            status="error",
                            matched_failure=True,
                            trigger="startup_error",
                            message=startup_result.message,
                        )
                    )
                    break

                if request.setup_steps:
                    setup_result = session.execute_batch_templates(
                        request.setup_steps,
                        fail_fast=True,
                        capture_stop_events=True,
                    )
                    if setup_result.value.error_count > 0:
                        matched_info = _MatchedFailure(
                            iteration=iteration,
                            trigger="setup_error",
                            run_payload=result_to_mapping(setup_result),
                            status_snapshot=self._status_snapshot(session),
                            capture_bundle=None,
                            capture_error=None,
                        )
                        iteration_summaries.append(
                            RunUntilFailureIterationInfo(
                                iteration=iteration,
                                status="error",
                                execution_state=(
                                    matched_info.status_snapshot.execution_state
                                    if matched_info.status_snapshot is not None
                                    else None
                                ),
                                stop_reason=(
                                    matched_info.status_snapshot.stop_reason
                                    if matched_info.status_snapshot is not None
                                    else None
                                ),
                                exit_code=(
                                    matched_info.status_snapshot.exit_code
                                    if matched_info.status_snapshot is not None
                                    else None
                                ),
                                matched_failure=True,
                                trigger="setup_error",
                                message="One or more setup steps failed",
                            )
                        )
                        break

                run_result = session.run(
                    args=list(request.run_args) or None,
                    timeout_sec=request.run_timeout_sec,
                )
                run_payload = result_to_mapping(run_result)
                status_snapshot = self._status_snapshot(session)
                last_payload = run_payload
                last_status_snapshot = status_snapshot

                matched, trigger = self._matches_failure(
                    request.failure,
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

                iteration_summaries.append(
                    RunUntilFailureIterationInfo(
                        iteration=iteration,
                        status=str(run_payload.get("status", "success")),
                        execution_state=status_snapshot.execution_state,
                        stop_reason=status_snapshot.stop_reason,
                        exit_code=status_snapshot.exit_code,
                        matched_failure=matched,
                        trigger=trigger,
                        message=self._payload_message(run_payload),
                    )
                )

                if matched:
                    matched_info = _MatchedFailure(
                        iteration=iteration,
                        trigger=trigger,
                        run_payload=run_payload,
                        status_snapshot=status_snapshot,
                        capture_bundle=capture_bundle,
                        capture_error=capture_error,
                    )
                    break
            finally:
                self._cleanup_session(session)

        if matched_info is None:
            return OperationSuccess(
                RunUntilFailureInfo(
                    message=(
                        f"No failure matched after {request.max_iterations} iteration"
                        f"{'' if request.max_iterations == 1 else 's'}"
                    ),
                    matched_failure=False,
                    iterations_requested=request.max_iterations,
                    iterations_completed=len(iteration_summaries),
                    execution_state=(
                        last_status_snapshot.execution_state
                        if last_status_snapshot is not None
                        else None
                    ),
                    stop_reason=(
                        last_status_snapshot.stop_reason
                        if last_status_snapshot is not None
                        else None
                    ),
                    exit_code=(
                        last_status_snapshot.exit_code if last_status_snapshot is not None else None
                    ),
                    last_result=last_payload,
                    iterations=iteration_summaries,
                )
            )

        warnings: tuple[str, ...] = ()
        if matched_info.capture_error is not None:
            warnings = (f"Capture bundle failed: {matched_info.capture_error}",)

        return OperationSuccess(
            RunUntilFailureInfo(
                message=(
                    f"Failure matched on iteration {matched_info.iteration}: {matched_info.trigger}"
                ),
                matched_failure=True,
                iterations_requested=request.max_iterations,
                iterations_completed=len(iteration_summaries),
                failure_iteration=matched_info.iteration,
                trigger=matched_info.trigger,
                execution_state=(
                    matched_info.status_snapshot.execution_state
                    if matched_info.status_snapshot is not None
                    else None
                ),
                stop_reason=(
                    matched_info.status_snapshot.stop_reason
                    if matched_info.status_snapshot is not None
                    else None
                ),
                exit_code=(
                    matched_info.status_snapshot.exit_code
                    if matched_info.status_snapshot is not None
                    else None
                ),
                capture_bundle=matched_info.capture_bundle,
                capture_error=matched_info.capture_error,
                last_result=matched_info.run_payload,
                iterations=iteration_summaries,
            ),
            warnings=warnings,
        )

    @staticmethod
    def _matches_failure(
        criteria: RunUntilFailureCriteria,
        *,
        payload: StructuredPayload,
        status: SessionStatusSnapshot,
        stop_event: StopEvent | None,
        compiled_regex: Pattern[str] | None,
    ) -> tuple[bool, str | None]:
        """Return whether one iteration matches the configured failure predicates."""

        payload_status = payload.get("status")
        message = RunUntilFailureService._payload_message(payload)
        if (
            criteria.failure_on_timeout
            and isinstance(message, str)
            and "timeout" in message.lower()
        ):
            return True, "timeout"
        if criteria.failure_on_error and payload_status == "error":
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
    def _payload_message(payload: StructuredPayload) -> str | None:
        """Extract a human-readable message from a serialized result payload."""

        value = payload.get("message")
        return value if isinstance(value, str) else None

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

        if session.controller is None:
            return
        with session_workflow_context(session):
            try:
                session.stop()
            except Exception:
                return


@dataclass(slots=True, frozen=True)
class _MatchedFailure:
    """Internal matched-failure record used while assembling the final response."""

    iteration: int
    trigger: str | None
    run_payload: StructuredPayload
    status_snapshot: SessionStatusSnapshot | None
    capture_bundle: CaptureBundleInfo | None
    capture_error: str | None
