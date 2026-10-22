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

from ..namespace import NamespaceLike, key_value_entries, one_of, optional_integer, snapshot
from ..parsers import (
    CliUsageError,
    collapse_key_value_entries,
    key_value_entry,
    validate_model,
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
        help="Debugger backend to drive: gdb, lldb, or delve",
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
            "Extra debugger startup argument, repeatable; inserted before the program loads "
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


def _build_session_start(namespace: NamespaceLike) -> StartSessionArgs:
    values = snapshot(namespace)
    payload: dict[str, object] = {
        name: item
        for name in ("program", "debugger", "core", "debugger_path", "working_dir", "rr_replay")
        if (item := values.get(name)) is not None
    }
    for name in ("args", "init_commands", "debugger_args"):
        value = values.get(name)
        if value:
            payload[name] = value
    env = collapse_key_value_entries(key_value_entries(values, "env"))
    if env:
        payload["env"] = env
    return validate_model(StartSessionArgs, payload)


def _configure_session_query(parser: argparse.ArgumentParser) -> None:
    _add_action(parser, choices=SESSION_QUERY_ACTIONS)
    _add_session_id(parser)


def _build_session_query(namespace: NamespaceLike) -> SessionQueryArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", SESSION_QUERY_ACTIONS)
    session_id = values.get("session_id")
    if action == "list" and session_id is not None:
        raise CliUsageError("--session-id not valid with --action list")
    payload: dict[str, object] = {"action": action}
    if session_id is not None:
        payload["session_id"] = session_id
    return validate_model(SessionQueryArgs, payload)


def _configure_session_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=SESSION_MANAGE_ACTIONS)


def _build_session_manage(namespace: NamespaceLike) -> SessionManageArgs:
    values = snapshot(namespace)
    return validate_model(
        SessionManageArgs,
        {
            "session_id": optional_integer(values, "session_id"),
            "action": one_of(values, "action", SESSION_MANAGE_ACTIONS),
            "session": {},
        },
    )
