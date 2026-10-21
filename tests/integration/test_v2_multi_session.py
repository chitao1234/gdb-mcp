"""Real-GDB integration coverage for representative direct-v2 multi-session workflows."""

from __future__ import annotations

import pytest

from tests.support import dig, integer, mapping_list, text

from .conftest import (
    CallGdbTool,
    CompileProgram,
    StartSession,
    StopSession,
    create_breakpoint,
    run,
    status_query,
)
from .program_sources import TEST_PROGRAM_1, TEST_PROGRAM_2


@pytest.fixture
def compiled_program_1(compile_program: CompileProgram) -> str:
    """Compile the first simple C program used for session-isolation tests."""

    return compile_program(
        TEST_PROGRAM_1,
        filename="program1.c",
        compiler="gcc",
    )


@pytest.fixture
def compiled_program_2(compile_program: CompileProgram) -> str:
    """Compile the second simple C program used for session-isolation tests."""

    return compile_program(
        TEST_PROGRAM_2,
        filename="program2.c",
        compiler="gcc",
    )


@pytest.mark.integration
def test_session_inventory_and_status_are_isolated_v2(
    compiled_program_1: str,
    compiled_program_2: str,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """Global session inventory should list multiple live sessions distinctly."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        sessions = call_gdb_tool("gdb_session_query", {"action": "list", "query": {}})
        assert sessions["status"] == "success"
        assert sessions["action"] == "list"
        listed_ids = {
            integer(item["session_id"])
            for item in mapping_list(dig(sessions, "result", "sessions"))
        }
        assert listed_ids == {session_id_1, session_id_2}

        status_1 = status_query(call_gdb_tool, session_id_1)
        status_2 = status_query(call_gdb_tool, session_id_2)
        assert dig(status_1, "result", "debugger_alive") is True
        assert dig(status_2, "result", "debugger_alive") is True
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_breakpoints_are_isolated_between_sessions_v2(
    compiled_program_1: str,
    compiled_program_2: str,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """Breakpoints created in one session should not leak into another."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        create_breakpoint(call_gdb_tool, session_id_1, "main")

        listed_1 = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id_1, "action": "list", "query": {}},
        )
        listed_2 = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id_2, "action": "list", "query": {}},
        )
        assert len(mapping_list(dig(listed_1, "result", "breakpoints"))) == 1
        assert len(mapping_list(dig(listed_2, "result", "breakpoints"))) == 0
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_execution_state_and_backtrace_are_isolated_v2(
    compiled_program_1: str,
    compiled_program_2: str,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """Execution and backtrace queries should be routed to the selected session only."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        for session_id in (session_id_1, session_id_2):
            create_breakpoint(call_gdb_tool, session_id, "main")
            run(call_gdb_tool, session_id)

        backtrace_1 = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id_1, "action": "backtrace", "query": {}},
        )
        backtrace_2 = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id_2, "action": "backtrace", "query": {}},
        )
        assert len(mapping_list(dig(backtrace_1, "result", "frames"))) > 0
        assert len(mapping_list(dig(backtrace_2, "result", "frames"))) > 0
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_stop_one_session_does_not_affect_other_v2(
    compiled_program_1: str,
    compiled_program_2: str,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """Stopping one session should not invalidate another live session."""

    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        stop = stop_session(session_id_2)
        assert stop["status"] == "success"

        status_1 = status_query(call_gdb_tool, session_id_1)
        assert status_1["status"] == "success"

        status_2 = status_query(call_gdb_tool, session_id_2)
        assert status_2["status"] == "error"
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_invalid_session_id_returns_error_v2(call_gdb_tool: CallGdbTool) -> None:
    """Invalid session IDs should return the public v2 error envelope."""

    result = call_gdb_tool(
        "gdb_session_query",
        {"session_id": 999, "action": "status", "query": {}},
    )
    assert result["status"] == "error"
    assert "Invalid session_id: 999" in text(result["message"])
    assert "gdb_session_start" in text(result["message"])
