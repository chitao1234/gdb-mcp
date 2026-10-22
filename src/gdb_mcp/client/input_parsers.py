"""Namespace-to-typed-input parsers for the MCP CLI client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, overload

from pydantic import ValidationError

import gdb_mcp.contracts as shared_contracts
from gdb_mcp.contracts import (
    BATCH_STEP_TOOL_NAMES,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
)
from gdb_mcp.mcp.schemas import BATCH_STEP_TOOL_MODELS

from .inputs import (
    RunUntilFailureInput,
    SessionStepInput,
    WorkflowBatchInput,
)
from .namespace import (
    NamespaceLike,
    choice,
    key_value_entries,
    optional_boolean,
    optional_integer,
    optional_integer_tuple,
    optional_text,
    optional_text_tuple,
    snapshot,
    tagged_events,
    text_tuple,
)
from .parsers import (
    CliUsageError,
    StepArgumentEvent,
    StepEvent,
    StepLabelEvent,
    StepToolEvent,
    assign_dotted_value,
    collapse_key_value_entries,
    format_validation_error,
    serialize_model,
    validate_model_with_list_coercion,
)


def _normalize_step_tool_name(tool_name: str) -> str:
    """Accept the gdb_-less tool spelling inside workflow steps."""

    if tool_name.startswith("gdb_"):
        return tool_name
    prefixed = f"gdb_{tool_name}"
    if prefixed in shared_contracts.PUBLIC_TOOL_NAMES:
        return prefixed
    return tool_name


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

    model = BATCH_STEP_TOOL_MODELS[tool_name]

    try:
        validated = validate_model_with_list_coercion(
            model,
            {"session_id": 1, **arguments},
        )
    except ValidationError as exc:
        raise CliUsageError(
            f"Invalid workflow step {index} ({tool_name}): {format_validation_error(exc)}"
        ) from exc

    step_arguments = serialize_model(validated)
    step_arguments.pop("session_id", None)
    return step_arguments


@dataclass
class _PendingStep:
    """One step assembled from flags before its tool and arguments are validated."""

    tool: str
    arguments: dict[str, object]
    label: str | None = None


@overload
def parse_step_inputs(
    step_events: list[StepEvent] | None, *, required: Literal[True], step_flag: str
) -> tuple[SessionStepInput, ...]: ...


@overload
def parse_step_inputs(
    step_events: list[StepEvent] | None, *, required: Literal[False], step_flag: str
) -> tuple[SessionStepInput, ...] | None: ...


def parse_step_inputs(
    step_events: list[StepEvent] | None,
    *,
    required: bool,
    step_flag: str,
) -> tuple[SessionStepInput, ...] | None:
    if not step_events:
        if required:
            raise CliUsageError(f"At least one {step_flag} is required")
        return None

    steps: list[_PendingStep] = []
    current: _PendingStep | None = None

    for event in step_events:
        if isinstance(event, StepToolEvent):
            current = _PendingStep(
                tool=_normalize_step_tool_name(event.tool),
                arguments={},
            )
            steps.append(current)
            continue

        if current is None:
            suffix = "label" if isinstance(event, StepLabelEvent) else "arg"
            raise CliUsageError(f"{step_flag}-{suffix} requires a preceding {step_flag}")

        match event:
            case StepLabelEvent():
                current.label = event.label
            case StepArgumentEvent():
                assign_dotted_value(current.arguments, event.path, event.value)

    validated_steps: list[SessionStepInput] = []
    for index, step in enumerate(steps):
        arguments = _validate_workflow_step(step.tool, dict(step.arguments), index=index)
        validated_steps.append(
            SessionStepInput(
                tool=choice(step.tool, BATCH_STEP_TOOL_NAMES, name="step"),
                label=step.label,
                arguments=arguments,
            )
        )

    return tuple(validated_steps)


def parse_workflow_batch_input(namespace: NamespaceLike) -> WorkflowBatchInput:
    """Parse one workflow-batch namespace into a typed input object."""

    values = snapshot(namespace)
    steps = parse_step_inputs(
        tagged_events(values, "step_events"),
        required=True,
        step_flag="--step",
    )
    return WorkflowBatchInput(
        session_id=optional_integer(values, "session_id"),
        steps=steps,
        fail_fast=optional_boolean(values, "fail_fast"),
        capture_stop_events=optional_boolean(values, "capture_stop_events"),
    )


def parse_run_until_failure_input(namespace: NamespaceLike) -> RunUntilFailureInput:
    """Parse one run-until-failure namespace into a typed input object."""

    values = snapshot(namespace)
    return RunUntilFailureInput(
        startup_debugger=optional_text(values, "startup_debugger"),
        startup_program=optional_text(values, "startup_program"),
        startup_args=text_tuple(values, "startup_args"),
        startup_init_commands=text_tuple(values, "startup_init_commands"),
        startup_env=collapse_key_value_entries(key_value_entries(values, "startup_env")),
        startup_debugger_path=optional_text(values, "startup_debugger_path"),
        startup_working_dir=optional_text(values, "startup_working_dir"),
        startup_core=optional_text(values, "startup_core"),
        setup_steps=parse_step_inputs(
            tagged_events(values, "setup_step_events"),
            required=False,
            step_flag="--setup-step",
        ),
        run_args=text_tuple(values, "run_args"),
        run_timeout_sec=optional_integer(values, "run_timeout_sec"),
        max_iterations=optional_integer(values, "max_iterations"),
        failure_on_error=optional_boolean(values, "failure_on_error"),
        failure_on_timeout=optional_boolean(values, "failure_on_timeout"),
        failure_stop_reasons=optional_text_tuple(values, "failure_stop_reasons"),
        failure_execution_states=optional_text_tuple(values, "failure_execution_states"),
        failure_exit_codes=optional_integer_tuple(values, "failure_exit_codes"),
        failure_result_text_regex=optional_text(values, "failure_result_text_regex"),
        capture_enabled=optional_boolean(values, "capture_enabled"),
        capture_output_dir=optional_text(values, "capture_output_dir"),
        capture_bundle_name_prefix=optional_text(values, "capture_bundle_name_prefix"),
        capture_bundle_name=optional_text(values, "capture_bundle_name"),
        capture_expressions=text_tuple(values, "capture_expressions"),
        capture_memory_ranges=text_tuple(values, "capture_memory_ranges"),
        capture_max_frames=optional_integer(values, "capture_max_frames"),
        capture_include_threads=optional_boolean(values, "capture_include_threads"),
        capture_include_backtraces=optional_boolean(values, "capture_include_backtraces"),
        capture_include_frame=optional_boolean(values, "capture_include_frame"),
        capture_include_variables=optional_boolean(values, "capture_include_variables"),
        capture_include_registers=optional_boolean(values, "capture_include_registers"),
        capture_include_transcript=optional_boolean(values, "capture_include_transcript"),
        capture_include_stop_history=optional_boolean(values, "capture_include_stop_history"),
    )
