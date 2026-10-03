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

from ..builders.inferior import build_inferior_manage_payload, build_inferior_query_payload
from ..inputs import (
    InferiorManageInput,
    InferiorQueryInput,
)
from ..parsers import (
    add_boolean_flag,
    CliUsageError,
    validate_model_payload,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
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
        if typed_input.inferior_id is None:
            raise CliUsageError(f"--inferior-id required with --action {typed_input.action}")
        if typed_input.executable is not None:
            invalid_flags.append("--executable")
        if typed_input.make_current is not None:
            invalid_flags.append("--make-current")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.enabled is not None:
            invalid_flags.append("--enabled")
    elif typed_input.action == "set_follow_fork_mode":
        if typed_input.mode is None:
            raise CliUsageError("--mode required with --action set_follow_fork_mode")
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


def _configure_inferior_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INFERIOR_QUERY_ACTIONS)


def _build_inferior_query(typed_input: InferiorQueryInput) -> dict[str, object]:
    payload = build_inferior_query_payload(typed_input)
    return validate_model_payload(InferiorQueryArgs, payload)


def _configure_inferior_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INFERIOR_MANAGE_ACTIONS)
    parser.add_argument("--inferior-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--executable", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "make_current",
        help_text="Select the new inferior after create",
        suppress_default=True,
    )
    parser.add_argument("--mode", choices=INFERIOR_FOLLOW_FORK_MODES, default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "enabled",
        help_text="Detach from the non-followed fork",
        suppress_default=True,
    )


def _build_inferior_manage(typed_input: InferiorManageInput) -> dict[str, object]:
    _validate_inferior_manage_input(typed_input)
    payload = build_inferior_manage_payload(typed_input)
    return validate_model_payload(InferiorManageArgs, payload)
