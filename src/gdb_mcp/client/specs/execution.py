"""CLI parser, validation, and payload builders for execution control."""

from __future__ import annotations

import argparse

from gdb_mcp.contracts import (
    ACTION_INTERRUPT,
    ACTION_JUMP,
    ACTION_RETURN,
    ACTION_RUN,
    ACTION_UNTIL,
    ACTION_WAIT_FOR_STOP,
    EXECUTION_MANAGE_ACTIONS,
    EXECUTION_WAIT_UNTIL_VALUES,
    ExecutionManageAction,
)
from gdb_mcp.mcp.schemas import (
    ExecutionManageArgs,
)

from ..namespace import NamespaceLike, ParsedOptions, one_of, snapshot
from ..parsers import (
    validate_model,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


def _validate_execution_manage_input(action: ExecutionManageAction, values: ParsedOptions) -> None:
    invalid_flags: list[str] = []

    if action not in {ACTION_JUMP, ACTION_UNTIL} and values.get("location") is not None:
        invalid_flags.append("--location")
    if action != ACTION_RETURN and values.get("value") is not None:
        invalid_flags.append("--value")
    if action != ACTION_RUN and values.get("args"):
        invalid_flags.append("--arg")
    if action != ACTION_WAIT_FOR_STOP and values.get("stop_reasons"):
        invalid_flags.append("--stop-reason")
    if (
        action in {ACTION_INTERRUPT, ACTION_RETURN, ACTION_WAIT_FOR_STOP}
        and values.get("wait_until") is not None
    ):
        invalid_flags.append("--wait-until")
    if action in {ACTION_INTERRUPT, ACTION_RETURN} and values.get("timeout_sec") is not None:
        invalid_flags.append("--timeout-sec")
    if action in {
        ACTION_RUN,
        ACTION_INTERRUPT,
        ACTION_RETURN,
        ACTION_WAIT_FOR_STOP,
        ACTION_JUMP,
        ACTION_UNTIL,
    } and values.get("reverse"):
        invalid_flags.append("--reverse")

    _raise_invalid_action_flags(action, invalid_flags)


def _configure_execution_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=EXECUTION_MANAGE_ACTIONS)
    parser.add_argument("--arg", dest="args", action="append", default=None)
    parser.add_argument(
        "--wait-until",
        choices=EXECUTION_WAIT_UNTIL_VALUES,
        default=None,
    )
    parser.add_argument("--timeout-sec", type=int, default=None)
    parser.add_argument("--stop-reason", dest="stop_reasons", action="append", default=None)
    parser.add_argument("--value", help="Return value expression for action=return")
    parser.add_argument(
        "--location", help="Destination: function, file:line, line number, or *address"
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Run the command backwards (rr replay or gdb record mode)",
    )


def _build_execution_manage(namespace: NamespaceLike) -> ExecutionManageArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", EXECUTION_MANAGE_ACTIONS)
    _validate_execution_manage_input(action, values)

    execution: dict[str, object] = {}
    for name in ("location", "value", "wait_until", "timeout_sec"):
        value = values.get(name)
        if value is not None:
            execution[name] = value
    for name in ("args", "stop_reasons", "reverse"):
        value = values.get(name)
        if value:
            execution[name] = value
    if action == ACTION_WAIT_FOR_STOP:
        execution.setdefault("timeout_sec", 30)

    return validate_model(
        ExecutionManageArgs,
        {"session_id": values.get("session_id"), "action": action, "execution": execution},
    )
