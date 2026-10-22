"""CLI parser, validation, and payload builders for inferior tools."""

from __future__ import annotations

import argparse

from gdb_mcp.contracts import (
    INFERIOR_FOLLOW_FORK_MODES,
    INFERIOR_MANAGE_ACTIONS,
    INFERIOR_QUERY_ACTIONS,
)
from gdb_mcp.mcp.schemas import (
    InferiorManageArgs,
    InferiorQueryArgs,
)

from ..namespace import NamespaceLike, ParsedOptions, one_of, snapshot
from ..parsers import (
    CliUsageError,
    add_boolean_flag,
    validate_model,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


def _validate_inferior_manage_input(action: str, values: ParsedOptions) -> None:
    invalid_flags: list[str] = []
    if action == "create":
        if values.get("inferior_id") is not None:
            invalid_flags.append("--inferior-id")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        if values.get("enabled") is not None:
            invalid_flags.append("--enabled")
    elif action in {"remove", "select"}:
        if values.get("inferior_id") is None:
            raise CliUsageError(f"--inferior-id required with --action {action}")
        if values.get("executable") is not None:
            invalid_flags.append("--executable")
        if values.get("make_current") is not None:
            invalid_flags.append("--make-current")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        if values.get("enabled") is not None:
            invalid_flags.append("--enabled")
    elif action == "set_follow_fork_mode":
        if values.get("mode") is None:
            raise CliUsageError("--mode required with --action set_follow_fork_mode")
        if values.get("inferior_id") is not None:
            invalid_flags.append("--inferior-id")
        if values.get("executable") is not None:
            invalid_flags.append("--executable")
        if values.get("make_current") is not None:
            invalid_flags.append("--make-current")
        if values.get("enabled") is not None:
            invalid_flags.append("--enabled")
    elif action == "set_detach_on_fork":
        if values.get("inferior_id") is not None:
            invalid_flags.append("--inferior-id")
        if values.get("executable") is not None:
            invalid_flags.append("--executable")
        if values.get("make_current") is not None:
            invalid_flags.append("--make-current")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")

    _raise_invalid_action_flags(action, invalid_flags)


def _configure_inferior_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INFERIOR_QUERY_ACTIONS)


def _build_inferior_query(namespace: NamespaceLike) -> InferiorQueryArgs:
    values = snapshot(namespace)
    return validate_model(
        InferiorQueryArgs,
        {
            "session_id": values.get("session_id"),
            "action": one_of(values, "action", INFERIOR_QUERY_ACTIONS),
        },
    )


def _configure_inferior_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INFERIOR_MANAGE_ACTIONS)
    parser.add_argument("--inferior-id", type=int, default=None)
    parser.add_argument("--executable", default=None)
    add_boolean_flag(
        parser,
        "make_current",
        help_text="Select the new inferior after create",
        optional=True,
    )
    parser.add_argument("--mode", choices=INFERIOR_FOLLOW_FORK_MODES, default=None)
    add_boolean_flag(
        parser,
        "enabled",
        help_text="Detach from the non-followed fork",
        optional=True,
    )


def _build_inferior_manage(namespace: NamespaceLike) -> InferiorManageArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", INFERIOR_MANAGE_ACTIONS)
    _validate_inferior_manage_input(action, values)
    inferior = {
        name: value
        for name in ("executable", "make_current", "inferior_id", "mode", "enabled")
        if (value := values.get(name)) is not None
    }
    if action == "set_detach_on_fork":
        inferior.setdefault("enabled", True)
    return validate_model(
        InferiorManageArgs,
        {"session_id": values.get("session_id"), "action": action, "inferior": inferior},
    )
