"""Batch, capture-bundle, and run-until-failure handlers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel

from ... import contracts as shared_contracts
from ...domain import (
    OperationError,
    OperationSuccess,
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
from .common import (
    SessionToolSpec,
    ToolArguments,
    ToolResult,
    _memory_capture_ranges,
    _normalize_run_args,
    _workflow_step_validation_error,
    _wrap_action_result_for,
)


def _handle_batch(session: SessionService, args: BatchArgs) -> ToolResult:
    """Validate one batch request and execute it under one workflow lock."""

    step_templates = _build_batch_step_templates(args.session_id, args.steps)
    if isinstance(step_templates, OperationError):
        return step_templates

    return session.execute_batch_templates(
        step_templates.value,
        fail_fast=args.fail_fast,
        capture_stop_events=args.capture_stop_events,
    )


def _handle_capture_bundle(session: SessionService, args: CaptureBundleArgs) -> ToolResult:
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
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Run repeated fresh sessions until one failure predicate matches."""

    args = RunUntilFailureArgs.model_validate(arguments)
    step_templates = _build_batch_step_templates(1, args.setup_steps)
    if isinstance(step_templates, OperationError):
        return step_templates

    run_args = _normalize_run_args(args.run_args)
    if isinstance(run_args, OperationError):
        return run_args

    capture_memory_ranges = _memory_capture_ranges(args.capture.memory_ranges)
    if isinstance(capture_memory_ranges, OperationError):
        return capture_memory_ranges

    runner = RunUntilFailureService(session_manager.create_untracked_session)
    return runner.run_until_failure(
        RunUntilFailureRequest(
            program=args.startup.program,
            args=tuple(args.startup.args or ()),
            init_commands=tuple(args.startup.init_commands or ()),
            env=dict(args.startup.env or {}),
            gdb_path=args.startup.gdb_path,
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

        tool_spec = SESSION_TOOL_SPECS.get(step.tool)
        if tool_spec is None:
            return OperationError(
                message=f"Unsupported batch step tool: {step.tool}",
                code="unknown_tool",
            )
        resolved_tool_spec = tool_spec

        step_arguments = cast(ToolArguments, {"session_id": session_id, **step.arguments})

        try:
            validated_args = resolved_tool_spec.model.model_validate(step_arguments)
        except Exception as exc:
            return OperationError(
                message=f"Invalid batch step {index} ({step.tool}): {exc}",
                code="validation_error",
            )

        def execute_step(
            session: SessionService,
            tool_spec: SessionToolSpec = resolved_tool_spec,
            validated_args: BaseModel = validated_args,
        ) -> ToolResult:
            return _wrap_action_result_for(
                validated_args,
                tool_spec.handler(session, validated_args),
            )

        templates.append(
            BatchStepTemplate(
                tool=step.tool,
                label=step.label,
                execute=execute_step,
            )
        )

    return OperationSuccess(templates)
