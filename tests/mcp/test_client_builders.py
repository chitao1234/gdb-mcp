"""Unit tests for typed MCP CLI client builders."""

from __future__ import annotations

import argparse

from gdb_mcp.client.builders.session import (
    build_session_query_payload,
    build_session_start_payload,
)
from gdb_mcp.client.input_parsers import (
    parse_session_query_input,
    parse_session_start_input,
)
from gdb_mcp.client.inputs import SessionQueryInput, SessionStartInput


def test_parse_session_start_input_from_namespace() -> None:
    namespace = argparse.Namespace(
        program="/bin/true",
        args=["--mode", "fast"],
        init_commands=["set pagination off"],
        env=[("TERM", "dumb")],
        core=None,
        gdb_path=None,
        working_dir=None,
    )

    assert parse_session_start_input(namespace) == SessionStartInput(
        program="/bin/true",
        args=("--mode", "fast"),
        init_commands=("set pagination off",),
        env={"TERM": "dumb"},
        core=None,
        gdb_path=None,
        working_dir=None,
    )


def test_build_session_start_payload_from_typed_input() -> None:
    typed_input = SessionStartInput(
        program="/bin/true",
        args=("--mode", "fast"),
        init_commands=("set pagination off",),
        env={"TERM": "dumb"},
        core=None,
        gdb_path=None,
        working_dir=None,
    )

    assert build_session_start_payload(typed_input) == {
        "program": "/bin/true",
        "args": ["--mode", "fast"],
        "init_commands": ["set pagination off"],
        "env": {"TERM": "dumb"},
    }


def test_parse_session_query_input_from_namespace() -> None:
    namespace = argparse.Namespace(action="status", session_id=7)

    assert parse_session_query_input(namespace) == SessionQueryInput(
        action="status",
        session_id=7,
    )


def test_parse_session_query_input_without_session_id_attribute() -> None:
    namespace = argparse.Namespace(action="list")

    assert parse_session_query_input(namespace) == SessionQueryInput(
        action="list",
        session_id=None,
    )


def test_build_session_query_payload_from_typed_input() -> None:
    typed_input = SessionQueryInput(action="status", session_id=7)

    assert build_session_query_payload(typed_input) == {
        "action": "status",
        "session_id": 7,
    }
