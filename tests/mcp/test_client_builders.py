"""Unit tests for typed MCP CLI client builders."""

from __future__ import annotations

import argparse

from gdb_mcp.client.builders.context import build_context_query_payload
from gdb_mcp.client.builders.execution import build_execution_manage_payload
from gdb_mcp.client.builders.inferior import build_inferior_manage_payload
from gdb_mcp.client.builders.session import (
    build_session_query_payload,
    build_session_start_payload,
)
from gdb_mcp.client.input_parsers import (
    parse_session_query_input,
    parse_session_start_input,
)
from gdb_mcp.client.inputs import SessionQueryInput, SessionStartInput
from gdb_mcp.client.inputs import (
    ContextQueryInput,
    ExecutionManageInput,
    ExecutionWaitInput,
    InferiorManageInput,
)


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


def test_build_inferior_manage_create_payload() -> None:
    typed_input = InferiorManageInput(
        action="create",
        session_id=7,
        executable="/bin/true",
        make_current=True,
        inferior_id=None,
        mode=None,
        enabled=None,
    )

    assert build_inferior_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "create",
        "inferior": {
            "executable": "/bin/true",
            "make_current": True,
        },
    }


def test_build_inferior_manage_detach_on_fork_defaults_enabled() -> None:
    typed_input = InferiorManageInput(
        action="set_detach_on_fork",
        session_id=7,
        executable=None,
        make_current=None,
        inferior_id=None,
        mode=None,
        enabled=None,
    )

    assert build_inferior_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "set_detach_on_fork",
        "inferior": {"enabled": True},
    }


def test_build_execution_manage_run_payload() -> None:
    typed_input = ExecutionManageInput(
        action="run",
        session_id=7,
        args=("--mode", "fast"),
        wait=ExecutionWaitInput(until="stop", timeout_sec=30),
        timeout_sec=None,
        stop_reasons=(),
    )

    assert build_execution_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "run",
        "execution": {
            "args": ["--mode", "fast"],
            "wait": {"until": "stop", "timeout_sec": 30},
        },
    }


def test_build_execution_manage_wait_for_stop_preserves_explicit_zero_timeout() -> None:
    typed_input = ExecutionManageInput(
        action="wait_for_stop",
        session_id=7,
        args=(),
        wait=None,
        timeout_sec=0,
        stop_reasons=(),
    )

    assert build_execution_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "wait_for_stop",
        "execution": {"timeout_sec": 0},
    }


def test_build_context_query_backtrace_payload() -> None:
    typed_input = ContextQueryInput(
        action="backtrace",
        session_id=7,
        thread_id=3,
        frame=None,
        max_frames=20,
    )

    assert build_context_query_payload(typed_input) == {
        "session_id": 7,
        "action": "backtrace",
        "query": {"thread_id": 3, "max_frames": 20},
    }
