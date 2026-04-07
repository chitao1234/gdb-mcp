"""Static CLI specs for MCP tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, cast

from pydantic import BaseModel

from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    BatchArgs,
    BreakpointManageArgs,
    BreakpointQueryArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ContextManageArgs,
    ContextQueryArgs,
    ExecutionManageArgs,
    ExecuteCommandArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    InspectQueryArgs,
    RunUntilFailureArgs,
    SessionManageArgs,
    SessionQueryArgs,
    StartSessionArgs,
    build_tool_definitions,
)

from .builders.breakpoint import build_breakpoint_manage_payload, build_breakpoint_query_payload
from .builders.context import build_context_manage_payload, build_context_query_payload
from .builders.execution import build_execution_manage_payload
from .builders.inferior import build_inferior_manage_payload, build_inferior_query_payload
from .builders.inspect import build_inspect_query_payload
from .builders.session import build_session_query_payload, build_session_start_payload
from .builders.workflow import build_run_until_failure_payload, build_workflow_batch_payload
from .input_parsers import (
    parse_breakpoint_manage_input,
    parse_breakpoint_query_input,
    parse_context_manage_input,
    parse_context_query_input,
    parse_execution_manage_input,
    parse_inferior_manage_input,
    parse_inferior_query_input,
    parse_inspect_query_input,
    parse_run_until_failure_input,
    parse_session_query_input,
    parse_session_start_input,
    parse_workflow_batch_input,
)
from .inputs import (
    BreakpointCreateInput,
    BreakpointManageInput,
    BreakpointQueryInput,
    ContextManageInput,
    ContextQueryInput,
    ExecutionManageInput,
    InferiorManageInput,
    InferiorQueryInput,
    InspectQueryInput,
    LocationInput,
    RunUntilFailureInput,
    SessionQueryInput,
    SessionStartInput,
    WorkflowBatchInput,
)
from .parsers import (
    AppendTaggedValue,
    add_boolean_flag,
    CliUsageError,
    dotted_assignment,
    ensure_action_fields,
    format_cli_flag,
    key_value_entry,
    validate_model_payload,
)
from .renderers import render_action_payload, render_mapping, render_session_start

_ToolInputT = TypeVar("_ToolInputT")


@dataclass(frozen=True)
class ToolCliSpec(Generic[_ToolInputT]):
    """One CLI mapping for a public MCP tool."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    parse_input: Callable[[argparse.Namespace], _ToolInputT]
    build_arguments: Callable[[_ToolInputT], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


@dataclass(frozen=True)
class RegisteredToolCliSpec:
    """Runtime CLI spec with erased parsed-input type."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    parse_input: Callable[[argparse.Namespace], object]
    build_arguments: Callable[[object], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


@dataclass(frozen=True)
class ActionVariant:
    """One CLI action variant within a nested MCP action envelope."""

    build_fields: Callable[[argparse.Namespace], dict[str, object]]
    allowed_fields: frozenset[str] = frozenset()


TOOL_DESCRIPTIONS = {tool.name: tool.description or "" for tool in build_tool_definitions()}

TOOL_HELP_DESCRIPTIONS = {
    tool.name: (tool.description or "").replace("%", "%%")
    for tool in build_tool_definitions()
}

_BREAKPOINT_KINDS = ["code", "watch", "catch"]
_BREAKPOINT_EVENTS = [
    "throw",
    "rethrow",
    "catch",
    "exec",
    "fork",
    "vfork",
    "load",
    "unload",
    "signal",
    "syscall",
]
_LOCATION_KIND_CHOICES = [
    "current",
    "function",
    "address",
    "address-range",
    "file-line",
    "file-range",
]


def _parse_namespace(namespace: argparse.Namespace) -> argparse.Namespace:
    return namespace


def _erase_builder(
    builder: Callable[[_ToolInputT], dict[str, object]],
) -> Callable[[object], dict[str, object]]:
    def build_arguments(parsed_input: object) -> dict[str, object]:
        return builder(cast(_ToolInputT, parsed_input))

    return build_arguments


def _register_tool_spec(spec: ToolCliSpec[_ToolInputT]) -> RegisteredToolCliSpec:
    return RegisteredToolCliSpec(
        name=spec.name,
        configure_parser=spec.configure_parser,
        parse_input=spec.parse_input,
        build_arguments=_erase_builder(spec.build_arguments),
        render_human=spec.render_human,
    )


def _require_fields(
    namespace: argparse.Namespace,
    *,
    context: str,
    required_fields: tuple[str, ...],
) -> None:
    missing = [format_cli_flag(field_name) for field_name in required_fields if not hasattr(namespace, field_name)]
    if missing:
        joined = ", ".join(missing)
        raise CliUsageError(f"{joined} required with {context}")


def _reject_fields(
    namespace: argparse.Namespace,
    *,
    context: str,
    forbidden_fields: tuple[str, ...],
) -> None:
    unexpected = [
        format_cli_flag(field_name)
        for field_name in forbidden_fields
        if hasattr(namespace, field_name)
    ]
    if unexpected:
        joined = ", ".join(sorted(unexpected))
        raise CliUsageError(f"{joined} not valid with {context}")


def _configure_session_start(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--program")
    parser.add_argument("--arg", dest="args", action="append", default=[])
    parser.add_argument("--init-command", dest="init_commands", action="append", default=[])
    parser.add_argument("--env", dest="env", action="append", type=key_value_entry, default=[])
    parser.add_argument("--gdb-path")
    parser.add_argument("--working-dir")
    parser.add_argument("--core")


def _build_session_start(typed_input: SessionStartInput) -> dict[str, object]:
    payload = build_session_start_payload(typed_input)
    return validate_model_payload(StartSessionArgs, payload)


def _configure_session_id_and_timeout(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--timeout-sec", type=int, default=30)


def _add_session_id(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    if required:
        parser.add_argument("--session-id", type=int, required=True)
        return

    parser.add_argument("--session-id", type=int, required=False, default=argparse.SUPPRESS)


def _add_action(parser: argparse.ArgumentParser, *, choices: list[str]) -> None:
    parser.add_argument("--action", required=True, choices=choices)


def _build_action_arguments(
    namespace: argparse.Namespace,
    *,
    model: type[BaseModel],
    variants: dict[str, ActionVariant],
    tracked_fields: frozenset[str] = frozenset(),
) -> dict[str, object]:
    variant = variants[namespace.action]
    ensure_action_fields(
        namespace,
        action=namespace.action,
        tracked_fields=tracked_fields,
        allowed_fields=variant.allowed_fields,
    )
    payload: dict[str, object] = {"action": namespace.action}
    if hasattr(namespace, "session_id") and namespace.session_id is not None:
        payload["session_id"] = namespace.session_id
    variant_fields = variant.build_fields(namespace)
    reserved_fields = {"action", "session_id"} & set(variant_fields)
    if reserved_fields:
        reserved_list = ", ".join(sorted(reserved_fields))
        raise CliUsageError(f"ActionVariant cannot override reserved fields: {reserved_list}")
    payload.update(variant_fields)
    return validate_model_payload(model, payload)


def _raise_invalid_action_flags(action: str, invalid_flags: list[str]) -> None:
    if invalid_flags:
        raise CliUsageError(
            f"{', '.join(sorted(invalid_flags))} not valid with --action {action}"
        )


def _validate_inferior_manage_input(typed_input: InferiorManageInput) -> None:
    invalid_flags: list[str] = []
    if typed_input.action == "create":
        if typed_input.inferior_id is not None:
            invalid_flags.append("--inferior-id")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.enabled is not None:
            invalid_flags.append("--enabled")
    elif typed_input.action in {"remove", "select"}:
        if typed_input.executable is not None:
            invalid_flags.append("--executable")
        if typed_input.make_current is not None:
            invalid_flags.append("--make-current")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.enabled is not None:
            invalid_flags.append("--enabled")
    elif typed_input.action == "set_follow_fork_mode":
        if typed_input.inferior_id is not None:
            invalid_flags.append("--inferior-id")
        if typed_input.executable is not None:
            invalid_flags.append("--executable")
        if typed_input.make_current is not None:
            invalid_flags.append("--make-current")
        if typed_input.enabled is not None:
            invalid_flags.append("--enabled")
    elif typed_input.action == "set_detach_on_fork":
        if typed_input.inferior_id is not None:
            invalid_flags.append("--inferior-id")
        if typed_input.executable is not None:
            invalid_flags.append("--executable")
        if typed_input.make_current is not None:
            invalid_flags.append("--make-current")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _validate_execution_manage_input(typed_input: ExecutionManageInput) -> None:
    invalid_flags: list[str] = []
    has_wait_until = typed_input.wait is not None and typed_input.wait.until is not None
    has_wait_timeout = typed_input.wait is not None and typed_input.wait.timeout_sec is not None

    if typed_input.action == "run":
        if typed_input.timeout_sec is not None:
            invalid_flags.append("--timeout-sec")
        if typed_input.stop_reasons:
            invalid_flags.append("--stop-reason")
    elif typed_input.action in {"continue", "step", "next", "finish"}:
        if typed_input.args:
            invalid_flags.append("--arg")
        if typed_input.timeout_sec is not None:
            invalid_flags.append("--timeout-sec")
        if typed_input.stop_reasons:
            invalid_flags.append("--stop-reason")
    elif typed_input.action == "interrupt":
        if typed_input.args:
            invalid_flags.append("--arg")
        if has_wait_until:
            invalid_flags.append("--wait-until")
        if has_wait_timeout:
            invalid_flags.append("--wait-timeout-sec")
        if typed_input.timeout_sec is not None:
            invalid_flags.append("--timeout-sec")
        if typed_input.stop_reasons:
            invalid_flags.append("--stop-reason")
    elif typed_input.action == "wait_for_stop":
        if typed_input.args:
            invalid_flags.append("--arg")
        if has_wait_until:
            invalid_flags.append("--wait-until")
        if has_wait_timeout:
            invalid_flags.append("--wait-timeout-sec")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _validate_context_query_input(typed_input: ContextQueryInput) -> None:
    invalid_flags: list[str] = []
    if typed_input.action == "threads":
        if typed_input.thread_id is not None:
            invalid_flags.append("--thread-id")
        if typed_input.frame is not None:
            invalid_flags.append("--frame")
        if typed_input.max_frames is not None:
            invalid_flags.append("--max-frames")
    elif typed_input.action == "backtrace":
        if typed_input.frame is not None:
            invalid_flags.append("--frame")
    elif typed_input.action == "frame":
        if typed_input.max_frames is not None:
            invalid_flags.append("--max-frames")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _validate_context_manage_input(typed_input: ContextManageInput) -> None:
    invalid_flags: list[str] = []
    if typed_input.action == "select_thread" and typed_input.frame is not None:
        invalid_flags.append("--frame")
    elif typed_input.action == "select_frame" and typed_input.thread_id is not None:
        invalid_flags.append("--thread-id")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _validate_breakpoint_query_input(typed_input: BreakpointQueryInput) -> None:
    invalid_flags: list[str] = []
    if typed_input.action == "list":
        if typed_input.number is not None:
            invalid_flags.append("--number")
    elif typed_input.action == "get":
        if typed_input.kinds:
            invalid_flags.append("--kind")
        if typed_input.enabled is not None:
            invalid_flags.append("--enabled")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _validate_breakpoint_create_input(typed_input: BreakpointCreateInput) -> None:
    if typed_input.kind == "code":
        if typed_input.location is None:
            raise CliUsageError("--location required with --breakpoint-kind code")
        invalid_flags: list[str] = []
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.access is not None:
            invalid_flags.append("--access")
        if typed_input.event is not None:
            invalid_flags.append("--event")
        if typed_input.argument is not None:
            invalid_flags.append("--argument")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --breakpoint-kind code"
            )
        return

    if typed_input.kind == "watch":
        if typed_input.expression is None:
            raise CliUsageError("--expression required with --breakpoint-kind watch")
        invalid_flags = []
        if typed_input.location is not None:
            invalid_flags.append("--location")
        if typed_input.condition is not None:
            invalid_flags.append("--condition")
        if typed_input.temporary_explicit:
            invalid_flags.append("--temporary")
        if typed_input.event is not None:
            invalid_flags.append("--event")
        if typed_input.argument is not None:
            invalid_flags.append("--argument")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --breakpoint-kind watch"
            )
        return

    if typed_input.event is None:
        raise CliUsageError("--event required with --breakpoint-kind catch")

    invalid_flags = []
    if typed_input.location is not None:
        invalid_flags.append("--location")
    if typed_input.condition is not None:
        invalid_flags.append("--condition")
    if typed_input.expression is not None:
        invalid_flags.append("--expression")
    if typed_input.access is not None:
        invalid_flags.append("--access")
    if invalid_flags:
        raise CliUsageError(
            f"{', '.join(sorted(invalid_flags))} not valid with --breakpoint-kind catch"
        )


def _validate_breakpoint_manage_input(typed_input: BreakpointManageInput) -> None:
    invalid_flags: list[str] = []
    if typed_input.action == "create":
        if typed_input.number is not None:
            invalid_flags.append("--number")
        if typed_input.clear_condition is not None:
            invalid_flags.append("--clear-condition")
        _raise_invalid_action_flags(typed_input.action, invalid_flags)
        if typed_input.breakpoint is None or not typed_input.breakpoint.kind_explicit:
            raise CliUsageError("--breakpoint-kind required with --action create")
        _validate_breakpoint_create_input(typed_input.breakpoint)
        return

    if typed_input.breakpoint is not None:
        if typed_input.breakpoint.kind_explicit:
            invalid_flags.append("--breakpoint-kind")
        if typed_input.breakpoint.location is not None:
            invalid_flags.append("--location")
        if typed_input.breakpoint.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.breakpoint.access is not None:
            invalid_flags.append("--access")
        if typed_input.breakpoint.event is not None:
            invalid_flags.append("--event")
        if typed_input.breakpoint.argument is not None:
            invalid_flags.append("--argument")
        if typed_input.breakpoint.temporary_explicit:
            invalid_flags.append("--temporary")

    if typed_input.action in {"delete", "enable", "disable"}:
        if typed_input.condition is not None:
            invalid_flags.append("--condition")
        if typed_input.clear_condition is not None:
            invalid_flags.append("--clear-condition")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _validate_location_input(typed_input: LocationInput | None, *, context: str) -> None:
    if typed_input is None:
        raise CliUsageError(f"--location-kind required with {context}")

    if typed_input.kind == "current":
        invalid_flags: list[str] = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind current"
            )
        return

    if typed_input.kind == "function":
        if typed_input.function is None:
            raise CliUsageError("--function required with --location-kind function")
        invalid_flags = []
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind function"
            )
        return

    if typed_input.kind == "address":
        if typed_input.address is None:
            raise CliUsageError("--address required with --location-kind address")
        invalid_flags = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind address"
            )
        return

    if typed_input.kind == "address_range":
        if typed_input.start_address is None:
            raise CliUsageError("--start-address required with --location-kind address-range")
        if typed_input.end_address is None:
            raise CliUsageError("--end-address required with --location-kind address-range")
        invalid_flags = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind address-range"
            )
        return

    if typed_input.kind == "file_line":
        if typed_input.file is None:
            raise CliUsageError("--file required with --location-kind file-line")
        if typed_input.line is None:
            raise CliUsageError("--line required with --location-kind file-line")
        invalid_flags = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind file-line"
            )
        return

    if typed_input.file is None:
        raise CliUsageError("--file required with --location-kind file-range")
    if typed_input.start_line is None:
        raise CliUsageError("--start-line required with --location-kind file-range")
    if typed_input.end_line is None:
        raise CliUsageError("--end-line required with --location-kind file-range")
    invalid_flags = []
    if typed_input.function is not None:
        invalid_flags.append("--function")
    if typed_input.address is not None:
        invalid_flags.append("--address")
    if typed_input.start_address is not None:
        invalid_flags.append("--start-address")
    if typed_input.end_address is not None:
        invalid_flags.append("--end-address")
    if typed_input.line is not None:
        invalid_flags.append("--line")
    if invalid_flags:
        raise CliUsageError(
            f"{', '.join(sorted(invalid_flags))} not valid with --location-kind file-range"
        )


def _validate_inspect_query_input(typed_input: InspectQueryInput) -> None:
    invalid_flags: list[str] = []
    location_flags = [format_cli_flag(field_name) for field_name in typed_input.location_fields]
    if typed_input.action == "evaluate":
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "variables":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "registers":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "memory":
        if typed_input.thread_id is not None:
            invalid_flags.append("--thread-id")
        if typed_input.frame is not None:
            invalid_flags.append("--frame")
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "disassembly":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
        _raise_invalid_action_flags(typed_input.action, invalid_flags)
        _validate_location_input(typed_input.location, context="--action disassembly")
        return
    elif typed_input.action == "source":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        _raise_invalid_action_flags(typed_input.action, invalid_flags)
        _validate_location_input(typed_input.location, context="--action source")
        return

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _empty_payload(_: argparse.Namespace) -> dict[str, object]:
    return {}


def _configure_session_query(parser: argparse.ArgumentParser) -> None:
    _add_action(parser, choices=["list", "status"])
    _add_session_id(parser, required=False)


def _build_session_query(typed_input: SessionQueryInput) -> dict[str, object]:
    payload = build_session_query_payload(typed_input)
    if typed_input.action == "list" and typed_input.session_id is not None:
        raise CliUsageError("--session-id not valid with --action list")
    return validate_model_payload(SessionQueryArgs, payload)


def _configure_session_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["stop"])


def _build_session_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=SessionManageArgs,
        variants={"stop": ActionVariant(build_fields=lambda _: {"session": {}})},
    )


def _configure_inferior_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["list", "current"])


def _build_inferior_query(typed_input: InferiorQueryInput) -> dict[str, object]:
    payload = build_inferior_query_payload(typed_input)
    return validate_model_payload(InferiorQueryArgs, payload)


def _configure_inferior_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(
        parser,
        choices=["create", "remove", "select", "set_follow_fork_mode", "set_detach_on_fork"],
    )
    parser.add_argument("--inferior-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--executable", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "make_current",
        default=False,
        help_text="Select the new inferior after create",
        suppress_default=True,
    )
    parser.add_argument("--mode", choices=["parent", "child"], default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "enabled",
        default=True,
        help_text="Detach from the non-followed fork",
        suppress_default=True,
    )


def _build_inferior_manage(typed_input: InferiorManageInput) -> dict[str, object]:
    _validate_inferior_manage_input(typed_input)
    payload = build_inferior_manage_payload(typed_input)
    return validate_model_payload(InferiorManageArgs, payload)


def _configure_execution_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(
        parser,
        choices=["run", "continue", "interrupt", "step", "next", "finish", "wait_for_stop"],
    )
    parser.add_argument("--arg", dest="args", action="append", default=argparse.SUPPRESS)
    parser.add_argument("--wait-until", choices=["acknowledged", "stop"], default=argparse.SUPPRESS)
    parser.add_argument("--wait-timeout-sec", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--timeout-sec", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--stop-reason", dest="stop_reasons", action="append", default=argparse.SUPPRESS)


def _build_execution_manage(typed_input: ExecutionManageInput) -> dict[str, object]:
    _validate_execution_manage_input(typed_input)
    payload = build_execution_manage_payload(typed_input)
    return validate_model_payload(ExecutionManageArgs, payload)


def _configure_context_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["threads", "backtrace", "frame"])
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=argparse.SUPPRESS)


def _build_context_query(typed_input: ContextQueryInput) -> dict[str, object]:
    _validate_context_query_input(typed_input)
    payload = build_context_query_payload(typed_input)
    return validate_model_payload(ContextQueryArgs, payload)


def _configure_context_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["select_thread", "select_frame"])
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)


def _build_context_manage(typed_input: ContextManageInput) -> dict[str, object]:
    _validate_context_manage_input(typed_input)
    payload = build_context_manage_payload(typed_input)
    return validate_model_payload(ContextManageArgs, payload)


def _configure_execute_command(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--command", required=True)


def _build_execute_command(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        ExecuteCommandArgs,
        {
            "session_id": namespace.session_id,
            "command": namespace.command,
            "timeout_sec": namespace.timeout_sec,
        },
    )


def _configure_attach_process(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--pid", type=int, required=True)


def _build_attach_process(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        AttachProcessArgs,
        {
            "session_id": namespace.session_id,
            "pid": namespace.pid,
            "timeout_sec": namespace.timeout_sec,
        },
    )


def _configure_call_function(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--function-call", required=True)


def _build_call_function(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        CallFunctionArgs,
        {
            "session_id": namespace.session_id,
            "function_call": namespace.function_call,
            "timeout_sec": namespace.timeout_sec,
        },
    )


def _configure_capture_bundle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--bundle-name")
    parser.add_argument("--expression", dest="expressions", action="append", default=[])
    parser.add_argument("--memory-range", dest="memory_ranges", action="append", default=[])
    parser.add_argument("--max-frames", type=int, default=100)
    add_boolean_flag(parser, "include_threads", default=True, help_text="Capture thread inventory")
    add_boolean_flag(
        parser,
        "include_backtraces",
        default=True,
        help_text="Capture thread backtraces",
    )
    add_boolean_flag(parser, "include_frame", default=True, help_text="Capture current frame")
    add_boolean_flag(
        parser,
        "include_variables",
        default=True,
        help_text="Capture variables",
    )
    add_boolean_flag(
        parser,
        "include_registers",
        default=True,
        help_text="Capture registers",
    )
    add_boolean_flag(
        parser,
        "include_transcript",
        default=True,
        help_text="Capture transcript",
    )
    add_boolean_flag(
        parser,
        "include_stop_history",
        default=True,
        help_text="Capture stop history",
    )


def _build_capture_bundle(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        CaptureBundleArgs,
        {
            "session_id": namespace.session_id,
            "output_dir": namespace.output_dir,
            "bundle_name": namespace.bundle_name,
            "expressions": namespace.expressions,
            "memory_ranges": namespace.memory_ranges,
            "max_frames": namespace.max_frames,
            "include_threads": namespace.include_threads,
            "include_backtraces": namespace.include_backtraces,
            "include_frame": namespace.include_frame,
            "include_variables": namespace.include_variables,
            "include_registers": namespace.include_registers,
            "include_transcript": namespace.include_transcript,
            "include_stop_history": namespace.include_stop_history,
        },
    )


def _configure_breakpoint_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["list", "get"])
    parser.add_argument("--number", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--kind",
        dest="kinds",
        action="append",
        choices=_BREAKPOINT_KINDS,
        default=argparse.SUPPRESS,
    )
    add_boolean_flag(
        parser,
        "enabled",
        default=True,
        help_text="Filter breakpoints by enabled state",
        suppress_default=True,
    )


def _build_breakpoint_query(typed_input: BreakpointQueryInput) -> dict[str, object]:
    _validate_breakpoint_query_input(typed_input)
    payload = build_breakpoint_query_payload(typed_input)
    validated = validate_model_payload(BreakpointQueryArgs, payload)
    query = validated.get("query")
    if isinstance(query, dict) and query.get("kinds") == []:
        query.pop("kinds", None)
    return validated


def _configure_breakpoint_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["create", "update", "delete", "enable", "disable"])
    parser.add_argument(
        "--breakpoint-kind",
        choices=_BREAKPOINT_KINDS,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--location", default=argparse.SUPPRESS)
    parser.add_argument("--expression", default=argparse.SUPPRESS)
    parser.add_argument(
        "--access",
        choices=["write", "read", "access"],
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--event", choices=_BREAKPOINT_EVENTS, default=argparse.SUPPRESS)
    parser.add_argument("--argument", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "temporary",
        default=False,
        help_text="Create a temporary breakpoint or catchpoint",
        suppress_default=True,
    )
    parser.add_argument("--number", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--condition", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "clear_condition",
        default=False,
        help_text="Clear the existing breakpoint condition",
        suppress_default=True,
    )


def _build_breakpoint_manage(typed_input: BreakpointManageInput) -> dict[str, object]:
    _validate_breakpoint_manage_input(typed_input)
    payload = build_breakpoint_manage_payload(typed_input)
    return validate_model_payload(BreakpointManageArgs, payload)


def _configure_inspect_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(
        parser,
        choices=["evaluate", "variables", "registers", "memory", "disassembly", "source"],
    )
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--expression", default=argparse.SUPPRESS)
    parser.add_argument(
        "--register-number",
        dest="register_numbers",
        action="append",
        type=int,
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--register-name",
        dest="register_names",
        action="append",
        default=argparse.SUPPRESS,
    )
    add_boolean_flag(
        parser,
        "include_vector_registers",
        default=True,
        help_text="Include vector and SIMD registers",
        suppress_default=True,
    )
    parser.add_argument("--max-registers", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--value-format", choices=["hex", "natural"], default=argparse.SUPPRESS)
    parser.add_argument("--address", default=argparse.SUPPRESS)
    parser.add_argument("--count", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--offset", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--location-kind",
        choices=_LOCATION_KIND_CHOICES,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--function", default=argparse.SUPPRESS)
    parser.add_argument("--start-address", default=argparse.SUPPRESS)
    parser.add_argument("--end-address", default=argparse.SUPPRESS)
    parser.add_argument("--file", default=argparse.SUPPRESS)
    parser.add_argument("--line", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--start-line", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--end-line", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--instruction-count", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=["assembly", "mixed"], default=argparse.SUPPRESS)
    parser.add_argument("--context-before", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--context-after", type=int, default=argparse.SUPPRESS)


def _build_inspect_query(typed_input: InspectQueryInput) -> dict[str, object]:
    _validate_inspect_query_input(typed_input)
    payload = build_inspect_query_payload(typed_input)
    return validate_model_payload(InspectQueryArgs, payload)


def _configure_workflow_batch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--step", dest="step_events", action=AppendTaggedValue)
    parser.add_argument("--step-label", dest="step_events", action=AppendTaggedValue)
    parser.add_argument(
        "--step-arg",
        dest="step_events",
        action=AppendTaggedValue,
        type=dotted_assignment,
    )
    add_boolean_flag(
        parser,
        "fail_fast",
        default=True,
        help_text="Stop executing later steps after the first error",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_stop_events",
        default=True,
        help_text="Include new stop events produced by batch steps",
        suppress_default=True,
    )


def _build_workflow_batch(typed_input: WorkflowBatchInput) -> dict[str, object]:
    payload = build_workflow_batch_payload(typed_input)
    return validate_model_payload(BatchArgs, payload)


def _configure_run_until_failure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--startup-program", default=argparse.SUPPRESS)
    parser.add_argument("--startup-arg", dest="startup_args", action="append", default=[])
    parser.add_argument(
        "--startup-init-command",
        dest="startup_init_commands",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--startup-env",
        dest="startup_env",
        action="append",
        type=key_value_entry,
        default=[],
    )
    parser.add_argument("--startup-gdb-path", default=argparse.SUPPRESS)
    parser.add_argument("--startup-working-dir", default=argparse.SUPPRESS)
    parser.add_argument("--startup-core", default=argparse.SUPPRESS)
    parser.add_argument("--setup-step", dest="setup_step_events", action=AppendTaggedValue)
    parser.add_argument("--setup-step-label", dest="setup_step_events", action=AppendTaggedValue)
    parser.add_argument(
        "--setup-step-arg",
        dest="setup_step_events",
        action=AppendTaggedValue,
        type=dotted_assignment,
    )
    parser.add_argument("--run-arg", dest="run_args", action="append", default=[])
    parser.add_argument("--run-timeout-sec", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--max-iterations", type=int, default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "failure_on_error",
        default=True,
        help_text="Treat startup or run errors as matching failures",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "failure_on_timeout",
        default=True,
        help_text="Treat run timeouts as matching failures",
        suppress_default=True,
    )
    parser.add_argument(
        "--failure-stop-reason",
        dest="failure_stop_reasons",
        action="append",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--failure-execution-state",
        dest="failure_execution_states",
        action="append",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--failure-exit-code",
        dest="failure_exit_codes",
        action="append",
        type=int,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--failure-result-text-regex", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "capture_enabled",
        default=True,
        help_text="Write a capture bundle when a failure matches",
        suppress_default=True,
    )
    parser.add_argument("--capture-output-dir", default=argparse.SUPPRESS)
    parser.add_argument("--capture-bundle-name-prefix", default=argparse.SUPPRESS)
    parser.add_argument("--capture-bundle-name", default=argparse.SUPPRESS)
    parser.add_argument(
        "--capture-expression",
        dest="capture_expressions",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--capture-memory-range",
        dest="capture_memory_ranges",
        action="append",
        default=[],
    )
    parser.add_argument("--capture-max-frames", type=int, default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "capture_include_threads",
        default=True,
        help_text="Capture thread inventory",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_backtraces",
        default=True,
        help_text="Capture thread backtraces",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_frame",
        default=True,
        help_text="Capture the selected frame",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_variables",
        default=True,
        help_text="Capture variables for the selected context",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_registers",
        default=True,
        help_text="Capture registers for the selected context",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_transcript",
        default=True,
        help_text="Capture the bounded command transcript",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_stop_history",
        default=True,
        help_text="Capture the bounded stop-event history",
        suppress_default=True,
    )


def _build_run_until_failure(typed_input: RunUntilFailureInput) -> dict[str, object]:
    payload = build_run_until_failure_payload(typed_input)
    return validate_model_payload(RunUntilFailureArgs, payload)


CLIENT_TOOL_SPECS: dict[str, RegisteredToolCliSpec] = {
    "gdb_session_start": _register_tool_spec(ToolCliSpec(
        name="gdb_session_start",
        configure_parser=_configure_session_start,
        parse_input=parse_session_start_input,
        build_arguments=_build_session_start,
        render_human=render_session_start,
    )),
    "gdb_session_query": _register_tool_spec(ToolCliSpec(
        name="gdb_session_query",
        configure_parser=_configure_session_query,
        parse_input=parse_session_query_input,
        build_arguments=_build_session_query,
        render_human=render_action_payload,
    )),
    "gdb_session_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_session_manage",
        configure_parser=_configure_session_manage,
        parse_input=_parse_namespace,
        build_arguments=_build_session_manage,
        render_human=render_action_payload,
    )),
    "gdb_inferior_query": _register_tool_spec(ToolCliSpec(
        name="gdb_inferior_query",
        configure_parser=_configure_inferior_query,
        parse_input=parse_inferior_query_input,
        build_arguments=_build_inferior_query,
        render_human=render_action_payload,
    )),
    "gdb_inferior_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_inferior_manage",
        configure_parser=_configure_inferior_manage,
        parse_input=parse_inferior_manage_input,
        build_arguments=_build_inferior_manage,
        render_human=render_action_payload,
    )),
    "gdb_execution_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_execution_manage",
        configure_parser=_configure_execution_manage,
        parse_input=parse_execution_manage_input,
        build_arguments=_build_execution_manage,
        render_human=render_action_payload,
    )),
    "gdb_breakpoint_query": _register_tool_spec(ToolCliSpec(
        name="gdb_breakpoint_query",
        configure_parser=_configure_breakpoint_query,
        parse_input=parse_breakpoint_query_input,
        build_arguments=_build_breakpoint_query,
        render_human=render_action_payload,
    )),
    "gdb_breakpoint_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_breakpoint_manage",
        configure_parser=_configure_breakpoint_manage,
        parse_input=parse_breakpoint_manage_input,
        build_arguments=_build_breakpoint_manage,
        render_human=render_action_payload,
    )),
    "gdb_execute_command": _register_tool_spec(ToolCliSpec(
        name="gdb_execute_command",
        configure_parser=_configure_execute_command,
        parse_input=_parse_namespace,
        build_arguments=_build_execute_command,
        render_human=render_mapping,
    )),
    "gdb_attach_process": _register_tool_spec(ToolCliSpec(
        name="gdb_attach_process",
        configure_parser=_configure_attach_process,
        parse_input=_parse_namespace,
        build_arguments=_build_attach_process,
        render_human=render_mapping,
    )),
    "gdb_context_query": _register_tool_spec(ToolCliSpec(
        name="gdb_context_query",
        configure_parser=_configure_context_query,
        parse_input=parse_context_query_input,
        build_arguments=_build_context_query,
        render_human=render_action_payload,
    )),
    "gdb_context_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_context_manage",
        configure_parser=_configure_context_manage,
        parse_input=parse_context_manage_input,
        build_arguments=_build_context_manage,
        render_human=render_action_payload,
    )),
    "gdb_inspect_query": _register_tool_spec(ToolCliSpec(
        name="gdb_inspect_query",
        configure_parser=_configure_inspect_query,
        parse_input=parse_inspect_query_input,
        build_arguments=_build_inspect_query,
        render_human=render_action_payload,
    )),
    "gdb_workflow_batch": _register_tool_spec(ToolCliSpec(
        name="gdb_workflow_batch",
        configure_parser=_configure_workflow_batch,
        parse_input=parse_workflow_batch_input,
        build_arguments=_build_workflow_batch,
        render_human=render_mapping,
    )),
    "gdb_call_function": _register_tool_spec(ToolCliSpec(
        name="gdb_call_function",
        configure_parser=_configure_call_function,
        parse_input=_parse_namespace,
        build_arguments=_build_call_function,
        render_human=render_mapping,
    )),
    "gdb_capture_bundle": _register_tool_spec(ToolCliSpec(
        name="gdb_capture_bundle",
        configure_parser=_configure_capture_bundle,
        parse_input=_parse_namespace,
        build_arguments=_build_capture_bundle,
        render_human=render_mapping,
    )),
    "gdb_run_until_failure": _register_tool_spec(ToolCliSpec(
        name="gdb_run_until_failure",
        configure_parser=_configure_run_until_failure,
        parse_input=parse_run_until_failure_input,
        build_arguments=_build_run_until_failure,
        render_human=render_mapping,
    )),
}
