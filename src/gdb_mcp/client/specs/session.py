"""CLI parser and payload builders for session tools."""

from __future__ import annotations

import argparse


from gdb_mcp.contracts import (
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
    parser.add_argument("--arg", dest="args", action="append", default=[])
    parser.add_argument("--init-command", dest="init_commands", action="append", default=[])
    parser.add_argument("--env", dest="env", action="append", type=key_value_entry, default=[])
    parser.add_argument("--gdb-path")
    parser.add_argument("--working-dir")
    parser.add_argument("--core")


def _build_session_start(typed_input: SessionStartInput) -> dict[str, object]:
    payload = build_session_start_payload(typed_input)
    return validate_model_payload(StartSessionArgs, payload)


def _configure_session_query(parser: argparse.ArgumentParser) -> None:
    _add_action(parser, choices=SESSION_QUERY_ACTIONS)
    _add_session_id(parser, required=False)


def _build_session_query(typed_input: SessionQueryInput) -> dict[str, object]:
    payload = build_session_query_payload(typed_input)
    if typed_input.action == "list" and typed_input.session_id is not None:
        raise CliUsageError("--session-id not valid with --action list")
    return validate_model_payload(SessionQueryArgs, payload)


def _configure_session_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=SESSION_MANAGE_ACTIONS)


def _build_session_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        SessionManageArgs,
        {"session_id": namespace.session_id, "action": namespace.action, "session": {}},
    )
