"""CLI parser, validation, and payload builders for context tools."""

from __future__ import annotations

import argparse


from gdb_mcp.contracts import (
    CONTEXT_MANAGE_ACTIONS,
    CONTEXT_QUERY_ACTIONS,
)
from gdb_mcp.mcp.schemas import (
    ContextManageArgs,
    ContextQueryArgs,
)

from ..builders.context import build_context_manage_payload, build_context_query_payload
from ..inputs import (
    ContextManageInput,
    ContextQueryInput,
)
from ..parsers import (
    CliUsageError,
    validate_model_payload,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


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
    if typed_input.action == "select_thread":
        if typed_input.thread_id is None:
            raise CliUsageError("--thread-id required with --action select_thread")
        if typed_input.frame is not None:
            invalid_flags.append("--frame")
    elif typed_input.action == "select_frame":
        if typed_input.frame is None:
            raise CliUsageError("--frame required with --action select_frame")
        if typed_input.thread_id is not None:
            invalid_flags.append("--thread-id")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _configure_context_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=CONTEXT_QUERY_ACTIONS)
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=argparse.SUPPRESS)


def _build_context_query(typed_input: ContextQueryInput) -> dict[str, object]:
    _validate_context_query_input(typed_input)
    payload = build_context_query_payload(typed_input)
    return validate_model_payload(ContextQueryArgs, payload)


def _configure_context_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=CONTEXT_MANAGE_ACTIONS)
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)


def _build_context_manage(typed_input: ContextManageInput) -> dict[str, object]:
    _validate_context_manage_input(typed_input)
    payload = build_context_manage_payload(typed_input)
    return validate_model_payload(ContextManageArgs, payload)
