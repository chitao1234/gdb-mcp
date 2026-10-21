"""CLI parser and payload builders for dedicated command tools."""

from __future__ import annotations

import argparse

from gdb_mcp.contracts import DEBUGGER_GDB, DEBUGGER_NAMES
from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    CallFunctionArgs,
    ConnectSessionArgs,
    ExecuteCommandArgs,
)

from ..namespace import (
    integer,
    one_of,
    optional_integer,
    optional_text,
    snapshot,
    text,
    text_tuple,
)
from ..parsers import (
    validate_model_payload,
)
from .common import _configure_session_id_and_timeout


def _configure_session_connect(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--endpoint",
        required=True,
        help=(
            "Endpoint owned by another process: GDB/MI (tcp://, unix://, pty://) or "
            "LLDB remote stub (connect://host:port)"
        ),
    )
    parser.add_argument("--debugger", choices=["gdb", "lldb"], default="gdb")
    parser.add_argument("--program", help="Executable for an LLDB remote target")
    parser.add_argument("--debugger-path", help="LLDB binary or installation path")
    parser.add_argument("--init-command", dest="init_commands", action="append", default=[])


def _build_session_connect(namespace: argparse.Namespace) -> dict[str, object]:
    values = snapshot(namespace)
    payload: dict[str, object] = {
        "endpoint": text(values, "endpoint"),
        "init_commands": list(text_tuple(values, "init_commands")) or None,
    }
    if one_of(values, "debugger", DEBUGGER_NAMES) != DEBUGGER_GDB:
        payload["debugger"] = one_of(values, "debugger", DEBUGGER_NAMES)
    program = optional_text(values, "program")
    if program is not None:
        payload["program"] = program
    debugger_path = optional_text(values, "debugger_path")
    if debugger_path is not None:
        payload["debugger_path"] = debugger_path
    return validate_model_payload(ConnectSessionArgs, payload)


def _configure_execute_command(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--command", required=True)


def _build_execute_command(namespace: argparse.Namespace) -> dict[str, object]:
    values = snapshot(namespace)
    return validate_model_payload(
        ExecuteCommandArgs,
        {
            "session_id": optional_integer(values, "session_id"),
            "command": text(values, "command"),
            "timeout_sec": integer(values, "timeout_sec"),
        },
    )


def _configure_attach_process(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--pid", type=int, required=True)


def _build_attach_process(namespace: argparse.Namespace) -> dict[str, object]:
    values = snapshot(namespace)
    return validate_model_payload(
        AttachProcessArgs,
        {
            "session_id": optional_integer(values, "session_id"),
            "pid": integer(values, "pid"),
            "timeout_sec": integer(values, "timeout_sec"),
        },
    )


def _configure_call_function(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--function-call", required=True)


def _build_call_function(namespace: argparse.Namespace) -> dict[str, object]:
    values = snapshot(namespace)
    return validate_model_payload(
        CallFunctionArgs,
        {
            "session_id": optional_integer(values, "session_id"),
            "function_call": text(values, "function_call"),
            "timeout_sec": integer(values, "timeout_sec"),
        },
    )
