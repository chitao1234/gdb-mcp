"""Static CLI specs for MCP tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ContextManageArgs,
    ContextQueryArgs,
    ExecutionManageArgs,
    ExecuteCommandArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    SessionManageArgs,
    SessionQueryArgs,
    StartSessionArgs,
    build_tool_definitions,
)

from .parsers import (
    add_boolean_flag,
    collapse_key_value_entries,
    CliUsageError,
    ensure_action_fields,
    key_value_entry,
    validate_model_payload,
)
from .renderers import render_action_payload, render_mapping, render_session_start


@dataclass(frozen=True)
class ToolCliSpec:
    """One CLI mapping for a public MCP tool."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    build_arguments: Callable[[argparse.Namespace], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


@dataclass(frozen=True)
class ActionVariant:
    """One CLI action variant within a nested MCP action envelope."""

    build_fields: Callable[[argparse.Namespace], dict[str, object]]
    allowed_fields: frozenset[str] = frozenset()


TOOL_DESCRIPTIONS = {tool.name: tool.description or "" for tool in build_tool_definitions()}


def _configure_session_start(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--program")
    parser.add_argument("--arg", dest="args", action="append", default=[])
    parser.add_argument("--init-command", dest="init_commands", action="append", default=[])
    parser.add_argument("--env", dest="env", action="append", type=key_value_entry, default=[])
    parser.add_argument("--gdb-path")
    parser.add_argument("--working-dir")
    parser.add_argument("--core")


def _build_session_start(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "program": namespace.program,
        "args": namespace.args or None,
        "init_commands": namespace.init_commands or None,
        "env": collapse_key_value_entries(namespace.env),
        "gdb_path": namespace.gdb_path,
        "working_dir": namespace.working_dir,
        "core": namespace.core,
    }
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


def _build_session_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=SessionQueryArgs,
        variants={
            "list": ActionVariant(
                build_fields=lambda _: {"query": {}},
                allowed_fields=frozenset(),
            ),
            "status": ActionVariant(
                build_fields=lambda _: {"query": {}},
                allowed_fields=frozenset({"session_id"}),
            ),
        },
        tracked_fields=frozenset({"session_id"}),
    )


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


CLIENT_TOOL_SPECS: dict[str, ToolCliSpec] = {
    "gdb_session_start": ToolCliSpec(
        name="gdb_session_start",
        configure_parser=_configure_session_start,
        build_arguments=_build_session_start,
        render_human=render_session_start,
    ),
    "gdb_session_query": ToolCliSpec(
        name="gdb_session_query",
        configure_parser=_configure_session_query,
        build_arguments=_build_session_query,
        render_human=render_action_payload,
    ),
    "gdb_session_manage": ToolCliSpec(
        name="gdb_session_manage",
        configure_parser=_configure_session_manage,
        build_arguments=_build_session_manage,
        render_human=render_action_payload,
    ),
    "gdb_inferior_query": ToolCliSpec(
        name="gdb_inferior_query",
        configure_parser=_configure_inferior_query,
        build_arguments=_build_inferior_query,
        render_human=render_action_payload,
    ),
    "gdb_inferior_manage": ToolCliSpec(
        name="gdb_inferior_manage",
        configure_parser=_configure_inferior_manage,
        build_arguments=_build_inferior_manage,
        render_human=render_action_payload,
    ),
    "gdb_execution_manage": ToolCliSpec(
        name="gdb_execution_manage",
        configure_parser=_configure_execution_manage,
        build_arguments=_build_execution_manage,
        render_human=render_action_payload,
    ),
    "gdb_execute_command": ToolCliSpec(
        name="gdb_execute_command",
        configure_parser=_configure_execute_command,
        build_arguments=_build_execute_command,
        render_human=render_mapping,
    ),
    "gdb_attach_process": ToolCliSpec(
        name="gdb_attach_process",
        configure_parser=_configure_attach_process,
        build_arguments=_build_attach_process,
        render_human=render_mapping,
    ),
    "gdb_context_query": ToolCliSpec(
        name="gdb_context_query",
        configure_parser=_configure_context_query,
        build_arguments=_build_context_query,
        render_human=render_action_payload,
    ),
    "gdb_context_manage": ToolCliSpec(
        name="gdb_context_manage",
        configure_parser=_configure_context_manage,
        build_arguments=_build_context_manage,
        render_human=render_action_payload,
    ),
    "gdb_call_function": ToolCliSpec(
        name="gdb_call_function",
        configure_parser=_configure_call_function,
        build_arguments=_build_call_function,
        render_human=render_mapping,
    ),
    "gdb_capture_bundle": ToolCliSpec(
        name="gdb_capture_bundle",
        configure_parser=_configure_capture_bundle,
        build_arguments=_build_capture_bundle,
        render_human=render_mapping,
    ),
}
