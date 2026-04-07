"""Namespace-to-typed-input parsers for the MCP CLI client."""

from __future__ import annotations

import argparse
from typing import cast

from .inputs import SessionQueryAction, SessionQueryInput, SessionStartInput
from .parsers import collapse_key_value_entries


def parse_session_start_input(namespace: argparse.Namespace) -> SessionStartInput:
    """Parse one session-start namespace into a typed input object."""

    return SessionStartInput(
        program=namespace.program,
        args=tuple(namespace.args),
        init_commands=tuple(namespace.init_commands),
        env=collapse_key_value_entries(namespace.env),
        core=namespace.core,
        gdb_path=namespace.gdb_path,
        working_dir=namespace.working_dir,
    )


def parse_session_query_input(namespace: argparse.Namespace) -> SessionQueryInput:
    """Parse one session-query namespace into a typed input object."""

    return SessionQueryInput(
        action=cast(SessionQueryAction, namespace.action),
        session_id=namespace.__dict__.get("session_id"),
    )
