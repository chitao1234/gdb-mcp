"""Real-GDB integration coverage for direct-v2 inspect and startup workflows."""

from __future__ import annotations

import subprocess

import pytest

from .program_sources import (
    ATTACHABLE_C_PROGRAM,
    CRASHING_C_PROGRAM,
    TEST_CPP_PROGRAM,
)


@pytest.fixture
def compiled_program(compile_program):
    """Compile the shared C++ program used by inspect-oriented integration tests."""

    return compile_program(
        TEST_CPP_PROGRAM,
        filename="inspect_program.cpp",
        compiler="g++",
    )


@pytest.fixture
def session_id(compiled_program, start_session):
    """Start one integration session and return its ID."""

    return start_session(compiled_program)


@pytest.fixture
def compiled_program_and_core(compile_program_with_core):
    """Compile a crashing program and return its executable/core-dump paths."""

    return compile_program_with_core(
        CRASHING_C_PROGRAM,
        filename="inspect_crash.c",
        compiler="gcc",
    )


@pytest.fixture
def attachable_program(compile_program):
    """Compile a long-running program that can be attached to."""

    return compile_program(
        ATTACHABLE_C_PROGRAM,
        filename="attachable.c",
        compiler="gcc",
    )


@pytest.mark.integration
def test_context_and_inspect_queries_v2(session_id, call_gdb_tool):
    """Representative context and inspect queries should use the v2 envelopes."""

    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )
    call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": {}},
    )

    threads = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "threads", "query": {}},
    )
    assert threads["status"] == "success"
    assert threads["action"] == "threads"
    assert threads["result"]["count"] >= 1

    backtrace = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "backtrace", "query": {"max_frames": 8}},
    )
    assert backtrace["status"] == "success"
    assert backtrace["action"] == "backtrace"
    assert backtrace["result"]["count"] > 0

    frame = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "frame", "query": {}},
    )
    assert frame["status"] == "success"
    assert frame["action"] == "frame"
    assert "func" in frame["result"]["frame"]

    variables = call_gdb_tool(
        "gdb_inspect_query",
        {
            "session_id": session_id,
            "action": "variables",
            "query": {"context": {"frame": 0}},
        },
    )
    assert variables["status"] == "success"
    assert variables["action"] == "variables"
    assert isinstance(variables["result"]["variables"], list)

    evaluated = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "evaluate", "query": {"expression": "a"}},
    )
    assert evaluated["status"] == "success"
    assert evaluated["action"] == "evaluate"
    assert evaluated["result"]["value"] == "5"


@pytest.mark.integration
def test_source_and_disassembly_queries_v2(session_id, call_gdb_tool):
    """Source and disassembly queries should use the location union directly."""

    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )
    call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": {}},
    )

    disassembly = call_gdb_tool(
        "gdb_inspect_query",
        {
            "session_id": session_id,
            "action": "disassembly",
            "query": {
                "location": {"kind": "function", "function": "add"},
                "instruction_count": 16,
                "mode": "mixed",
            },
        },
    )
    assert disassembly["status"] == "success"
    assert disassembly["action"] == "disassembly"
    assert disassembly["result"]["count"] > 0

    source = call_gdb_tool(
        "gdb_inspect_query",
        {
            "session_id": session_id,
            "action": "source",
            "query": {
                "location": {"kind": "function", "function": "add"},
                "context_before": 2,
                "context_after": 2,
            },
        },
    )
    assert source["status"] == "success"
    assert source["action"] == "source"
    assert source["result"]["count"] > 0
    assert any(line.get("is_current") for line in source["result"]["lines"])


@pytest.mark.integration
def test_memory_query_v2(session_id, call_gdb_tool):
    """Memory inspection should remain available through gdb_inspect_query."""

    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": {}},
    )

    memory = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "memory", "query": {"address": "&main", "count": 8}},
    )
    assert memory["status"] == "success"
    assert memory["action"] == "memory"
    assert memory["result"]["captured_bytes"] > 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload_builder", "expected_loaded"),
    [
        (lambda compiled, _: {"program": f"{compiled}.missing"}, False),
        (lambda compiled, _: {"program": compiled}, True),
        (
            lambda compiled, compiled_and_core: {
                "program": compiled_and_core[0],
                "core": compiled_and_core[1],
            },
            True,
        ),
    ],
)
def test_startup_target_loaded_variants_v2(
    compiled_program,
    compiled_program_and_core,
    payload_builder,
    expected_loaded,
    call_gdb_tool,
    stop_session,
):
    """Startup should report target loading consistently across key v2 flows."""

    payload = payload_builder(compiled_program, compiled_program_and_core)
    payload.setdefault("init_commands", [])

    result = call_gdb_tool("gdb_session_start", payload)
    assert result["status"] == "success"
    assert result["target_loaded"] is expected_loaded

    if "session_id" in result:
        stop_session(result["session_id"], ignore_errors=True)


@pytest.mark.integration
def test_execute_command_escape_hatch_v2(session_id, call_gdb_tool):
    """The escape hatch should remain a direct public tool, not a test shim."""

    result = call_gdb_tool(
        "gdb_execute_command",
        {"session_id": session_id, "command": "info functions", "timeout_sec": 30},
    )
    assert result["status"] == "success"
    assert result["command"] == "info functions"
    assert isinstance(result["output"], str)


@pytest.mark.integration
def test_get_status_reports_exited_state_after_continue_v2(session_id, call_gdb_tool):
    """Status should update through the v2 query envelope after continue completes."""

    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": {}},
    )
    call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "continue", "execution": {}},
    )

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert status["status"] == "success"
    assert status["result"]["execution_state"] in {"paused", "exited"}


@pytest.mark.integration
def test_attach_process_v2(
    attachable_program, start_session_result, stop_session, call_gdb_tool
):
    """Attach-by-PID should work without legacy wrappers or response flattening."""

    process = subprocess.Popen([attachable_program])
    session_id: int | None = None
    try:
        start = start_session_result(attachable_program)
        session_id = start["session_id"]

        attach = call_gdb_tool(
            "gdb_attach_process",
            {"session_id": session_id, "pid": process.pid, "timeout_sec": 30},
        )
        assert attach["status"] == "success"

        status = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id, "action": "status", "query": {}},
        )
        assert status["status"] == "success"
        assert status["result"]["execution_state"] == "paused"
    finally:
        if session_id is not None:
            stop_session(session_id, ignore_errors=True)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
