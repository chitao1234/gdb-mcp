"""Namespace-to-typed-input parsers for the MCP CLI client."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from typing import cast

from pydantic import ValidationError

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
    ExecutionWaitInput,
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
    SessionStepInput,
    SessionStartInput,
    WorkflowBatchInput,
)
from .parsers import (
    CliUsageError,
    assign_dotted_value,
    collapse_key_value_entries,
    format_validation_error,
    validate_model_payload_with_list_coercion,
)

_LOCATION_FIELD_NAMES = (
    "location_kind",
    "function",
    "address",
    "start_address",
    "end_address",
    "file",
    "line",
    "start_line",
    "end_line",
)
_WORKFLOW_STEP_OPTION_MAP = {
    "--setup-step": "--step",
    "--setup-step-label": "--step-label",
    "--setup-step-arg": "--step-arg",
}


def provided_fields(namespace: argparse.Namespace, tracked_fields: Iterable[str]) -> set[str]:
    """Return the tracked CLI fields that were explicitly provided on one namespace."""

    namespace_fields = namespace.__dict__
    return {
        field_name
        for field_name in tracked_fields
        if field_name in namespace_fields
    }


def parse_session_start_input(namespace: argparse.Namespace) -> SessionStartInput:
    """Parse one session-start namespace into a typed input object."""

    return SessionStartInput(
        program=namespace.program,
        args=tuple(namespace.args),
        init_commands=tuple(namespace.init_commands),
        env=collapse_key_value_entries(namespace.env),
        core=namespace.core,
        gdb_path=namespace.gdb_path,
        working_dir=namespace.working_dir,
    )


def parse_session_query_input(namespace: argparse.Namespace) -> SessionQueryInput:
    """Parse one session-query namespace into a typed input object."""

    return SessionQueryInput(
        action=cast(SessionQueryAction, namespace.action),
        session_id=namespace.__dict__.get("session_id"),
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
        executable=namespace.__dict__.get("executable"),
        make_current=namespace.__dict__.get("make_current"),
        inferior_id=namespace.__dict__.get("inferior_id"),
        mode=cast(InferiorFollowForkMode | None, namespace.__dict__.get("mode")),
        enabled=namespace.__dict__.get("enabled"),
    )


def parse_execution_manage_input(namespace: argparse.Namespace) -> ExecutionManageInput:
    """Parse one execution-manage namespace into a typed input object."""

    wait_until = cast(ExecutionWaitUntil | None, namespace.__dict__.get("wait_until"))
    wait_timeout_sec = namespace.__dict__.get("wait_timeout_sec")
    wait = (
        ExecutionWaitInput(until=wait_until, timeout_sec=wait_timeout_sec)
        if wait_until is not None or wait_timeout_sec is not None
        else None
    )
    return ExecutionManageInput(
        action=cast(ExecutionManageAction, namespace.action),
        session_id=namespace.session_id,
        args=tuple(namespace.__dict__.get("args", ())),
        wait=wait,
        timeout_sec=namespace.__dict__.get("timeout_sec"),
        stop_reasons=tuple(namespace.__dict__.get("stop_reasons", ())),
    )


def parse_context_query_input(namespace: argparse.Namespace) -> ContextQueryInput:
    """Parse one context-query namespace into a typed input object."""

    return ContextQueryInput(
        action=cast(ContextQueryAction, namespace.action),
        session_id=namespace.session_id,
        thread_id=namespace.__dict__.get("thread_id"),
        frame=namespace.__dict__.get("frame"),
        max_frames=namespace.__dict__.get("max_frames"),
    )


def parse_context_manage_input(namespace: argparse.Namespace) -> ContextManageInput:
    """Parse one context-manage namespace into a typed input object."""

    return ContextManageInput(
        action=cast(ContextManageAction, namespace.action),
        session_id=namespace.session_id,
        thread_id=namespace.__dict__.get("thread_id"),
        frame=namespace.__dict__.get("frame"),
    )


def _parse_location_input(namespace: argparse.Namespace) -> LocationInput | None:
    location_kind = namespace.__dict__.get("location_kind")
    if location_kind is None:
        return None

    return LocationInput(
        kind=cast(LocationKind, location_kind.replace("-", "_")),
        function=namespace.__dict__.get("function"),
        address=namespace.__dict__.get("address"),
        start_address=namespace.__dict__.get("start_address"),
        end_address=namespace.__dict__.get("end_address"),
        file=namespace.__dict__.get("file"),
        line=namespace.__dict__.get("line"),
        start_line=namespace.__dict__.get("start_line"),
        end_line=namespace.__dict__.get("end_line"),
    )


def _validate_workflow_step(
    tool_name: str,
    arguments: dict[str, object],
    *,
    index: int,
) -> dict[str, object]:
    if "session_id" in arguments:
        raise CliUsageError(
            f"Workflow step {index} ({tool_name}) must not include session_id; "
            "it is inherited from the enclosing command"
        )

    if tool_name == "gdb_session_query" and arguments.get("action") == "list":
        raise CliUsageError("gdb_session_query(action=list) is not valid inside workflow steps")

    if tool_name == "gdb_session_manage":
        raise CliUsageError("gdb_session_manage is not valid inside workflow steps")

    if tool_name in {"gdb_workflow_batch", "gdb_run_until_failure"}:
        raise CliUsageError(f"{tool_name} is not valid inside workflow steps")

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
    current_tool: str | None = None
    current_label: str | None = None
    current_arguments: dict[str, object] | None = None

    for option, value in normalized_events:
        if option == "--step":
            current_tool = cast(str, value)
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
        number=namespace.__dict__.get("number"),
        kinds=tuple(cast(tuple[BreakpointKind, ...], tuple(namespace.__dict__.get("kinds", ())))),
        enabled=namespace.__dict__.get("enabled"),
    )


def parse_breakpoint_manage_input(namespace: argparse.Namespace) -> BreakpointManageInput:
    """Parse one breakpoint-manage namespace into a typed input object."""

    namespace_fields = namespace.__dict__
    breakpoint_kind = cast(BreakpointKind | None, namespace.__dict__.get("breakpoint_kind"))
    temporary_explicit = "temporary" in namespace_fields
    has_breakpoint_fields = (
        breakpoint_kind is not None
        or namespace_fields.get("location") is not None
        or namespace_fields.get("expression") is not None
        or namespace_fields.get("access") is not None
        or namespace_fields.get("event") is not None
        or namespace_fields.get("argument") is not None
        or temporary_explicit
    )
    breakpoint = (
        BreakpointCreateInput(
            kind=breakpoint_kind or "code",
            location=namespace_fields.get("location"),
            expression=namespace_fields.get("expression"),
            access=cast(BreakpointAccess | None, namespace_fields.get("access")),
            event=cast(BreakpointEvent | None, namespace_fields.get("event")),
            argument=namespace_fields.get("argument"),
            condition=namespace_fields.get("condition"),
            temporary=bool(namespace_fields.get("temporary", False)),
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
        number=namespace_fields.get("number"),
        condition=namespace_fields.get("condition"),
        clear_condition=namespace_fields.get("clear_condition"),
    )


def parse_inspect_query_input(namespace: argparse.Namespace) -> InspectQueryInput:
    """Parse one inspect-query namespace into a typed input object."""

    namespace_fields = namespace.__dict__
    action = cast(InspectQueryAction, namespace.action)
    location_fields = tuple(
        field_name
        for field_name in _LOCATION_FIELD_NAMES
        if field_name in namespace_fields and not (field_name == "address" and action == "memory")
    )
    return InspectQueryInput(
        action=action,
        session_id=namespace.session_id,
        thread_id=namespace_fields.get("thread_id"),
        frame=namespace_fields.get("frame"),
        expression=namespace_fields.get("expression"),
        register_numbers=tuple(namespace_fields.get("register_numbers", ())),
        register_names=tuple(namespace_fields.get("register_names", ())),
        include_vector_registers=namespace_fields.get("include_vector_registers"),
        max_registers=namespace_fields.get("max_registers"),
        value_format=cast(RegisterValueFormat | None, namespace_fields.get("value_format")),
        memory_address=namespace_fields.get("address") if action == "memory" else None,
        count=namespace_fields.get("count"),
        offset=namespace_fields.get("offset"),
        location=_parse_location_input(namespace),
        instruction_count=namespace_fields.get("instruction_count"),
        mode=cast(DisassemblyMode | None, namespace_fields.get("mode")),
        context_before=namespace_fields.get("context_before"),
        context_after=namespace_fields.get("context_after"),
        location_fields=location_fields,
    )


def parse_workflow_batch_input(namespace: argparse.Namespace) -> WorkflowBatchInput:
    """Parse one workflow-batch namespace into a typed input object."""

    steps = parse_step_inputs(
        namespace.__dict__.get("step_events"),
        required=True,
        step_flag="--step",
    )
    assert steps is not None
    return WorkflowBatchInput(
        session_id=namespace.session_id,
        steps=steps,
        fail_fast=namespace.__dict__.get("fail_fast"),
        capture_stop_events=namespace.__dict__.get("capture_stop_events"),
    )


def parse_run_until_failure_input(namespace: argparse.Namespace) -> RunUntilFailureInput:
    """Parse one run-until-failure namespace into a typed input object."""

    namespace_fields = namespace.__dict__
    return RunUntilFailureInput(
        startup_program=namespace_fields.get("startup_program"),
        startup_args=tuple(namespace.startup_args),
        startup_init_commands=tuple(namespace.startup_init_commands),
        startup_env=collapse_key_value_entries(namespace.startup_env),
        startup_gdb_path=namespace_fields.get("startup_gdb_path"),
        startup_working_dir=namespace_fields.get("startup_working_dir"),
        startup_core=namespace_fields.get("startup_core"),
        setup_steps=parse_step_inputs(
            namespace_fields.get("setup_step_events"),
            required=False,
            step_flag="--setup-step",
        ),
        run_args=tuple(namespace.run_args),
        run_timeout_sec=namespace_fields.get("run_timeout_sec"),
        max_iterations=namespace_fields.get("max_iterations"),
        failure_on_error=namespace_fields.get("failure_on_error"),
        failure_on_timeout=namespace_fields.get("failure_on_timeout"),
        failure_stop_reasons=(
            tuple(cast(tuple[str, ...], namespace_fields.get("failure_stop_reasons", ())))
            if "failure_stop_reasons" in namespace_fields
            else None
        ),
        failure_execution_states=(
            tuple(cast(tuple[str, ...], namespace_fields.get("failure_execution_states", ())))
            if "failure_execution_states" in namespace_fields
            else None
        ),
        failure_exit_codes=(
            tuple(cast(tuple[int, ...], namespace_fields.get("failure_exit_codes", ())))
            if "failure_exit_codes" in namespace_fields
            else None
        ),
        failure_result_text_regex=namespace_fields.get("failure_result_text_regex"),
        capture_enabled=namespace_fields.get("capture_enabled"),
        capture_output_dir=namespace_fields.get("capture_output_dir"),
        capture_bundle_name_prefix=namespace_fields.get("capture_bundle_name_prefix"),
        capture_bundle_name=namespace_fields.get("capture_bundle_name"),
        capture_expressions=tuple(namespace.capture_expressions),
        capture_memory_ranges=tuple(namespace.capture_memory_ranges),
        capture_max_frames=namespace_fields.get("capture_max_frames"),
        capture_include_threads=namespace_fields.get("capture_include_threads"),
        capture_include_backtraces=namespace_fields.get("capture_include_backtraces"),
        capture_include_frame=namespace_fields.get("capture_include_frame"),
        capture_include_variables=namespace_fields.get("capture_include_variables"),
        capture_include_registers=namespace_fields.get("capture_include_registers"),
        capture_include_transcript=namespace_fields.get("capture_include_transcript"),
        capture_include_stop_history=namespace_fields.get("capture_include_stop_history"),
    )
