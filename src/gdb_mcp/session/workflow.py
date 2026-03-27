"""Workflow-oriented helpers for composed debugger sessions."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Sequence

from ..domain import (
    BatchExecutionInfo,
    BatchStepResult,
    OperationError,
    OperationResult,
    OperationSuccess,
    StopEvent,
    StructuredPayload,
    result_to_mapping,
)
from .runtime import SessionRuntime

WorkflowResult = OperationResult[object]


@dataclass(slots=True, frozen=True)
class BatchStepInvocation:
    """One validated batch step ready to be executed under the workflow lock."""

    tool: str
    execute: Callable[[], WorkflowResult]
    label: str | None = None


class SessionWorkflowService:
    """High-level workflow operations layered on top of one live session."""

    def __init__(self, runtime: SessionRuntime):
        self._runtime = runtime

    def execute_batch(
        self,
        steps: Sequence[BatchStepInvocation],
        *,
        fail_fast: bool = True,
        capture_stop_events: bool = True,
    ) -> OperationSuccess[BatchExecutionInfo]:
        """Execute a validated step sequence atomically within one session."""

        results: list[BatchStepResult] = []
        error_count = 0
        stopped_early = False
        failure_step_index: int | None = None

        for index, step in enumerate(steps):
            previous_stop_history_size = len(self._runtime.stop_history)
            step_result = step.execute()
            serialized_result = result_to_mapping(step_result)
            step_status = serialized_result.get("status")
            if not isinstance(step_status, str):
                step_status = "error" if isinstance(step_result, OperationError) else "success"

            stop_event = None
            if capture_stop_events:
                stop_event = self._step_stop_event(previous_stop_history_size)

            results.append(
                BatchStepResult(
                    index=index,
                    tool=step.tool,
                    label=step.label,
                    status=step_status,
                    result=serialized_result,
                    stop_event=stop_event,
                )
            )

            if isinstance(step_result, OperationError):
                error_count += 1
                if fail_fast:
                    stopped_early = True
                    failure_step_index = index
                    break

        return OperationSuccess(
            BatchExecutionInfo(
                steps=results,
                count=len(steps),
                completed_steps=len(results),
                error_count=error_count,
                stopped_early=stopped_early,
                failure_step_index=failure_step_index,
                final_execution_state=self._runtime.execution_state,
                final_stop_reason=self._runtime.stop_reason,
                last_stop_event=self._runtime.last_stop_event,
            )
        )

    def _step_stop_event(self, previous_stop_history_size: int) -> StopEvent | None:
        """Return the new stop event produced by the most recent step, if any."""

        if len(self._runtime.stop_history) > previous_stop_history_size:
            return self._runtime.stop_history[-1]
        return None
