"""CLI parser and payload builders for dedicated command tools."""

from __future__ import annotations

import argparse


from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    CallFunctionArgs,
    ExecuteCommandArgs,
)

from ..parsers import (
    validate_model_payload,
)
from .common import _configure_session_id_and_timeout


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
