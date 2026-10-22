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


def _validate_breakpoint_query_input(action: str, values: ParsedOptions) -> None:
    invalid_flags: list[str] = []
    if action == "list":
        if values.get("number") is not None:
            invalid_flags.append("--number")
    elif action == "get":
        if values.get("number") is None:
            raise CliUsageError("--number required with --action get")
        if values.get("kinds"):
            invalid_flags.append("--kind")
        if values.get("enabled") is not None:
            invalid_flags.append("--enabled")

    _raise_invalid_action_flags(action, invalid_flags)


def _validate_breakpoint_create_input(kind: str, values: ParsedOptions) -> None:
    if kind == "code":
        if values.get("location") is None:
            raise CliUsageError("--location required with --breakpoint-kind code")
        invalid_flags: list[str] = []
        if values.get("expression") is not None:
            invalid_flags.append("--expression")
        if values.get("access") is not None:
            invalid_flags.append("--access")
        if values.get("event") is not None:
            invalid_flags.append("--event")
        if values.get("argument") is not None:
            invalid_flags.append("--argument")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --breakpoint-kind code"
            )
        return

    if kind == "watch":
        if values.get("expression") is None:
            raise CliUsageError("--expression required with --breakpoint-kind watch")
        invalid_flags = []
        if values.get("location") is not None:
            invalid_flags.append("--location")
        if values.get("condition") is not None:
            invalid_flags.append("--condition")
        if values.get("temporary") is not None:
            invalid_flags.append("--temporary")
        if values.get("event") is not None:
            invalid_flags.append("--event")
        if values.get("argument") is not None:
            invalid_flags.append("--argument")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --breakpoint-kind watch"
            )
        return

    if values.get("event") is None:
        raise CliUsageError("--event required with --breakpoint-kind catch")

    invalid_flags = []
    if values.get("location") is not None:
        invalid_flags.append("--location")
    if values.get("condition") is not None:
        invalid_flags.append("--condition")
    if values.get("expression") is not None:
        invalid_flags.append("--expression")
    if values.get("access") is not None:
        invalid_flags.append("--access")
    if invalid_flags:
        raise CliUsageError(
            f"{', '.join(sorted(invalid_flags))} not valid with --breakpoint-kind catch"
        )


def _validate_breakpoint_manage_input(action: str, values: ParsedOptions) -> None:
    invalid_flags: list[str] = []
    if action == "create":
        if values.get("number") is not None:
            invalid_flags.append("--number")
        if values.get("clear_condition") is not None:
            invalid_flags.append("--clear-condition")
        _raise_invalid_action_flags(action, invalid_flags)
        if values.get("breakpoint_kind") is None:
            raise CliUsageError("--breakpoint-kind required with --action create")
        _validate_breakpoint_create_input(
            one_of(values, "breakpoint_kind", BREAKPOINT_KINDS), values
        )
        return

    if values.get("breakpoint_kind") is not None:
        invalid_flags.append("--breakpoint-kind")
    if values.get("location") is not None:
        invalid_flags.append("--location")
    if values.get("expression") is not None:
        invalid_flags.append("--expression")
    if values.get("access") is not None:
        invalid_flags.append("--access")
    if values.get("event") is not None:
        invalid_flags.append("--event")
    if values.get("argument") is not None:
        invalid_flags.append("--argument")
    if values.get("temporary") is not None:
        invalid_flags.append("--temporary")

    if action in {"delete", "enable", "disable"}:
        if values.get("condition") is not None:
            invalid_flags.append("--condition")
        if values.get("clear_condition") is not None:
            invalid_flags.append("--clear-condition")

    _raise_invalid_action_flags(action, invalid_flags)

    if action == "update":
        if values.get("number") is None:
            raise CliUsageError("--number required with --action update")
        if values.get("condition") is None and values.get("clear_condition") is not True:
            raise CliUsageError("--condition or --clear-condition required with --action update")

    if action in {"delete", "enable", "disable"} and values.get("number") is None:
        raise CliUsageError(f"--number required with --action {action}")


def _configure_breakpoint_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=BREAKPOINT_QUERY_ACTIONS)
    parser.add_argument("--number", type=int, default=None)
    parser.add_argument(
        "--kind",
        dest="kinds",
        action="append",
        choices=BREAKPOINT_KINDS,
        default=None,
    )
    add_boolean_flag(
        parser,
        "enabled",
        help_text="Filter breakpoints by enabled state",
        optional=True,
    )


def _build_breakpoint_query(namespace: NamespaceLike) -> BreakpointQueryArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", BREAKPOINT_QUERY_ACTIONS)
    _validate_breakpoint_query_input(action, values)
    query: dict[str, object] = {}
    for name in ("number", "enabled"):
        value = values.get(name)
        if value is not None:
            query[name] = value
    if values.get("kinds"):
        query["kinds"] = values.get("kinds")
    payload: dict[str, object] = {"session_id": values.get("session_id"), "action": action}
    if query:
        payload["query"] = query
    return validate_model(BreakpointQueryArgs, payload)


def _configure_breakpoint_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=BREAKPOINT_MANAGE_ACTIONS)
    parser.add_argument(
        "--breakpoint-kind",
        choices=BREAKPOINT_KINDS,
        default=None,
    )
    parser.add_argument("--location", default=None)
    parser.add_argument("--expression", default=None)
    parser.add_argument(
        "--access",
        choices=BREAKPOINT_ACCESS_VALUES,
        default=None,
    )
    parser.add_argument("--event", choices=BREAKPOINT_EVENTS, default=None)
    parser.add_argument("--argument", default=None)
    add_boolean_flag(
        parser,
        "temporary",
        help_text="Create a temporary breakpoint or catchpoint",
        optional=True,
    )
    parser.add_argument("--number", type=int, default=None)
    parser.add_argument("--condition", default=None)
    add_boolean_flag(
        parser,
        "clear_condition",
        help_text="Clear the existing breakpoint condition",
        optional=True,
    )


def _build_breakpoint_manage(namespace: NamespaceLike) -> BreakpointManageArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", BREAKPOINT_MANAGE_ACTIONS)
    _validate_breakpoint_manage_input(action, values)
    payload: dict[str, object] = {"session_id": values.get("session_id"), "action": action}
    if action == "create":
        breakpoint: dict[str, object] = {
            name: item
            for name in ("location", "expression", "access", "event", "argument", "condition")
            if (item := values.get(name)) is not None
        }
        breakpoint["kind"] = values.get("breakpoint_kind")
        if values.get("temporary"):
            breakpoint["temporary"] = True
        payload["breakpoint"] = breakpoint
    else:
        payload["breakpoint"] = {"number": values.get("number")}
        if action == "update":
            payload["changes"] = {
                name: change
                for name in ("condition", "clear_condition")
                if (change := values.get(name)) is not None
            }
    return validate_model(BreakpointManageArgs, payload)
