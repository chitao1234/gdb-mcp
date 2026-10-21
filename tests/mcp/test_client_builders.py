"""Unit tests for typed MCP CLI client builders."""

from __future__ import annotations

import argparse

from gdb_mcp.client.builders.breakpoint import (
    build_breakpoint_manage_payload,
    build_breakpoint_query_payload,
)
from gdb_mcp.client.builders.context import build_context_query_payload
from gdb_mcp.client.builders.execution import build_execution_manage_payload
from gdb_mcp.client.builders.inferior import build_inferior_manage_payload
from gdb_mcp.client.builders.inspect import build_inspect_query_payload
from gdb_mcp.client.builders.session import (
    build_session_query_payload,
    build_session_start_payload,
)
from gdb_mcp.client.builders.workflow import (
    build_run_until_failure_payload,
    build_workflow_batch_payload,
)
from gdb_mcp.client.input_parsers import (
    parse_session_query_input,
    parse_session_start_input,
)
from gdb_mcp.client.inputs import (
    BreakpointCreateInput,
    BreakpointManageInput,
    BreakpointQueryInput,
    ContextQueryInput,
    ExecutionManageInput,
    InferiorManageInput,
    InspectQueryInput,
    LocationInput,
    RunUntilFailureInput,
    SessionQueryInput,
    SessionStartInput,
    SessionStepInput,
    WorkflowBatchInput,
)


def test_parse_session_start_input_from_namespace() -> None:
    """Namespace fields are converted into the typed session-start input."""

    namespace = argparse.Namespace(
        program="/bin/true",
        debugger="gdb",
        args=["--mode", "fast"],
        init_commands=["set pagination off"],
        env=[("TERM", "dumb")],
        core=None,
        debugger_path=None,
        debugger_args=["-iex", "set pagination off"],
        working_dir=None,
        rr_replay=None,
    )

    assert parse_session_start_input(namespace) == SessionStartInput(
        program="/bin/true",
        debugger="gdb",
        args=("--mode", "fast"),
        init_commands=("set pagination off",),
        env={"TERM": "dumb"},
        core=None,
        debugger_path=None,
        debugger_args=("-iex", "set pagination off"),
        working_dir=None,
        rr_replay=None,
    )


def test_build_session_start_payload_from_typed_input() -> None:
    """The typed input drops empty optional fields from the wire payload."""

    typed_input = SessionStartInput(
        program="/bin/true",
        debugger=None,
        args=("--mode", "fast"),
        init_commands=("set pagination off",),
        env={"TERM": "dumb"},
        core=None,
        debugger_path=None,
        debugger_args=(),
        working_dir=None,
        rr_replay=None,
    )

    assert build_session_start_payload(typed_input) == {
        "program": "/bin/true",
        "args": ["--mode", "fast"],
        "init_commands": ["set pagination off"],
        "env": {"TERM": "dumb"},
    }


def test_parse_session_query_input_from_namespace() -> None:
    """The namespace action and session id map onto the typed query input."""

    namespace = argparse.Namespace(action="status", session_id=7)

    assert parse_session_query_input(namespace) == SessionQueryInput(
        action="status",
        session_id=7,
    )


def test_parse_session_query_input_without_session_id_argument() -> None:
    """A missing session id stays ``None`` for list queries."""

    namespace = argparse.Namespace(action="list", session_id=None)

    assert parse_session_query_input(namespace) == SessionQueryInput(
        action="list",
        session_id=None,
    )


def test_build_session_query_payload_from_typed_input() -> None:
    """The session query payload mirrors the typed fields."""

    typed_input = SessionQueryInput(action="status", session_id=7)

    assert build_session_query_payload(typed_input) == {
        "action": "status",
        "session_id": 7,
    }


def test_build_inferior_manage_create_payload() -> None:
    """A create action nests the inferior fields expected by the tool."""

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
    """Detach-on-fork defaults to enabled when no value is supplied."""

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


def test_build_execution_manage_payload_includes_reverse() -> None:
    """An explicit reverse flag is carried into the execution block."""

    typed_input = ExecutionManageInput(
        action="step",
        session_id=7,
        args=(),
        wait_until="stop",
        timeout_sec=None,
        stop_reasons=(),
        reverse=True,
    )

    assert build_execution_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "step",
        "execution": {"wait_until": "stop", "reverse": True},
    }


def test_build_session_start_payload_includes_debugger_args() -> None:
    """Extra GDB arguments are forwarded under ``debugger_args``."""

    typed_input = SessionStartInput(
        program="/bin/true",
        debugger=None,
        args=(),
        init_commands=(),
        env=None,
        core=None,
        debugger_path=None,
        debugger_args=("-iex", "set pagination off"),
        working_dir=None,
        rr_replay=None,
    )

    assert build_session_start_payload(typed_input) == {
        "program": "/bin/true",
        "debugger_args": ["-iex", "set pagination off"],
    }


def test_build_session_start_payload_includes_debugger() -> None:
    """The selected backend is forwarded under ``debugger``."""

    typed_input = SessionStartInput(
        program="/bin/true",
        debugger="gdb",
        args=(),
        init_commands=(),
        env=None,
        core=None,
        debugger_path=None,
        debugger_args=(),
        working_dir=None,
        rr_replay=None,
    )

    assert build_session_start_payload(typed_input) == {
        "program": "/bin/true",
        "debugger": "gdb",
    }


def test_build_session_start_payload_includes_rr_replay() -> None:
    """A replay trace becomes the only field in the payload."""

    typed_input = SessionStartInput(
        program=None,
        debugger=None,
        args=(),
        init_commands=(),
        env=None,
        core=None,
        debugger_path=None,
        debugger_args=(),
        working_dir=None,
        rr_replay="/traces/app-0",
    )

    assert build_session_start_payload(typed_input) == {"rr_replay": "/traces/app-0"}


def test_build_execution_manage_run_payload() -> None:
    """A run action groups its arguments and stop condition."""

    typed_input = ExecutionManageInput(
        action="run",
        session_id=7,
        args=("--mode", "fast"),
        wait_until="stop",
        timeout_sec=30,
        stop_reasons=(),
        reverse=False,
    )

    assert build_execution_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "run",
        "execution": {
            "args": ["--mode", "fast"],
            "wait_until": "stop",
            "timeout_sec": 30,
        },
    }


def test_build_execution_manage_wait_for_stop_preserves_explicit_zero_timeout() -> None:
    """A zero timeout is meaningful and must not be dropped."""

    typed_input = ExecutionManageInput(
        action="wait_for_stop",
        session_id=7,
        args=(),
        wait_until=None,
        timeout_sec=0,
        stop_reasons=(),
        reverse=False,
    )

    assert build_execution_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "wait_for_stop",
        "execution": {"timeout_sec": 0},
    }


def test_build_context_query_backtrace_payload() -> None:
    """Backtrace filters are nested under the query block."""

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


def test_build_breakpoint_query_filtered_list_payload() -> None:
    """A false ``enabled`` filter is preserved rather than treated as absent."""

    typed_input = BreakpointQueryInput(
        action="list",
        session_id=7,
        number=None,
        kinds=("code", "watch"),
        enabled=False,
    )

    assert build_breakpoint_query_payload(typed_input) == {
        "session_id": 7,
        "action": "list",
        "query": {"kinds": ["code", "watch"], "enabled": False},
    }


def test_build_breakpoint_manage_create_payload() -> None:
    """A create action nests the breakpoint definition."""

    typed_input = BreakpointManageInput(
        action="create",
        session_id=7,
        breakpoint=BreakpointCreateInput(
            kind="code",
            location="main",
            expression=None,
            access=None,
            event=None,
            argument=None,
            condition=None,
            temporary=True,
        ),
        number=None,
        condition=None,
        clear_condition=None,
    )

    assert build_breakpoint_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "create",
        "breakpoint": {"kind": "code", "location": "main", "temporary": True},
    }


def test_build_inspect_source_payload() -> None:
    """Source inspection preserves the location and context window."""

    typed_input = InspectQueryInput(
        action="source",
        session_id=7,
        thread_id=None,
        frame=None,
        expression=None,
        register_numbers=(),
        register_names=(),
        include_vector_registers=None,
        max_registers=None,
        value_format=None,
        memory_address=None,
        count=None,
        offset=None,
        location=LocationInput(kind="file_line", file="src/main.c", line=42),
        instruction_count=None,
        mode=None,
        context_before=2,
        context_after=3,
    )

    assert build_inspect_query_payload(typed_input) == {
        "session_id": 7,
        "action": "source",
        "query": {
            "location": {"kind": "file_line", "file": "src/main.c", "line": 42},
            "context_before": 2,
            "context_after": 3,
        },
    }


def test_build_workflow_batch_payload_from_typed_steps() -> None:
    """Typed steps are serialized into the batch wire format."""

    typed_input = WorkflowBatchInput(
        session_id=7,
        steps=(
            SessionStepInput(
                tool="gdb_context_query",
                label="stack",
                arguments={"action": "backtrace", "query": {"max_frames": 20}},
            ),
        ),
        fail_fast=False,
        capture_stop_events=True,
    )

    assert build_workflow_batch_payload(typed_input) == {
        "session_id": 7,
        "steps": [
            {
                "tool": "gdb_context_query",
                "label": "stack",
                "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
            }
        ],
        "fail_fast": False,
        "capture_stop_events": True,
    }


def _run_until_failure_input() -> RunUntilFailureInput:
    """Build a fully populated campaign input for the payload test."""

    return RunUntilFailureInput(
        startup_program="/bin/true",
        startup_args=(),
        startup_init_commands=("set pagination off",),
        startup_env=None,
        startup_debugger_path=None,
        startup_working_dir=None,
        startup_core=None,
        setup_steps=(),
        run_args=(),
        run_timeout_sec=15,
        max_iterations=2,
        failure_on_error=True,
        failure_on_timeout=True,
        failure_stop_reasons=("signal-received",),
        failure_execution_states=(),
        failure_exit_codes=(),
        failure_result_text_regex=None,
        capture_enabled=True,
        capture_output_dir=None,
        capture_bundle_name_prefix=None,
        capture_bundle_name=None,
        capture_expressions=("errno",),
        capture_memory_ranges=("&errno:8",),
        capture_max_frames=100,
        capture_include_threads=True,
        capture_include_backtraces=True,
        capture_include_frame=True,
        capture_include_variables=True,
        capture_include_registers=True,
        capture_include_transcript=True,
        capture_include_stop_history=True,
    )


def test_build_run_until_failure_payload_from_typed_input() -> None:
    """Every campaign section is emitted in the expected nested shape."""

    payload = build_run_until_failure_payload(_run_until_failure_input())

    assert set(payload) == {
        "startup",
        "setup_steps",
        "run_timeout_sec",
        "max_iterations",
        "failure",
        "capture",
    }

    # Startup process and campaign loop.
    assert payload["startup"] == {
        "program": "/bin/true",
        "init_commands": ["set pagination off"],
    }
    assert payload["setup_steps"] == []
    assert payload["run_timeout_sec"] == 15
    assert payload["max_iterations"] == 2

    # Failure matching criteria.
    assert payload["failure"] == {
        "failure_on_error": True,
        "failure_on_timeout": True,
        "stop_reasons": ["signal-received"],
        "execution_states": [],
        "exit_codes": [],
    }

    # Capture bundle contents.
    assert payload["capture"] == {
        "enabled": True,
        "expressions": ["errno"],
        "memory_ranges": ["&errno:8"],
        "max_frames": 100,
        "include_threads": True,
        "include_backtraces": True,
        "include_frame": True,
        "include_variables": True,
        "include_registers": True,
        "include_transcript": True,
        "include_stop_history": True,
    }
