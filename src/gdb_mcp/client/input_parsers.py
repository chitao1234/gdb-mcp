"""Namespace-to-typed-input parsers for the MCP CLI client."""

from __future__ import annotations

import argparse
from typing import cast

from pydantic import ValidationError

import gdb_mcp.contracts as shared_contracts
from gdb_mcp.contracts import (
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    BatchStepToolName,
)
from gdb_mcp.mcp.schemas import BATCH_STEP_TOOL_MODELS

from .inputs import (
    BreakpointAccess,
    BreakpointCreateInput,
    BreakpointEvent,
    BreakpointKind,
    BreakpointManageAction,
    BreakpointManageInput,
    BreakpointQueryAction,
    BreakpointQueryInput,
    ContextManageAction,
    ContextManageInput,
    ContextQueryAction,
    ContextQueryInput,
    DisassemblyMode,
    ExecutionManageAction,
    ExecutionManageInput,
    ExecutionWaitUntil,
    InferiorFollowForkMode,
    InferiorManageAction,
    InferiorManageInput,
    InferiorQueryAction,
    InferiorQueryInput,
    InspectQueryAction,
    InspectQueryInput,
    LocationInput,
    LocationKind,
    RegisterValueFormat,
    RunUntilFailureInput,
    SessionQueryAction,
    SessionQueryInput,
    SessionStartInput,
    SessionStepInput,
    WorkflowBatchInput,
)
from .parsers import (
    CliUsageError,
    assign_dotted_value,
    collapse_key_value_entries,
    format_validation_error,
    validate_model_payload_with_list_coercion,
)

_WORKFLOW_STEP_OPTION_MAP = {
    "--setup-step": "--step",
    "--setup-step-label": "--step-label",
    "--setup-step-arg": "--step-arg",
}


def _normalize_step_tool_name(tool_name: str) -> str:
    """Accept the gdb_-less tool spelling inside workflow steps."""

    if tool_name.startswith("gdb_"):
        return tool_name
    prefixed = f"gdb_{tool_name}"
    if prefixed in shared_contracts.PUBLIC_TOOL_NAMES:
        return prefixed
    return tool_name


def parse_session_start_input(namespace: argparse.Namespace) -> SessionStartInput:
    """Parse one session-start namespace into a typed input object."""

    return SessionStartInput(
        program=namespace.program,
        debugger=namespace.debugger,
        args=tuple(namespace.args),
        init_commands=tuple(namespace.init_commands),
        env=collapse_key_value_entries(namespace.env),
        core=namespace.core,
        gdb_path=namespace.gdb_path,
        gdb_args=tuple(namespace.gdb_args),
        working_dir=namespace.working_dir,
        rr_replay=namespace.rr_replay,
    )


def parse_session_query_input(namespace: argparse.Namespace) -> SessionQueryInput:
    """Parse one session-query namespace into a typed input object."""

    return SessionQueryInput(
        action=cast(SessionQueryAction, namespace.action),
        session_id=namespace.session_id,
    )


def parse_inferior_query_input(namespace: argparse.Namespace) -> InferiorQueryInput:
    """Parse one inferior-query namespace into a typed input object."""

    return InferiorQueryInput(
        action=cast(InferiorQueryAction, namespace.action),
        session_id=namespace.session_id,
    )


def parse_inferior_manage_input(namespace: argparse.Namespace) -> InferiorManageInput:
    """Parse one inferior-manage namespace into a typed input object."""

    return InferiorManageInput(
        action=cast(InferiorManageAction, namespace.action),
        session_id=namespace.session_id,
        executable=namespace.executable,
        make_current=namespace.make_current,
        inferior_id=namespace.inferior_id,
        mode=cast(InferiorFollowForkMode | None, namespace.mode),
        enabled=namespace.enabled,
    )


def parse_execution_manage_input(namespace: argparse.Namespace) -> ExecutionManageInput:
    """Parse one execution-manage namespace into a typed input object."""

    return ExecutionManageInput(
        action=cast(ExecutionManageAction, namespace.action),
        session_id=namespace.session_id,
        args=tuple(namespace.args or ()),
        wait_until=cast(ExecutionWaitUntil | None, namespace.wait_until),
        timeout_sec=namespace.timeout_sec,
        stop_reasons=tuple(namespace.stop_reasons or ()),
        reverse=bool(namespace.reverse),
    )


def parse_context_query_input(namespace: argparse.Namespace) -> ContextQueryInput:
    """Parse one context-query namespace into a typed input object."""

    return ContextQueryInput(
        action=cast(ContextQueryAction, namespace.action),
        session_id=namespace.session_id,
        thread_id=namespace.thread_id,
        frame=namespace.frame,
        max_frames=namespace.max_frames,
    )


def parse_context_manage_input(namespace: argparse.Namespace) -> ContextManageInput:
    """Parse one context-manage namespace into a typed input object."""

    return ContextManageInput(
        action=cast(ContextManageAction, namespace.action),
        session_id=namespace.session_id,
        thread_id=namespace.thread_id,
        frame=namespace.frame,
    )


def _parse_location_input(namespace: argparse.Namespace) -> LocationInput | None:
    location_kind = namespace.location_kind
    if location_kind is None:
        return None

    return LocationInput(
        kind=cast(LocationKind, location_kind.replace("-", "_")),
        function=namespace.function,
        address=namespace.address,
        start_address=namespace.start_address,
        end_address=namespace.end_address,
        file=namespace.file,
        line=namespace.line,
        start_line=namespace.start_line,
        end_line=namespace.end_line,
    )


def _validate_workflow_step(
    tool_name: str,
    arguments: dict[str, object],
    *,
    index: int,
) -> dict[str, object]:
    issue = shared_contracts.validate_workflow_step_contract(tool_name, arguments)
    if issue is not None:
        if issue.kind == "session_id_not_allowed":
            raise CliUsageError(
                f"Workflow step {index} ({tool_name}) must not include session_id; "
                "it is inherited from the enclosing command"
            )
        if issue.kind == "session_query_list_not_allowed":
            raise CliUsageError(
                f"{TOOL_SESSION_QUERY}(action={shared_contracts.ACTION_LIST}) "
                "is not valid inside workflow steps"
            )
        if issue.kind == "session_manage_not_allowed":
            raise CliUsageError(f"{TOOL_SESSION_MANAGE} is not valid inside workflow steps")
        if issue.kind == "nested_workflow_tool_not_allowed":
            raise CliUsageError(f"{tool_name} is not valid inside workflow steps")
        raise CliUsageError(f"Unsupported workflow step tool: {tool_name}")

    model = BATCH_STEP_TOOL_MODELS.get(tool_name)
    if model is None:
        raise CliUsageError(f"Unsupported workflow step tool: {tool_name}")

    try:
        validated = validate_model_payload_with_list_coercion(
            model,
            {"session_id": 1, **arguments},
        )
    except ValidationError as exc:
        raise CliUsageError(
            f"Invalid workflow step {index} ({tool_name}): {format_validation_error(exc)}"
        ) from exc

    validated.pop("session_id", None)
    return validated


def _normalize_step_events(
    step_events: list[tuple[str, object]] | None,
) -> list[tuple[str, object]] | None:
    if step_events is None:
        return None
    return [(_WORKFLOW_STEP_OPTION_MAP.get(option, option), value) for option, value in step_events]


def parse_step_inputs(
    step_events: list[tuple[str, object]] | None,
    *,
    required: bool,
    step_flag: str,
) -> tuple[SessionStepInput, ...] | None:
    normalized_events = _normalize_step_events(step_events)
    if not normalized_events:
        if required:
            raise CliUsageError(f"At least one {step_flag} is required")
        return None

    steps: list[SessionStepInput] = []
    current_tool: BatchStepToolName | None = None
    current_label: str | None = None
    current_arguments: dict[str, object] | None = None

    for option, value in normalized_events:
        if option == "--step":
            current_tool = cast(BatchStepToolName, _normalize_step_tool_name(str(value)))
            current_label = None
            current_arguments = {}
            steps.append(
                SessionStepInput(
                    tool=current_tool,
                    label=current_label,
                    arguments=current_arguments,
                )
            )
            continue

        if current_tool is None or current_arguments is None:
            raise CliUsageError(f"{option} requires a preceding {step_flag}")

        if option == "--step-label":
            current_label = cast(str, value)
            steps[-1] = SessionStepInput(
                tool=current_tool,
                label=current_label,
                arguments=current_arguments,
            )
            continue

        if option == "--step-arg":
            path, scalar = cast(tuple[str, object], value)
            assign_dotted_value(current_arguments, path, scalar)

    validated_steps: list[SessionStepInput] = []
    for index, step in enumerate(steps):
        validated_steps.append(
            SessionStepInput(
                tool=step.tool,
                label=step.label,
                arguments=_validate_workflow_step(
                    step.tool,
                    dict(step.arguments),
                    index=index,
                ),
            )
        )

    return tuple(validated_steps)


def parse_breakpoint_query_input(namespace: argparse.Namespace) -> BreakpointQueryInput:
    """Parse one breakpoint-query namespace into a typed input object."""

    return BreakpointQueryInput(
        action=cast(BreakpointQueryAction, namespace.action),
        session_id=namespace.session_id,
        number=namespace.number,
        kinds=tuple(cast(tuple[BreakpointKind, ...], tuple(namespace.kinds or ()))),
        enabled=namespace.enabled,
    )


def parse_breakpoint_manage_input(namespace: argparse.Namespace) -> BreakpointManageInput:
    """Parse one breakpoint-manage namespace into a typed input object."""

    breakpoint_kind = cast(BreakpointKind | None, namespace.breakpoint_kind)
    temporary_explicit = namespace.temporary is not None
    has_breakpoint_fields = (
        breakpoint_kind is not None
        or namespace.location is not None
        or namespace.expression is not None
        or namespace.access is not None
        or namespace.event is not None
        or namespace.argument is not None
        or temporary_explicit
    )
    breakpoint = (
        BreakpointCreateInput(
            kind=breakpoint_kind or "code",
            location=namespace.location,
            expression=namespace.expression,
            access=cast(BreakpointAccess | None, namespace.access),
            event=cast(BreakpointEvent | None, namespace.event),
            argument=namespace.argument,
            condition=namespace.condition,
            temporary=bool(namespace.temporary),
            kind_explicit=breakpoint_kind is not None,
            temporary_explicit=temporary_explicit,
        )
        if namespace.action == "create" or has_breakpoint_fields
        else None
    )

    return BreakpointManageInput(
        action=cast(BreakpointManageAction, namespace.action),
        session_id=namespace.session_id,
        breakpoint=breakpoint,
        number=namespace.number,
        condition=namespace.condition,
        clear_condition=namespace.clear_condition,
    )


def parse_inspect_query_input(namespace: argparse.Namespace) -> InspectQueryInput:
    """Parse one inspect-query namespace into a typed input object."""

    action = cast(InspectQueryAction, namespace.action)
    location_fields = tuple(
        field_name
        for field_name, value in (
            ("location_kind", namespace.location_kind),
            ("function", namespace.function),
            ("address", namespace.address),
            ("start_address", namespace.start_address),
            ("end_address", namespace.end_address),
            ("file", namespace.file),
            ("line", namespace.line),
            ("start_line", namespace.start_line),
            ("end_line", namespace.end_line),
        )
        if value is not None and not (field_name == "address" and action == "memory")
    )
    location = _parse_location_input(namespace)
    if location is None and action in ("source", "disassembly") and not location_fields:
        # Bare `list`/`disasm` operate on the current location rather than
        # forcing callers to spell out `--location-kind current`.
        location = LocationInput(kind="current")
    return InspectQueryInput(
        action=action,
        session_id=namespace.session_id,
        thread_id=namespace.thread_id,
        frame=namespace.frame,
        expression=namespace.expression,
        register_numbers=tuple(namespace.register_numbers or ()),
        register_names=tuple(namespace.register_names or ()),
        include_vector_registers=namespace.include_vector_registers,
        max_registers=namespace.max_registers,
        value_format=cast(RegisterValueFormat | None, namespace.value_format),
        memory_address=namespace.address if action == "memory" else None,
        count=namespace.count,
        offset=namespace.offset,
        location=location,
        instruction_count=namespace.instruction_count,
        mode=cast(DisassemblyMode | None, namespace.mode),
        context_before=namespace.context_before,
        context_after=namespace.context_after,
        location_fields=location_fields,
    )


def parse_workflow_batch_input(namespace: argparse.Namespace) -> WorkflowBatchInput:
    """Parse one workflow-batch namespace into a typed input object."""

    steps = parse_step_inputs(
        namespace.step_events,
        required=True,
        step_flag="--step",
    )
    if steps is None:  # pragma: no cover - required=True raises before this
        raise CliUsageError("At least one --step is required")
    return WorkflowBatchInput(
        session_id=namespace.session_id,
        steps=steps,
        fail_fast=namespace.fail_fast,
        capture_stop_events=namespace.capture_stop_events,
    )


def parse_run_until_failure_input(namespace: argparse.Namespace) -> RunUntilFailureInput:
    """Parse one run-until-failure namespace into a typed input object."""

    return RunUntilFailureInput(
        startup_program=namespace.startup_program,
        startup_args=tuple(namespace.startup_args),
        startup_init_commands=tuple(namespace.startup_init_commands),
        startup_env=collapse_key_value_entries(namespace.startup_env),
        startup_gdb_path=namespace.startup_gdb_path,
        startup_working_dir=namespace.startup_working_dir,
        startup_core=namespace.startup_core,
        setup_steps=parse_step_inputs(
            namespace.setup_step_events,
            required=False,
            step_flag="--setup-step",
        ),
        run_args=tuple(namespace.run_args),
        run_timeout_sec=namespace.run_timeout_sec,
        max_iterations=namespace.max_iterations,
        failure_on_error=namespace.failure_on_error,
        failure_on_timeout=namespace.failure_on_timeout,
        failure_stop_reasons=(
            tuple(namespace.failure_stop_reasons)
            if namespace.failure_stop_reasons is not None
            else None
        ),
        failure_execution_states=(
            tuple(namespace.failure_execution_states)
            if namespace.failure_execution_states is not None
            else None
        ),
        failure_exit_codes=(
            tuple(namespace.failure_exit_codes)
            if namespace.failure_exit_codes is not None
            else None
        ),
        failure_result_text_regex=namespace.failure_result_text_regex,
        capture_enabled=namespace.capture_enabled,
        capture_output_dir=namespace.capture_output_dir,
        capture_bundle_name_prefix=namespace.capture_bundle_name_prefix,
        capture_bundle_name=namespace.capture_bundle_name,
        capture_expressions=tuple(namespace.capture_expressions),
        capture_memory_ranges=tuple(namespace.capture_memory_ranges),
        capture_max_frames=namespace.capture_max_frames,
        capture_include_threads=namespace.capture_include_threads,
        capture_include_backtraces=namespace.capture_include_backtraces,
        capture_include_frame=namespace.capture_include_frame,
        capture_include_variables=namespace.capture_include_variables,
        capture_include_registers=namespace.capture_include_registers,
        capture_include_transcript=namespace.capture_include_transcript,
        capture_include_stop_history=namespace.capture_include_stop_history,
    )
