"""Batch, capture-bundle, and run-until-failure handlers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import ValidationError

from ... import contracts as shared_contracts
from ...domain import (
    BatchExecutionInfo,
    CaptureBundleInfo,
    OperationError,
    OperationResult,
    OperationSuccess,
    RunUntilFailureInfo,
)
from ...session.campaign import (
    RunUntilFailureCaptureRequest,
    RunUntilFailureCriteria,
    RunUntilFailureRequest,
    RunUntilFailureService,
)
from ...session.registry import SessionRegistry
from ...session.service import SessionService
from ...session.workflow import BatchStepTemplate
from ..schemas import (
    BatchArgs,
    BatchStepArgs,
    CaptureBundleArgs,
    RunUntilFailureArgs,
)
from ..validation_errors import build_validation_error
from .common import (
    SessionToolInvocation,
    ToolResult,
    _memory_capture_ranges,
    _normalize_run_args,
    _workflow_step_validation_error,
    execute_session_invocation,
)


def _handle_batch(session: SessionService, args: BatchArgs) -> OperationResult[BatchExecutionInfo]:
    """Validate one batch request and execute it under one workflow lock."""

    step_templates = _build_batch_step_templates(args.session_id, args.steps)
    if isinstance(step_templates, OperationError):
        return step_templates

    return session.execute_batch_templates(
        step_templates.value,
        fail_fast=args.fail_fast,
        capture_stop_events=args.capture_stop_events,
    )


def _handle_capture_bundle(
    session: SessionService, args: CaptureBundleArgs
) -> OperationResult[CaptureBundleInfo]:
    """Write a file-oriented forensic bundle for the current session."""

    memory_ranges = _memory_capture_ranges(args.memory_ranges)
    if isinstance(memory_ranges, OperationError):
        return memory_ranges

    return session.capture_bundle(
        output_dir=args.output_dir,
        bundle_name=args.bundle_name,
        expressions=args.expressions,
        memory_ranges=memory_ranges,
        max_frames=args.max_frames,
        include_threads=args.include_threads,
        include_backtraces=args.include_backtraces,
        include_frame=args.include_frame,
        include_variables=args.include_variables,
        include_registers=args.include_registers,
        include_transcript=args.include_transcript,
        include_stop_history=args.include_stop_history,
    )


def _handle_run_until_failure(
    args: RunUntilFailureArgs,
    session_manager: SessionRegistry,
) -> OperationResult[RunUntilFailureInfo]:
    """Run repeated fresh sessions until one failure predicate matches."""

    step_templates = _build_batch_step_templates(1, args.setup_steps, steps_field="setup_steps")
    if isinstance(step_templates, OperationError):
        return step_templates

    run_args = _normalize_run_args(args.run_args)
    if isinstance(run_args, OperationError):
        return run_args

    startup_args = _normalize_run_args(args.startup.args)
    if isinstance(startup_args, OperationError):
        return startup_args

    capture_memory_ranges = _memory_capture_ranges(args.capture.memory_ranges)
    if isinstance(capture_memory_ranges, OperationError):
        return capture_memory_ranges

    runner = RunUntilFailureService(
        lambda: session_manager.create_untracked_session(args.startup.debugger)
    )
    return runner.run_until_failure(
        RunUntilFailureRequest(
            program=args.startup.program,
            debugger=args.startup.debugger,
            debugger_args=tuple(args.startup.debugger_args or ()),
            rr_replay=args.startup.rr_replay,
            args=tuple(startup_args or ()),
            init_commands=tuple(args.startup.init_commands or ()),
            env=dict(args.startup.env or {}),
            debugger_path=args.startup.debugger_path,
            working_dir=args.startup.working_dir,
            core=args.startup.core,
            setup_steps=tuple(step_templates.value),
            run_args=tuple(run_args or ()),
            run_timeout_sec=args.run_timeout_sec,
            max_iterations=args.max_iterations,
            failure=RunUntilFailureCriteria(
                failure_on_error=args.failure.failure_on_error,
                failure_on_timeout=args.failure.failure_on_timeout,
                stop_reasons=tuple(args.failure.stop_reasons),
                execution_states=tuple(args.failure.execution_states),
                exit_codes=tuple(args.failure.exit_codes),
                result_text_regex=args.failure.result_text_regex,
            ),
            capture=RunUntilFailureCaptureRequest(
                enabled=args.capture.enabled,
                output_dir=args.capture.output_dir,
                bundle_name_prefix=args.capture.bundle_name_prefix,
                bundle_name=args.capture.bundle_name,
                expressions=tuple(args.capture.expressions),
                memory_ranges=tuple(capture_memory_ranges),
                max_frames=args.capture.max_frames,
                include_threads=args.capture.include_threads,
                include_backtraces=args.capture.include_backtraces,
                include_frame=args.capture.include_frame,
                include_variables=args.capture.include_variables,
                include_registers=args.capture.include_registers,
                include_transcript=args.capture.include_transcript,
                include_stop_history=args.capture.include_stop_history,
            ),
        )
    )


def _build_batch_step_templates(
    session_id: int,
    steps: Sequence[BatchStepArgs | str],
    *,
    steps_field: Literal["steps", "setup_steps"] = "steps",
) -> OperationSuccess[list[BatchStepTemplate]] | OperationError:
    """Validate batch-like step definitions into reusable execution templates."""

    from .registry import SESSION_TOOL_SPECS

    templates: list[BatchStepTemplate] = []

    for index, raw_step in enumerate(steps):
        step = (
            BatchStepArgs.model_validate({"tool": raw_step})
            if isinstance(raw_step, str)
            else raw_step
        )
        issue = shared_contracts.validate_workflow_step_contract(step.tool, step.arguments)
        if issue is not None:
            return _workflow_step_validation_error(step.tool, issue, index=index)

        tool_spec = SESSION_TOOL_SPECS[step.tool]
        step_arguments = {"session_id": session_id, **step.arguments}

        try:
            invocation = tool_spec.prepare(step_arguments)
        except ValidationError as exc:
            return build_validation_error(
                exc,
                tool_name=step.tool,
                model=tool_spec.model,
                field_prefix=f"{steps_field}[{index}].arguments",
            )

        def execute_step(
            session: SessionService,
            invocation: SessionToolInvocation = invocation,
            step_session_id: int | None = session_id if steps_field == "steps" else None,
        ) -> ToolResult:
            return execute_session_invocation(invocation, session, session_id=step_session_id)

        templates.append(
            BatchStepTemplate(
                tool=step.tool,
                label=step.label,
                execute=execute_step,
            )
        )

    return OperationSuccess(templates)
