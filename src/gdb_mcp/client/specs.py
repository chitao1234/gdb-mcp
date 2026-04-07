"""Static CLI specs for MCP tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ExecuteCommandArgs,
    StartSessionArgs,
    build_tool_definitions,
)

from .parsers import (
    add_boolean_flag,
    collapse_key_value_entries,
    key_value_entry,
    validate_model_payload,
)
from .renderers import render_mapping, render_session_start


@dataclass(frozen=True)
class ToolCliSpec:
    """One CLI mapping for a public MCP tool."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    build_arguments: Callable[[argparse.Namespace], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


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
