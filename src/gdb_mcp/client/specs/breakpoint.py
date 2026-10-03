"""CLI parser, validation, and payload builders for breakpoint tools."""

from __future__ import annotations

import argparse


from gdb_mcp.contracts import (
    BREAKPOINT_ACCESS_VALUES,
    BREAKPOINT_EVENTS,
    BREAKPOINT_KINDS,
    BREAKPOINT_MANAGE_ACTIONS,
    BREAKPOINT_QUERY_ACTIONS,
)
from gdb_mcp.mcp.schemas import (
    BreakpointManageArgs,
    BreakpointQueryArgs,
)

from ..builders.breakpoint import build_breakpoint_manage_payload, build_breakpoint_query_payload
from ..inputs import (
    BreakpointCreateInput,
    BreakpointManageInput,
    BreakpointQueryInput,
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


def _validate_breakpoint_query_input(typed_input: BreakpointQueryInput) -> None:
    invalid_flags: list[str] = []
    if typed_input.action == "list":
        if typed_input.number is not None:
            invalid_flags.append("--number")
    elif typed_input.action == "get":
        if typed_input.number is None:
            raise CliUsageError("--number required with --action get")
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

    if typed_input.action == "update":
        if typed_input.number is None:
            raise CliUsageError("--number required with --action update")
        if typed_input.condition is None and typed_input.clear_condition is not True:
            raise CliUsageError("--condition or --clear-condition required with --action update")

    if typed_input.action in {"delete", "enable", "disable"}:
        if typed_input.number is None:
            raise CliUsageError(f"--number required with --action {typed_input.action}")


def _configure_breakpoint_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=BREAKPOINT_QUERY_ACTIONS)
    parser.add_argument("--number", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--kind",
        dest="kinds",
        action="append",
        choices=BREAKPOINT_KINDS,
        default=argparse.SUPPRESS,
    )
    add_boolean_flag(
        parser,
        "enabled",
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
    _add_action(parser, choices=BREAKPOINT_MANAGE_ACTIONS)
    parser.add_argument(
        "--breakpoint-kind",
        choices=BREAKPOINT_KINDS,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--location", default=argparse.SUPPRESS)
    parser.add_argument("--expression", default=argparse.SUPPRESS)
    parser.add_argument(
        "--access",
        choices=BREAKPOINT_ACCESS_VALUES,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--event", choices=BREAKPOINT_EVENTS, default=argparse.SUPPRESS)
    parser.add_argument("--argument", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "temporary",
        help_text="Create a temporary breakpoint or catchpoint",
        suppress_default=True,
    )
    parser.add_argument("--number", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--condition", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "clear_condition",
        help_text="Clear the existing breakpoint condition",
        suppress_default=True,
    )


def _build_breakpoint_manage(typed_input: BreakpointManageInput) -> dict[str, object]:
    _validate_breakpoint_manage_input(typed_input)
    payload = build_breakpoint_manage_payload(typed_input)
    return validate_model_payload(BreakpointManageArgs, payload)
