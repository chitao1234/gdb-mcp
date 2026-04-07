"""Static CLI specs for MCP tools."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, cast

from pydantic import BaseModel, ValidationError

from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    BATCH_STEP_TOOL_MODELS,
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

from .builders.session import build_session_query_payload, build_session_start_payload
from .input_parsers import parse_session_query_input, parse_session_start_input
from .inputs import SessionQueryInput, SessionStartInput
from .parsers import (
    AppendTaggedValue,
    add_boolean_flag,
    assign_dotted_value,
    collapse_key_value_entries,
    CliUsageError,
    dotted_assignment,
    ensure_action_fields,
    format_cli_flag,
    format_validation_error,
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
_LOCATION_TRACKED_FIELDS = frozenset(
    {
        "location_kind",
        "function",
        "address",
        "start_address",
        "end_address",
        "file",
        "line",
        "start_line",
        "end_line",
    }
)
_WORKFLOW_STEP_OPTION_MAP = {
    "--setup-step": "--step",
    "--setup-step-label": "--step-label",
    "--setup-step-arg": "--step-arg",
}


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


def _build_context_override(namespace: argparse.Namespace) -> dict[str, object] | None:
    payload: dict[str, object] = {}
    if hasattr(namespace, "thread_id"):
        payload["thread_id"] = namespace.thread_id
    if hasattr(namespace, "frame"):
        payload["frame"] = namespace.frame
    return payload or None


def _build_location(namespace: argparse.Namespace, *, context: str) -> dict[str, object]:
    if not hasattr(namespace, "location_kind"):
        raise CliUsageError(f"--location-kind required with {context}")

    kind = namespace.location_kind
    if kind == "current":
        _reject_fields(
            namespace,
            context="--location-kind current",
            forbidden_fields=(
                "function",
                "address",
                "start_address",
                "end_address",
                "file",
                "line",
                "start_line",
                "end_line",
            ),
        )
        return {"kind": "current"}

    if kind == "function":
        _require_fields(
            namespace,
            context="--location-kind function",
            required_fields=("function",),
        )
        _reject_fields(
            namespace,
            context="--location-kind function",
            forbidden_fields=(
                "address",
                "start_address",
                "end_address",
                "file",
                "line",
                "start_line",
                "end_line",
            ),
        )
        return {"kind": "function", "function": namespace.function}

    if kind == "address":
        _require_fields(
            namespace,
            context="--location-kind address",
            required_fields=("address",),
        )
        _reject_fields(
            namespace,
            context="--location-kind address",
            forbidden_fields=(
                "function",
                "start_address",
                "end_address",
                "file",
                "line",
                "start_line",
                "end_line",
            ),
        )
        return {"kind": "address", "address": namespace.address}

    if kind == "address-range":
        _require_fields(
            namespace,
            context="--location-kind address-range",
            required_fields=("start_address", "end_address"),
        )
        _reject_fields(
            namespace,
            context="--location-kind address-range",
            forbidden_fields=(
                "function",
                "address",
                "file",
                "line",
                "start_line",
                "end_line",
            ),
        )
        return {
            "kind": "address_range",
            "start_address": namespace.start_address,
            "end_address": namespace.end_address,
        }

    if kind == "file-line":
        _require_fields(
            namespace,
            context="--location-kind file-line",
            required_fields=("file", "line"),
        )
        _reject_fields(
            namespace,
            context="--location-kind file-line",
            forbidden_fields=(
                "function",
                "address",
                "start_address",
                "end_address",
                "start_line",
                "end_line",
            ),
        )
        return {"kind": "file_line", "file": namespace.file, "line": namespace.line}

    _require_fields(
        namespace,
        context="--location-kind file-range",
        required_fields=("file", "start_line", "end_line"),
    )
    _reject_fields(
        namespace,
        context="--location-kind file-range",
        forbidden_fields=(
            "function",
            "address",
            "start_address",
            "end_address",
            "line",
        ),
    )
    return {
        "kind": "file_range",
        "file": namespace.file,
        "start_line": namespace.start_line,
        "end_line": namespace.end_line,
    }


def _build_breakpoint_create_payload(namespace: argparse.Namespace) -> dict[str, object]:
    if not hasattr(namespace, "breakpoint_kind"):
        raise CliUsageError("--breakpoint-kind required with --action create")

    kind = namespace.breakpoint_kind
    if kind == "code":
        _require_fields(
            namespace,
            context="--breakpoint-kind code",
            required_fields=("location",),
        )
        _reject_fields(
            namespace,
            context="--breakpoint-kind code",
            forbidden_fields=("expression", "access", "event", "argument"),
        )
        payload: dict[str, object] = {"kind": "code", "location": namespace.location}
        if hasattr(namespace, "condition"):
            payload["condition"] = namespace.condition
        if getattr(namespace, "temporary", False):
            payload["temporary"] = True
        return payload

    if kind == "watch":
        _require_fields(
            namespace,
            context="--breakpoint-kind watch",
            required_fields=("expression",),
        )
        _reject_fields(
            namespace,
            context="--breakpoint-kind watch",
            forbidden_fields=("location", "condition", "temporary", "event", "argument"),
        )
        payload = {"kind": "watch", "expression": namespace.expression}
        if hasattr(namespace, "access"):
            payload["access"] = namespace.access
        return payload

    _require_fields(
        namespace,
        context="--breakpoint-kind catch",
        required_fields=("event",),
    )
    _reject_fields(
        namespace,
        context="--breakpoint-kind catch",
        forbidden_fields=("location", "condition", "expression", "access"),
    )
    payload = {"kind": "catch", "event": namespace.event}
    if hasattr(namespace, "argument"):
        payload["argument"] = namespace.argument
    if getattr(namespace, "temporary", False):
        payload["temporary"] = True
    return payload


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
        validated = _validate_workflow_step_payload(model, {"session_id": 1, **arguments})
    except ValidationError as exc:
        raise CliUsageError(
            f"Invalid workflow step {index} ({tool_name}): {format_validation_error(exc)}"
        ) from exc

    validated.pop("session_id", None)
    return validated


def _coerce_single_item_list_path(
    payload: dict[str, object],
    path: tuple[object, ...],
    *,
    action: str | None,
) -> bool:
    normalized_path: list[str] = []
    for position, segment in enumerate(path):
        if not isinstance(segment, str):
            return False
        if position == 0 and action is not None and segment == action:
            continue
        normalized_path.append(segment)

    if not normalized_path:
        return False

    current: object = payload
    for segment in normalized_path[:-1]:
        if not isinstance(current, dict):
            return False
        current = current.get(segment)
        if current is None:
            return False

    if not isinstance(current, dict):
        return False

    leaf = normalized_path[-1]
    existing = current.get(leaf)
    if existing is None or isinstance(existing, list):
        return False

    current[leaf] = [existing]
    return True


def _validate_workflow_step_payload(
    model: type[BaseModel],
    payload: dict[str, object],
) -> dict[str, object]:
    candidate = cast(dict[str, object], deepcopy(payload))

    while True:
        try:
            return validate_model_payload(model, candidate)
        except ValidationError as exc:
            errors = exc.errors()
            if not errors or any(error.get("type") != "list_type" for error in errors):
                raise

            action_value = candidate.get("action")
            action: str | None = action_value if isinstance(action_value, str) else None
            changed = False
            for error in errors:
                location = cast(tuple[object, ...], tuple(error.get("loc", ())))
                changed = _coerce_single_item_list_path(
                    candidate,
                    location,
                    action=action,
                ) or changed

            if not changed:
                raise


def _build_step_list(step_events: list[tuple[str, object]] | None) -> list[dict[str, object]]:
    if not step_events:
        raise CliUsageError("At least one --step is required")

    steps: list[dict[str, object]] = []
    current_step: dict[str, object] | None = None

    for option, value in step_events:
        if option == "--step":
            current_step = {"tool": value, "arguments": {}}
            steps.append(current_step)
            continue

        if current_step is None:
            raise CliUsageError(f"{option} requires a preceding --step")

        if option == "--step-label":
            current_step["label"] = value
            continue

        if option == "--step-arg":
            path, scalar = cast(tuple[str, object], value)
            arguments = cast(dict[str, object], current_step["arguments"])
            assign_dotted_value(arguments, path, scalar)

    validated_steps: list[dict[str, object]] = []
    for index, step in enumerate(steps):
        step_arguments = cast(dict[str, object], step["arguments"])
        validated_step = {
            "tool": step["tool"],
            "arguments": _validate_workflow_step(
                str(step["tool"]),
                dict(step_arguments),
                index=index,
            ),
        }
        if "label" in step:
            validated_step["label"] = step["label"]
        validated_steps.append(validated_step)

    return validated_steps


def _remap_step_events(step_events: list[tuple[str, object]] | None) -> list[tuple[str, object]] | None:
    if step_events is None:
        return None
    return [(_WORKFLOW_STEP_OPTION_MAP[option], value) for option, value in step_events]


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


def _empty_payload(_: argparse.Namespace) -> dict[str, object]:
    return {}


def _execution_wait(namespace: argparse.Namespace) -> dict[str, object] | None:
    wait_until = getattr(namespace, "wait_until", None)
    wait_timeout_sec = getattr(namespace, "wait_timeout_sec", None)
    if wait_until is None and wait_timeout_sec is None:
        return None

    payload: dict[str, object] = {}
    if wait_until is not None:
        payload["until"] = wait_until
    if wait_timeout_sec is not None:
        payload["timeout_sec"] = wait_timeout_sec
    return payload


def _execution_run_payload(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {}
    args = getattr(namespace, "args", [])
    if args:
        payload["args"] = args

    wait_payload = _execution_wait(namespace)
    if wait_payload is not None:
        payload["wait"] = wait_payload

    return payload


def _execution_control_payload(namespace: argparse.Namespace) -> dict[str, object]:
    wait_payload = _execution_wait(namespace)
    if wait_payload is None:
        return {}
    return {"wait": wait_payload}


def _execution_wait_for_stop_payload(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {"timeout_sec": getattr(namespace, "timeout_sec", 30)}
    stop_reasons = getattr(namespace, "stop_reasons", [])
    if stop_reasons:
        payload["stop_reasons"] = stop_reasons
    return payload


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


def _build_inferior_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=InferiorQueryArgs,
        variants={
            "list": ActionVariant(build_fields=lambda _: {"query": {}}),
            "current": ActionVariant(build_fields=lambda _: {"query": {}}),
        },
    )


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


def _build_inferior_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=InferiorManageArgs,
        variants={
            "create": ActionVariant(
                build_fields=lambda ns: {
                    "inferior": {
                        "executable": getattr(ns, "executable", None),
                        "make_current": getattr(ns, "make_current", False),
                    }
                },
                allowed_fields=frozenset({"executable", "make_current"}),
            ),
            "remove": ActionVariant(
                build_fields=lambda ns: {
                    "inferior": {"inferior_id": getattr(ns, "inferior_id", None)}
                },
                allowed_fields=frozenset({"inferior_id"}),
            ),
            "select": ActionVariant(
                build_fields=lambda ns: {
                    "inferior": {"inferior_id": getattr(ns, "inferior_id", None)}
                },
                allowed_fields=frozenset({"inferior_id"}),
            ),
            "set_follow_fork_mode": ActionVariant(
                build_fields=lambda ns: {"inferior": {"mode": getattr(ns, "mode", None)}},
                allowed_fields=frozenset({"mode"}),
            ),
            "set_detach_on_fork": ActionVariant(
                build_fields=lambda ns: {
                    "inferior": {"enabled": getattr(ns, "enabled", True)}
                },
                allowed_fields=frozenset({"enabled"}),
            ),
        },
        tracked_fields=frozenset({"inferior_id", "executable", "make_current", "mode", "enabled"}),
    )


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


def _build_execution_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=ExecutionManageArgs,
        variants={
            "run": ActionVariant(
                build_fields=lambda ns: {"execution": _execution_run_payload(ns)},
                allowed_fields=frozenset({"args", "wait_until", "wait_timeout_sec"}),
            ),
            "continue": ActionVariant(
                build_fields=lambda ns: {"execution": _execution_control_payload(ns)},
                allowed_fields=frozenset({"wait_until", "wait_timeout_sec"}),
            ),
            "interrupt": ActionVariant(
                build_fields=lambda _: {"execution": {}},
                allowed_fields=frozenset(),
            ),
            "step": ActionVariant(
                build_fields=lambda ns: {"execution": _execution_control_payload(ns)},
                allowed_fields=frozenset({"wait_until", "wait_timeout_sec"}),
            ),
            "next": ActionVariant(
                build_fields=lambda ns: {"execution": _execution_control_payload(ns)},
                allowed_fields=frozenset({"wait_until", "wait_timeout_sec"}),
            ),
            "finish": ActionVariant(
                build_fields=lambda ns: {"execution": _execution_control_payload(ns)},
                allowed_fields=frozenset({"wait_until", "wait_timeout_sec"}),
            ),
            "wait_for_stop": ActionVariant(
                build_fields=lambda ns: {"execution": _execution_wait_for_stop_payload(ns)},
                allowed_fields=frozenset({"timeout_sec", "stop_reasons"}),
            ),
        },
        tracked_fields=frozenset({"args", "wait_until", "wait_timeout_sec", "timeout_sec", "stop_reasons"}),
    )


def _configure_context_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["threads", "backtrace", "frame"])
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=argparse.SUPPRESS)


def _build_context_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=ContextQueryArgs,
        variants={
            "threads": ActionVariant(
                build_fields=lambda _: {"query": {}},
                allowed_fields=frozenset(),
            ),
            "backtrace": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        "thread_id": getattr(ns, "thread_id", None),
                        "max_frames": getattr(ns, "max_frames", 100),
                    }
                },
                allowed_fields=frozenset({"thread_id", "max_frames"}),
            ),
            "frame": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        "thread_id": getattr(ns, "thread_id", None),
                        "frame": getattr(ns, "frame", None),
                    }
                },
                allowed_fields=frozenset({"thread_id", "frame"}),
            ),
        },
        tracked_fields=frozenset({"thread_id", "frame", "max_frames"}),
    )


def _configure_context_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["select_thread", "select_frame"])
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)


def _build_context_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=ContextManageArgs,
        variants={
            "select_thread": ActionVariant(
                build_fields=lambda ns: {
                    "context": {"thread_id": getattr(ns, "thread_id", None)}
                },
                allowed_fields=frozenset({"thread_id"}),
            ),
            "select_frame": ActionVariant(
                build_fields=lambda ns: {"context": {"frame": getattr(ns, "frame", None)}},
                allowed_fields=frozenset({"frame"}),
            ),
        },
        tracked_fields=frozenset({"thread_id", "frame"}),
    )


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


def _build_breakpoint_query(namespace: argparse.Namespace) -> dict[str, object]:
    payload = _build_action_arguments(
        namespace,
        model=BreakpointQueryArgs,
        variants={
            "list": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        **({"kinds": ns.kinds} if getattr(ns, "kinds", None) else {}),
                        **({"enabled": ns.enabled} if hasattr(ns, "enabled") else {}),
                    }
                },
                allowed_fields=frozenset({"kinds", "enabled"}),
            ),
            "get": ActionVariant(
                build_fields=lambda ns: {"query": {"number": getattr(ns, "number", None)}},
                allowed_fields=frozenset({"number"}),
            ),
        },
        tracked_fields=frozenset({"number", "kinds", "enabled"}),
    )
    query = payload.get("query")
    if isinstance(query, dict) and query.get("kinds") == []:
        query.pop("kinds", None)
    return payload


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


def _build_breakpoint_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=BreakpointManageArgs,
        variants={
            "create": ActionVariant(
                build_fields=lambda ns: {"breakpoint": _build_breakpoint_create_payload(ns)},
                allowed_fields=frozenset(
                    {
                        "breakpoint_kind",
                        "location",
                        "expression",
                        "access",
                        "event",
                        "argument",
                        "temporary",
                        "condition",
                    }
                ),
            ),
            "update": ActionVariant(
                build_fields=lambda ns: {
                    "breakpoint": {"number": getattr(ns, "number", None)},
                    "changes": {
                        **({"condition": ns.condition} if hasattr(ns, "condition") else {}),
                        **(
                            {"clear_condition": ns.clear_condition}
                            if hasattr(ns, "clear_condition")
                            else {}
                        ),
                    },
                },
                allowed_fields=frozenset({"number", "condition", "clear_condition"}),
            ),
            "delete": ActionVariant(
                build_fields=lambda ns: {"breakpoint": {"number": getattr(ns, "number", None)}},
                allowed_fields=frozenset({"number"}),
            ),
            "enable": ActionVariant(
                build_fields=lambda ns: {"breakpoint": {"number": getattr(ns, "number", None)}},
                allowed_fields=frozenset({"number"}),
            ),
            "disable": ActionVariant(
                build_fields=lambda ns: {"breakpoint": {"number": getattr(ns, "number", None)}},
                allowed_fields=frozenset({"number"}),
            ),
        },
        tracked_fields=frozenset(
            {
                "breakpoint_kind",
                "location",
                "expression",
                "access",
                "event",
                "argument",
                "temporary",
                "number",
                "condition",
                "clear_condition",
            }
        ),
    )


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


def _build_inspect_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=InspectQueryArgs,
        variants={
            "evaluate": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        **({"context": _build_context_override(ns)} if _build_context_override(ns) is not None else {}),
                        "expression": getattr(ns, "expression", None),
                    }
                },
                allowed_fields=frozenset({"thread_id", "frame", "expression"}),
            ),
            "variables": ActionVariant(
                build_fields=lambda ns: {
                    "query": (
                        {"context": _build_context_override(ns)}
                        if _build_context_override(ns) is not None
                        else {}
                    )
                },
                allowed_fields=frozenset({"thread_id", "frame"}),
            ),
            "registers": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        **({"context": _build_context_override(ns)} if _build_context_override(ns) is not None else {}),
                        **({"register_numbers": ns.register_numbers} if hasattr(ns, "register_numbers") else {}),
                        **({"register_names": ns.register_names} if hasattr(ns, "register_names") else {}),
                        **(
                            {"include_vector_registers": ns.include_vector_registers}
                            if hasattr(ns, "include_vector_registers")
                            else {}
                        ),
                        **({"max_registers": ns.max_registers} if hasattr(ns, "max_registers") else {}),
                        **({"value_format": ns.value_format} if hasattr(ns, "value_format") else {}),
                    }
                },
                allowed_fields=frozenset(
                    {
                        "thread_id",
                        "frame",
                        "register_numbers",
                        "register_names",
                        "include_vector_registers",
                        "max_registers",
                        "value_format",
                    }
                ),
            ),
            "memory": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        "address": getattr(ns, "address", None),
                        "count": getattr(ns, "count", None),
                        **({"offset": ns.offset} if hasattr(ns, "offset") else {}),
                    }
                },
                allowed_fields=frozenset({"address", "count", "offset"}),
            ),
            "disassembly": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        **({"context": _build_context_override(ns)} if _build_context_override(ns) is not None else {}),
                        "location": _build_location(ns, context="--action disassembly"),
                        **(
                            {"instruction_count": ns.instruction_count}
                            if hasattr(ns, "instruction_count")
                            else {}
                        ),
                        **({"mode": ns.mode} if hasattr(ns, "mode") else {}),
                    }
                },
                allowed_fields=frozenset(
                    {
                        "thread_id",
                        "frame",
                        "instruction_count",
                        "mode",
                    }
                )
                | _LOCATION_TRACKED_FIELDS,
            ),
            "source": ActionVariant(
                build_fields=lambda ns: {
                    "query": {
                        **({"context": _build_context_override(ns)} if _build_context_override(ns) is not None else {}),
                        "location": _build_location(ns, context="--action source"),
                        **({"context_before": ns.context_before} if hasattr(ns, "context_before") else {}),
                        **({"context_after": ns.context_after} if hasattr(ns, "context_after") else {}),
                    }
                },
                allowed_fields=frozenset(
                    {
                        "thread_id",
                        "frame",
                        "context_before",
                        "context_after",
                    }
                )
                | _LOCATION_TRACKED_FIELDS,
            ),
        },
        tracked_fields=frozenset(
            {
                "thread_id",
                "frame",
                "expression",
                "register_numbers",
                "register_names",
                "include_vector_registers",
                "max_registers",
                "value_format",
                "address",
                "count",
                "offset",
                "location_kind",
                "function",
                "start_address",
                "end_address",
                "file",
                "line",
                "start_line",
                "end_line",
                "instruction_count",
                "mode",
                "context_before",
                "context_after",
            }
        ),
    )


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


def _build_workflow_batch(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": namespace.session_id,
        "steps": _build_step_list(getattr(namespace, "step_events", None)),
    }
    if hasattr(namespace, "fail_fast"):
        payload["fail_fast"] = namespace.fail_fast
    if hasattr(namespace, "capture_stop_events"):
        payload["capture_stop_events"] = namespace.capture_stop_events
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


def _build_run_until_failure(namespace: argparse.Namespace) -> dict[str, object]:
    setup_step_events = getattr(namespace, "setup_step_events", None)
    startup: dict[str, object] = {
        **({"program": namespace.startup_program} if hasattr(namespace, "startup_program") else {}),
        **({"args": namespace.startup_args} if namespace.startup_args else {}),
        **(
            {"init_commands": namespace.startup_init_commands}
            if namespace.startup_init_commands
            else {}
        ),
        **(
            {"env": collapse_key_value_entries(namespace.startup_env)}
            if namespace.startup_env
            else {}
        ),
        **({"gdb_path": namespace.startup_gdb_path} if hasattr(namespace, "startup_gdb_path") else {}),
        **(
            {"working_dir": namespace.startup_working_dir}
            if hasattr(namespace, "startup_working_dir")
            else {}
        ),
        **({"core": namespace.startup_core} if hasattr(namespace, "startup_core") else {}),
    }
    failure: dict[str, object] = {
        **(
            {"failure_on_error": namespace.failure_on_error}
            if hasattr(namespace, "failure_on_error")
            else {}
        ),
        **(
            {"failure_on_timeout": namespace.failure_on_timeout}
            if hasattr(namespace, "failure_on_timeout")
            else {}
        ),
        **(
            {"stop_reasons": namespace.failure_stop_reasons}
            if hasattr(namespace, "failure_stop_reasons")
            else {}
        ),
        **(
            {"execution_states": namespace.failure_execution_states}
            if hasattr(namespace, "failure_execution_states")
            else {}
        ),
        **(
            {"exit_codes": namespace.failure_exit_codes}
            if hasattr(namespace, "failure_exit_codes")
            else {}
        ),
        **(
            {"result_text_regex": namespace.failure_result_text_regex}
            if hasattr(namespace, "failure_result_text_regex")
            else {}
        ),
    }
    capture: dict[str, object] = {
        **(
            {"enabled": namespace.capture_enabled}
            if hasattr(namespace, "capture_enabled")
            else {}
        ),
        **(
            {"output_dir": namespace.capture_output_dir}
            if hasattr(namespace, "capture_output_dir")
            else {}
        ),
        **(
            {"bundle_name_prefix": namespace.capture_bundle_name_prefix}
            if hasattr(namespace, "capture_bundle_name_prefix")
            else {}
        ),
        **(
            {"bundle_name": namespace.capture_bundle_name}
            if hasattr(namespace, "capture_bundle_name")
            else {}
        ),
        **(
            {"expressions": namespace.capture_expressions}
            if namespace.capture_expressions
            else {}
        ),
        **(
            {"memory_ranges": namespace.capture_memory_ranges}
            if namespace.capture_memory_ranges
            else {}
        ),
        **(
            {"max_frames": namespace.capture_max_frames}
            if hasattr(namespace, "capture_max_frames")
            else {}
        ),
        **(
            {"include_threads": namespace.capture_include_threads}
            if hasattr(namespace, "capture_include_threads")
            else {}
        ),
        **(
            {"include_backtraces": namespace.capture_include_backtraces}
            if hasattr(namespace, "capture_include_backtraces")
            else {}
        ),
        **(
            {"include_frame": namespace.capture_include_frame}
            if hasattr(namespace, "capture_include_frame")
            else {}
        ),
        **(
            {"include_variables": namespace.capture_include_variables}
            if hasattr(namespace, "capture_include_variables")
            else {}
        ),
        **(
            {"include_registers": namespace.capture_include_registers}
            if hasattr(namespace, "capture_include_registers")
            else {}
        ),
        **(
            {"include_transcript": namespace.capture_include_transcript}
            if hasattr(namespace, "capture_include_transcript")
            else {}
        ),
        **(
            {"include_stop_history": namespace.capture_include_stop_history}
            if hasattr(namespace, "capture_include_stop_history")
            else {}
        ),
    }
    payload: dict[str, object] = {
        **({"startup": startup} if startup else {}),
        **(
            {
                "setup_steps": _build_step_list(
                    _remap_step_events(setup_step_events)
                )
            }
            if setup_step_events
            else {}
        ),
        **({"run_args": namespace.run_args} if namespace.run_args else {}),
        **(
            {"run_timeout_sec": namespace.run_timeout_sec}
            if hasattr(namespace, "run_timeout_sec")
            else {}
        ),
        **(
            {"max_iterations": namespace.max_iterations}
            if hasattr(namespace, "max_iterations")
            else {}
        ),
        **({"failure": failure} if failure else {}),
        **({"capture": capture} if capture else {}),
    }
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
        parse_input=_parse_namespace,
        build_arguments=_build_inferior_query,
        render_human=render_action_payload,
    )),
    "gdb_inferior_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_inferior_manage",
        configure_parser=_configure_inferior_manage,
        parse_input=_parse_namespace,
        build_arguments=_build_inferior_manage,
        render_human=render_action_payload,
    )),
    "gdb_execution_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_execution_manage",
        configure_parser=_configure_execution_manage,
        parse_input=_parse_namespace,
        build_arguments=_build_execution_manage,
        render_human=render_action_payload,
    )),
    "gdb_breakpoint_query": _register_tool_spec(ToolCliSpec(
        name="gdb_breakpoint_query",
        configure_parser=_configure_breakpoint_query,
        parse_input=_parse_namespace,
        build_arguments=_build_breakpoint_query,
        render_human=render_action_payload,
    )),
    "gdb_breakpoint_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_breakpoint_manage",
        configure_parser=_configure_breakpoint_manage,
        parse_input=_parse_namespace,
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
        parse_input=_parse_namespace,
        build_arguments=_build_context_query,
        render_human=render_action_payload,
    )),
    "gdb_context_manage": _register_tool_spec(ToolCliSpec(
        name="gdb_context_manage",
        configure_parser=_configure_context_manage,
        parse_input=_parse_namespace,
        build_arguments=_build_context_manage,
        render_human=render_action_payload,
    )),
    "gdb_inspect_query": _register_tool_spec(ToolCliSpec(
        name="gdb_inspect_query",
        configure_parser=_configure_inspect_query,
        parse_input=_parse_namespace,
        build_arguments=_build_inspect_query,
        render_human=render_action_payload,
    )),
    "gdb_workflow_batch": _register_tool_spec(ToolCliSpec(
        name="gdb_workflow_batch",
        configure_parser=_configure_workflow_batch,
        parse_input=_parse_namespace,
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
        parse_input=_parse_namespace,
        build_arguments=_build_run_until_failure,
        render_human=render_mapping,
    )),
}
