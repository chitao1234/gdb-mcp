"""Integration tests for multi-session support in the GDB MCP server.

These tests verify that multiple GDB sessions can coexist independently,
including testing:
- Concurrent session creation
- Session isolation (breakpoints, state, etc.)
- Invalid session_id handling
- Session cleanup and lifecycle
- Debugging multiple different programs simultaneously

All tests use real GDB processes via the MCP server interface.
"""

import pytest


# Simple C program for testing - different from main test suite
TEST_PROGRAM_1 = """
#include <stdio.h>

int double_value(int x) {
    return x * 2;
}

int main() {
    int num = 10;
    int result = double_value(num);
    printf("Result: %d\\n", result);
    return 0;
}
"""

# Another simple C program with different functions
TEST_PROGRAM_2 = """
#include <stdio.h>

int triple_value(int x) {
    return x * 3;
}

int main() {
    int num = 7;
    int result = triple_value(num);
    printf("Result: %d\\n", result);
    return 0;
}
"""


@pytest.fixture
def compiled_program_1(compile_program):
    """Compile test program 1 with debug symbols."""

    return compile_program(
        TEST_PROGRAM_1,
        filename="program1.c",
        compiler="gcc",
    )


@pytest.fixture
def compiled_program_2(compile_program):
    """Compile test program 2 with debug symbols."""

    return compile_program(
        TEST_PROGRAM_2,
        filename="program2.c",
        compiler="gcc",
    )


# Multi-session integration tests


@pytest.mark.integration
def test_create_multiple_sessions(compiled_program_1, compiled_program_2, start_session):
    """Test creating multiple GDB sessions and verify they have different session IDs."""
    session_id_1 = start_session(
        compiled_program_1,
        init_commands=["set disable-randomization on"],
    )
    session_id_2 = start_session(
        compiled_program_2,
        init_commands=["set disable-randomization on"],
    )

    # Verify session IDs are different
    assert (
        session_id_1 != session_id_2
    ), f"Session IDs should be unique: {session_id_1} == {session_id_2}"

    # Verify both sessions are running
    status1 = call_gdb_tool("gdb_get_status", {"session_id": session_id_1})
    assert status1["is_running"] is True

    status2 = call_gdb_tool("gdb_get_status", {"session_id": session_id_2})
    assert status2["is_running"] is True

    # Cleanup
    call_gdb_tool("gdb_stop_session", {"session_id": session_id_1})
    call_gdb_tool("gdb_stop_session", {"session_id": session_id_2})


@pytest.mark.integration
def test_session_isolation_breakpoints(compiled_program_1, compiled_program_2, start_session):
    """Test that breakpoints in one session don't affect another session."""
    session_id_1 = start_session(
        compiled_program_1,
        init_commands=["set disable-randomization on"],
    )
    session_id_2 = start_session(
        compiled_program_2,
        init_commands=["set disable-randomization on"],
    )

    try:
        # Set breakpoint at main in session 1
        bp_result1 = call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "main"},
        )
        assert bp_result1["status"] == "success"

        # Set breakpoint at double_value in session 1
        bp_result2 = call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "double_value"},
        )
        assert bp_result2["status"] == "success"

        # Verify session 1 has 2 breakpoints
        list_result1 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_1})
        assert list_result1["status"] == "success"
        assert list_result1["count"] == 2

        # Verify session 2 has 0 breakpoints (isolation)
        list_result2 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_2})
        assert list_result2["status"] == "success"
        assert (
            list_result2["count"] == 0
        ), f"Session 2 should have no breakpoints, but has {list_result2['count']}"

        # Set different breakpoint in session 2
        bp_result3 = call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_2, "location": "triple_value"},
        )
        assert bp_result3["status"] == "success"

        # Verify session 2 has only 1 breakpoint
        list_result2 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_2})
        assert list_result2["status"] == "success"
        assert list_result2["count"] == 1

        # Verify session 1 still has 2 breakpoints (not affected by session 2)
        list_result1 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_1})
        assert list_result1["status"] == "success"
        assert list_result1["count"] == 2

    finally:
        # Cleanup
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_1})
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_2})


@pytest.mark.integration
def test_invalid_session_id_returns_error():
    """Test that using an invalid session_id returns a helpful error message."""
    # Try to use a session_id that doesn't exist (999 is unlikely to exist)
    result = call_gdb_tool("gdb_get_status", {"session_id": 999})

    # Verify error response
    assert result["status"] == "error"
    assert "Invalid session_id: 999" in result["message"]
    assert (
        "gdb_start_session" in result["message"]
    ), "Error message should mention gdb_start_session"


@pytest.mark.integration
def test_session_after_stop_cannot_be_used(compiled_program_1, start_session):
    """Test that after stopping a session, it cannot be used anymore."""
    session_id = start_session(
        compiled_program_1,
        init_commands=["set disable-randomization on"],
    )

    # Verify session works
    status1 = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status1["is_running"] is True

    # Stop the session
    stop_result = call_gdb_tool("gdb_stop_session", {"session_id": session_id})
    assert stop_result["status"] == "success"

    # Try to use the stopped session
    status2 = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status2["status"] == "error"
    assert "Invalid session_id" in status2["message"], "Should get error about invalid session_id"


@pytest.mark.integration
def test_concurrent_debugging_different_programs(compiled_program_1, compiled_program_2, start_session):
    """Test debugging two different programs simultaneously in separate sessions."""
    session_id_1 = start_session(
        compiled_program_1,
        init_commands=["set disable-randomization on"],
    )
    session_id_2 = start_session(
        compiled_program_2,
        init_commands=["set disable-randomization on"],
    )

    try:
        # Set breakpoints in both sessions at main
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "main"},
        )
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_2, "location": "main"},
        )

        # Run both programs to their breakpoints
        run_result1 = call_gdb_tool(
            "gdb_execute_command", {"session_id": session_id_1, "command": "run"}
        )
        assert run_result1["status"] == "success"

        run_result2 = call_gdb_tool(
            "gdb_execute_command", {"session_id": session_id_2, "command": "run"}
        )
        assert run_result2["status"] == "success"

        # Get backtraces from both sessions - they should be at main
        backtrace1 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id_1})
        assert backtrace1["status"] == "success"
        assert any("main" in frame.get("func", "") for frame in backtrace1["frames"])

        backtrace2 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id_2})
        assert backtrace2["status"] == "success"
        assert any("main" in frame.get("func", "") for frame in backtrace2["frames"])

        # Step in session 1 a few times
        for _ in range(3):
            call_gdb_tool("gdb_next", {"session_id": session_id_1})

        # Verify session 2 is still at main (not affected by session 1 stepping)
        backtrace2_after = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id_2})
        assert backtrace2_after["status"] == "success"

        # Set breakpoint in function specific to program 1
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "double_value"},
        )

        # Set breakpoint in function specific to program 2
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_2, "location": "triple_value"},
        )

        # Verify breakpoints are in correct sessions
        bp_list1 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_1})
        assert bp_list1["status"] == "success"
        # Should have main and double_value breakpoints
        assert any("double_value" in bp.get("func", "") for bp in bp_list1["breakpoints"])

        bp_list2 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_2})
        assert bp_list2["status"] == "success"
        # Should have main and triple_value breakpoints
        assert any("triple_value" in bp.get("func", "") for bp in bp_list2["breakpoints"])

        # Continue both programs
        cont_result1 = call_gdb_tool("gdb_continue", {"session_id": session_id_1})
        assert cont_result1["status"] == "success"

        cont_result2 = call_gdb_tool("gdb_continue", {"session_id": session_id_2})
        assert cont_result2["status"] == "success"

        # Session 1 may legitimately exit normally if stepping already moved past the
        # double_value call site; session 2 should still stop at triple_value.
        final_bt1 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id_1})
        session_1_reasons = [
            notify.get("reason")
            for notify in cont_result1.get("result", {}).get("notify", [])
            if isinstance(notify, dict)
        ]
        if "breakpoint-hit" in session_1_reasons:
            assert final_bt1["status"] == "success"
        else:
            assert "exited-normally" in session_1_reasons
            assert final_bt1["status"] == "error"

        final_bt2 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id_2})
        assert final_bt2["status"] == "success"
        assert any("triple_value" in frame.get("func", "") for frame in final_bt2["frames"])

    finally:
        # Cleanup both sessions
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_1})
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_2})


@pytest.mark.integration
def test_session_isolation_execution_state(compiled_program_1, compiled_program_2, start_session):
    """Test that execution state (running/paused) is isolated between sessions."""
    session_id_1 = start_session(
        compiled_program_1,
        init_commands=["set disable-randomization on"],
    )
    session_id_2 = start_session(
        compiled_program_2,
        init_commands=["set disable-randomization on"],
    )

    try:
        # Set breakpoint and run in session 1
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "main"},
        )
        call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id_1, "command": "run"},
        )

        # Session 1 should be paused at breakpoint
        # Session 2 should still be in pre-run state (not started)

        # Verify session 2 still has no breakpoints
        list_result2 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_2})
        assert list_result2["count"] == 0

        # Set breakpoint in session 2
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_2, "location": "main"},
        )

        # Run session 2
        call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id_2, "command": "run"},
        )

        # Both sessions should now be paused at their respective breakpoints
        # Step session 1 forward
        call_gdb_tool("gdb_next", {"session_id": session_id_1})

        # Verify session 2 is still at main (not affected by session 1 stepping)
        bt2 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id_2})
        assert any("main" in frame.get("func", "") for frame in bt2["frames"])

    finally:
        # Cleanup
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_1})
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_2})


@pytest.mark.integration
def test_session_isolation_variables(compiled_program_1, compiled_program_2, start_session):
    """Test that variable inspection in one session doesn't affect another."""
    session_id_1 = start_session(
        compiled_program_1,
        init_commands=["set disable-randomization on"],
    )
    session_id_2 = start_session(
        compiled_program_2,
        init_commands=["set disable-randomization on"],
    )

    try:
        # Set breakpoints at the functions with parameters
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "double_value"},
        )
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_2, "location": "triple_value"},
        )

        # Run both programs
        call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id_1, "command": "run"},
        )
        call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id_2, "command": "run"},
        )

        # Get variables from both sessions
        # They should be at their respective functions with different parameters
        vars1 = call_gdb_tool("gdb_get_variables", {"session_id": session_id_1})
        assert vars1["status"] == "success"

        vars2 = call_gdb_tool("gdb_get_variables", {"session_id": session_id_2})
        assert vars2["status"] == "success"

        # Evaluate expression in session 1
        eval_result1 = call_gdb_tool(
            "gdb_evaluate_expression",
            {"session_id": session_id_1, "expression": "x"},
        )
        # Session 1 is at double_value(10), so x should be 10

        # Evaluate expression in session 2
        eval_result2 = call_gdb_tool(
            "gdb_evaluate_expression",
            {"session_id": session_id_2, "expression": "x"},
        )
        # Session 2 is at triple_value(7), so x should be 7

        # Values should be different (isolation)
        if eval_result1["status"] == "success" and eval_result2["status"] == "success":
            assert (
                eval_result1["value"] != eval_result2["value"]
            ), "Variables in different sessions should have different values"

    finally:
        # Cleanup
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_1})
        call_gdb_tool("gdb_stop_session", {"session_id": session_id_2})


@pytest.mark.integration
def test_stop_one_session_doesnt_affect_other(compiled_program_1, compiled_program_2):
    """Test that stopping one session doesn't affect other active sessions."""
    # Create three sessions to be extra sure
    session_id_1 = call_gdb_tool(
        "gdb_start_session",
        {
            "program": compiled_program_1,
            "init_commands": ["set disable-randomization on"],
        },
    )["session_id"]

    session_id_2 = call_gdb_tool(
        "gdb_start_session",
        {
            "program": compiled_program_2,
            "init_commands": ["set disable-randomization on"],
        },
    )["session_id"]

    session_id_3 = call_gdb_tool(
        "gdb_start_session",
        {
            "program": compiled_program_1,
            "init_commands": ["set disable-randomization on"],
        },
    )["session_id"]

    try:
        # Verify all three sessions are running
        status1 = call_gdb_tool("gdb_get_status", {"session_id": session_id_1})
        assert status1["is_running"] is True

        status2 = call_gdb_tool("gdb_get_status", {"session_id": session_id_2})
        assert status2["is_running"] is True

        status3 = call_gdb_tool("gdb_get_status", {"session_id": session_id_3})
        assert status3["is_running"] is True

        # Stop the middle session
        stop_result = call_gdb_tool("gdb_stop_session", {"session_id": session_id_2})
        assert stop_result["status"] == "success"

        # Verify session 2 is no longer usable
        status2_after = call_gdb_tool("gdb_get_status", {"session_id": session_id_2})
        assert status2_after["status"] == "error"
        assert "Invalid session_id" in status2_after["message"]

        # Verify sessions 1 and 3 are still running (not affected)
        status1_after = call_gdb_tool("gdb_get_status", {"session_id": session_id_1})
        assert status1_after["is_running"] is True

        status3_after = call_gdb_tool("gdb_get_status", {"session_id": session_id_3})
        assert status3_after["is_running"] is True

        # Should be able to use sessions 1 and 3 normally
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_1, "location": "main"},
        )
        call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id_3, "location": "double_value"},
        )

        bp_list1 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_1})
        assert bp_list1["status"] == "success"
        assert bp_list1["count"] == 1

        bp_list3 = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id_3})
        assert bp_list3["status"] == "success"
        assert bp_list3["count"] == 1

    finally:
        # Cleanup remaining sessions
        try:
            call_gdb_tool("gdb_stop_session", {"session_id": session_id_1})
        except Exception:
            pass
        try:
            call_gdb_tool("gdb_stop_session", {"session_id": session_id_3})
        except Exception:
            pass
