"""CLI parser and payload builders for session tools."""

from __future__ import annotations

import argparse

from gdb_mcp.contracts import (
    DEBUGGER_NAMES,
    SESSION_MANAGE_ACTIONS,
    SESSION_QUERY_ACTIONS,
)
from gdb_mcp.mcp.schemas import (
    SessionManageArgs,
    SessionQueryArgs,
    StartSessionArgs,
)

from ..builders.session import build_session_query_payload, build_session_start_payload
from ..inputs import (
    SessionQueryInput,
    SessionStartInput,
)
from ..namespace import one_of, optional_integer, snapshot
from ..parsers import (
    CliUsageError,
    key_value_entry,
    validate_model_payload,
)
from .common import (
    _add_action,
    _add_session_id,
)


def _configure_session_start(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--program")
    parser.add_argument(
        "--debugger",
        choices=list(DEBUGGER_NAMES),
        help="Debugger backend to drive: gdb or lldb",
    )
    parser.add_argument("--arg", dest="args", action="append", default=[])
    parser.add_argument("--init-command", dest="init_commands", action="append", default=[])
    parser.add_argument("--env", dest="env", action="append", type=key_value_entry, default=[])
    parser.add_argument("--debugger-path")
    parser.add_argument(
        "--debugger-arg",
        dest="debugger_args",
        action="append",
        default=[],
        help=(
            "Extra GDB command-line argument, repeatable; inserted before the program loads "
            "(e.g. --debugger-arg=--directory=/usr/lib/rustlib/etc "
            "--debugger-arg=-iex --debugger-arg='add-auto-load-safe-path /usr/lib/rustlib/etc')"
        ),
    )
    parser.add_argument("--working-dir")
    parser.add_argument("--core")
    parser.add_argument(
        "--rr-replay",
        dest="rr_replay",
        help=(
            "GDB only: rr trace directory to replay (starts 'rr replay', enables reverse "
            "execution). For LLDB, start 'rr replay -s PORT TRACE' and use 'connect'."
        ),
    )


def _build_session_start(typed_input: SessionStartInput) -> dict[str, object]:
    payload = build_session_start_payload(typed_input)
    return validate_model_payload(StartSessionArgs, payload)


def _configure_session_query(parser: argparse.ArgumentParser) -> None:
    _add_action(parser, choices=SESSION_QUERY_ACTIONS)
    _add_session_id(parser)


def _build_session_query(typed_input: SessionQueryInput) -> dict[str, object]:
    payload = build_session_query_payload(typed_input)
    if typed_input.action == "list" and typed_input.session_id is not None:
        raise CliUsageError("--session-id not valid with --action list")
    return validate_model_payload(SessionQueryArgs, payload)


def _configure_session_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=SESSION_MANAGE_ACTIONS)


def _build_session_manage(namespace: argparse.Namespace) -> dict[str, object]:
    values = snapshot(namespace)
    return validate_model_payload(
        SessionManageArgs,
        {
            "session_id": optional_integer(values, "session_id"),
            "action": one_of(values, "action", SESSION_MANAGE_ACTIONS),
            "session": {},
        },
    )
