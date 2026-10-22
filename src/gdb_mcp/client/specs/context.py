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

from ..namespace import NamespaceLike, ParsedOptions, one_of, snapshot
from ..parsers import (
    CliUsageError,
    validate_model,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


def _validate_context_query_input(action: str, values: ParsedOptions) -> None:
    invalid_flags: list[str] = []
    if action == "threads":
        if values.get("thread_id") is not None:
            invalid_flags.append("--thread-id")
        if values.get("frame") is not None:
            invalid_flags.append("--frame")
        if values.get("max_frames") is not None:
            invalid_flags.append("--max-frames")
    elif action == "backtrace":
        if values.get("frame") is not None:
            invalid_flags.append("--frame")
    elif action == "frame":
        if values.get("max_frames") is not None:
            invalid_flags.append("--max-frames")

    _raise_invalid_action_flags(action, invalid_flags)


def _validate_context_manage_input(action: str, values: ParsedOptions) -> None:
    invalid_flags: list[str] = []
    if action == "select_thread":
        if values.get("thread_id") is None:
            raise CliUsageError("--thread-id required with --action select_thread")
        if values.get("frame") is not None:
            invalid_flags.append("--frame")
    elif action == "select_frame":
        if values.get("frame") is None:
            raise CliUsageError("--frame required with --action select_frame")
        if values.get("thread_id") is not None:
            invalid_flags.append("--thread-id")

    _raise_invalid_action_flags(action, invalid_flags)


def _configure_context_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=CONTEXT_QUERY_ACTIONS)
    parser.add_argument("--thread-id", type=int, default=None)
    parser.add_argument("--frame", type=int, default=None)
    parser.add_argument("--max-frames", type=int, default=None)


def _build_context_query(namespace: NamespaceLike) -> ContextQueryArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", CONTEXT_QUERY_ACTIONS)
    _validate_context_query_input(action, values)
    query = {
        name: value
        for name in ("thread_id", "frame", "max_frames")
        if (value := values.get(name)) is not None
    }
    payload: dict[str, object] = {"session_id": values.get("session_id"), "action": action}
    if query:
        payload["query"] = query
    return validate_model(ContextQueryArgs, payload)


def _configure_context_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=CONTEXT_MANAGE_ACTIONS)
    parser.add_argument("--thread-id", type=int, default=None)
    parser.add_argument("--frame", type=int, default=None)


def _build_context_manage(namespace: NamespaceLike) -> ContextManageArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", CONTEXT_MANAGE_ACTIONS)
    _validate_context_manage_input(action, values)
    context = {
        name: value for name in ("thread_id", "frame") if (value := values.get(name)) is not None
    }
    return validate_model(
        ContextManageArgs,
        {"session_id": values.get("session_id"), "action": action, "context": context},
    )
