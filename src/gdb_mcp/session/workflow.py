"""Workflow-oriented helpers for composed debugger sessions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..domain import (
    BatchExecutionInfo,
    BatchStepResult,
    OperationError,
    OperationResult,
    OperationSuccess,
    ToolPayload,
    result_to_envelope,
)
from .runtime import SessionRuntime

if TYPE_CHECKING:
    from .service import SessionService

WorkflowResult = OperationResult[ToolPayload]


@dataclass(slots=True, frozen=True)
class BatchStepInvocation:
    """One validated batch step ready to be executed under the workflow lock."""

    tool: str
    execute: Callable[[], WorkflowResult]
    label: str | None = None


@dataclass(slots=True, frozen=True)
class BatchStepTemplate:
    """Reusable validated batch step that can be bound to any session instance."""

    tool: str
    execute: Callable[[SessionService], WorkflowResult]
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

        for index, step in enumerate(steps):
            previous_stop = self._runtime.last_stop_event
            step_result = step.execute()
            serialized_result = result_to_envelope(step_result)
            stop_event = None
            if capture_stop_events and self._runtime.last_stop_event is not previous_stop:
                stop_event = self._runtime.last_stop_event

            results.append(
                BatchStepResult(
                    index=index,
                    tool=step.tool,
                    label=step.label,
                    result=serialized_result,
                    stop_event=stop_event,
                )
            )

            if isinstance(step_result, OperationError) and fail_fast:
                break

        return OperationSuccess(
            BatchExecutionInfo(
                steps=results,
                requested_steps=len(steps),
                final_execution_state=self._runtime.execution_state,
                final_stop_reason=self._runtime.stop_reason,
                final_stop_kind=self._runtime.stop_kind,
                last_stop_event=self._runtime.last_stop_event,
            )
        )

    def execute_batch_templates(
        self,
        session: SessionService,
        steps: Sequence[BatchStepTemplate],
        *,
        fail_fast: bool = True,
        capture_stop_events: bool = True,
    ) -> OperationSuccess[BatchExecutionInfo]:
        """Bind reusable step templates to one session and execute them as a batch."""

        def build_invocation(step: BatchStepTemplate) -> BatchStepInvocation:
            def execute_bound() -> WorkflowResult:
                return step.execute(session)

            return BatchStepInvocation(
                tool=step.tool,
                label=step.label,
                execute=execute_bound,
            )

        invocations = [build_invocation(step) for step in steps]
        return self.execute_batch(
            invocations,
            fail_fast=fail_fast,
            capture_stop_events=capture_stop_events,
        )
