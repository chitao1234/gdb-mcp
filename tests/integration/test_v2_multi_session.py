"""Real-GDB integration coverage for representative direct-v2 multi-session workflows."""

from __future__ import annotations

import pytest

from .program_sources import TEST_PROGRAM_1, TEST_PROGRAM_2


@pytest.fixture
def compiled_program_1(compile_program):
    """Compile the first simple C program used for session-isolation tests."""

    return compile_program(
        TEST_PROGRAM_1,
        filename="program1.c",
        compiler="gcc",
    )


@pytest.fixture
def compiled_program_2(compile_program):
    """Compile the second simple C program used for session-isolation tests."""

    return compile_program(
        TEST_PROGRAM_2,
        filename="program2.c",
        compiler="gcc",
    )


@pytest.mark.integration
def test_session_inventory_and_status_are_isolated_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    """Global session inventory should list multiple live sessions distinctly."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        sessions = call_gdb_tool("gdb_session_query", {"action": "list", "query": {}})
        assert sessions["status"] == "success"
        assert sessions["action"] == "list"
        listed_ids = {item["session_id"] for item in sessions["result"]["sessions"]}
        assert listed_ids == {session_id_1, session_id_2}

        status_1 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_1, "action": "status", "query": {}},
        )
        status_2 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_2, "action": "status", "query": {}},
        )
        assert status_1["result"]["is_running"] is True
        assert status_2["result"]["is_running"] is True
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_breakpoints_are_isolated_between_sessions_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    """Breakpoints created in one session should not leak into another."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id_1,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main"},
            },
        )

        listed_1 = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id_1, "action": "list", "query": {}},
        )
        listed_2 = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id_2, "action": "list", "query": {}},
        )
        assert listed_1["result"]["count"] == 1
        assert listed_2["result"]["count"] == 0
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_execution_state_and_backtrace_are_isolated_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    """Execution and backtrace queries should be routed to the selected session only."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        for session_id in (session_id_1, session_id_2):
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

        backtrace_1 = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id_1, "action": "backtrace", "query": {}},
        )
        backtrace_2 = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id_2, "action": "backtrace", "query": {}},
        )
        assert backtrace_1["result"]["count"] > 0
        assert backtrace_2["result"]["count"] > 0
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_stop_one_session_does_not_affect_other_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    """Stopping one session should not invalidate another live session."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        stop = call_gdb_tool(
            "gdb_session_manage",
            {"session_id": session_id_2, "action": "stop", "session": {}},
        )
        assert stop["status"] == "success"

        status_1 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_1, "action": "status", "query": {}},
        )
        assert status_1["status"] == "success"

        status_2 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_2, "action": "status", "query": {}},
        )
        assert status_2["status"] == "error"
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_invalid_session_id_returns_error_v2(call_gdb_tool):
    """Invalid session IDs should return the public v2 error envelope."""

    result = call_gdb_tool(
        "gdb_session_query",
        {"session_id": 999, "action": "status", "query": {}},
    )
    assert result["status"] == "error"
    assert "Invalid session_id: 999" in result["message"]
    assert "gdb_session_start" in result["message"]
