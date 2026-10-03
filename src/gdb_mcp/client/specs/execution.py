"""CLI parser, validation, and payload builders for execution control."""

from __future__ import annotations

import argparse


from gdb_mcp.contracts import (
    EXECUTION_MANAGE_ACTIONS,
    EXECUTION_WAIT_UNTIL_VALUES,
)
from gdb_mcp.mcp.schemas import (
    ExecutionManageArgs,
)

from ..builders.execution import build_execution_manage_payload
from ..inputs import (
    ExecutionManageInput,
)
from ..parsers import (
    validate_model_payload,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


def _validate_execution_manage_input(typed_input: ExecutionManageInput) -> None:
    invalid_flags: list[str] = []

    if typed_input.action == "interrupt":
        if typed_input.args:
            invalid_flags.append("--arg")
        if typed_input.wait_until is not None:
            invalid_flags.append("--wait-until")
        if typed_input.timeout_sec is not None:
            invalid_flags.append("--timeout-sec")
        if typed_input.stop_reasons:
            invalid_flags.append("--stop-reason")
    elif typed_input.action in {"continue", "step", "next", "finish"}:
        if typed_input.args:
            invalid_flags.append("--arg")
        if typed_input.stop_reasons:
            invalid_flags.append("--stop-reason")
    elif typed_input.action == "run":
        if typed_input.stop_reasons:
            invalid_flags.append("--stop-reason")
    elif typed_input.action == "wait_for_stop":
        if typed_input.args:
            invalid_flags.append("--arg")
        if typed_input.wait_until is not None:
            invalid_flags.append("--wait-until")

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _configure_execution_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=EXECUTION_MANAGE_ACTIONS)
    parser.add_argument("--arg", dest="args", action="append", default=argparse.SUPPRESS)
    parser.add_argument(
        "--wait-until",
        choices=EXECUTION_WAIT_UNTIL_VALUES,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--timeout-sec", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--stop-reason", dest="stop_reasons", action="append", default=argparse.SUPPRESS
    )


def _build_execution_manage(typed_input: ExecutionManageInput) -> dict[str, object]:
    _validate_execution_manage_input(typed_input)
    payload = build_execution_manage_payload(typed_input)
    return validate_model_payload(ExecutionManageArgs, payload)
