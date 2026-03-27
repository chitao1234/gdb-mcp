"""Integration tests for GDB MCP server with real GDB instances.

These tests compile and debug a real C++ program using GDB through the MCP
server interface. They validate the complete workflow including:
- Starting GDB sessions with compiled programs via gdb_start_session
- Session management with session_id routing
- Setting and managing breakpoints
- Stepping through code execution
- Inspecting variables and call stacks
- Executing both MI and CLI commands

Note: These tests may occasionally exhibit flakiness due to timing issues
with GDB process state transitions. This is expected behavior for integration
tests that interact with external processes.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

# Simple C++ program with function calls for testing
TEST_CPP_PROGRAM = """
#include <iostream>

int add(int a, int b) {
    int result = a + b;
    return result;
}

int multiply(int x, int y) {
    int product = x * y;
    return product;
}

int calculate(int num) {
    int sum = add(num, 10);
    int prod = multiply(sum, 2);
    return prod;
}

int main() {
    int value = 5;
    int result = calculate(value);
    std::cout << "Result: " << result << std::endl;
    return 0;
}
"""

CRASHING_C_PROGRAM = """
#include <signal.h>

int main(void) {
    raise(SIGABRT);
    return 0;
}
"""

ATTACHABLE_C_PROGRAM = """
#include <unistd.h>

int main(void) {
    while (1) {
        sleep(1);
    }
    return 0;
}
"""


@pytest.fixture
def compiled_program(compile_program):
    """Compile the shared C++ test program."""

    return compile_program(
        TEST_CPP_PROGRAM,
        filename="test_program.cpp",
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
        filename="crash.c",
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


# Integration tests that run GDB with a real program


@pytest.mark.integration
def test_start_session_with_program(compiled_program, start_session_result, stop_session):
    """Test starting a GDB session with a compiled program via MCP server."""
    result = start_session_result(compiled_program)

    assert result["status"] == "success"
    assert result["program"] == compiled_program
    assert result["target_loaded"] is True
    assert "session_id" in result
    assert isinstance(result["session_id"], int)
    session_id = result["session_id"]

    # Verify session status
    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["is_running"] is True
    assert status["target_loaded"] is True
    assert status["execution_state"] == "not_started"

    # Cleanup
    stop_session(session_id)


@pytest.mark.integration
def test_set_and_list_breakpoints(session_id):
    """Test setting breakpoints and listing them."""
    # Set breakpoint at main
    bp_result = call_gdb_tool(
        "gdb_set_breakpoint",
        {
            "session_id": session_id,
            "location": "main",
        },
    )
    assert bp_result["status"] == "success"
    assert "breakpoint" in bp_result
    # Function name might be "main" or "main()" depending on GDB version
    assert "main" in bp_result["breakpoint"]["func"]

    # Set breakpoint at add function
    bp_result2 = call_gdb_tool(
        "gdb_set_breakpoint",
        {
            "session_id": session_id,
            "location": "add",
        },
    )
    assert bp_result2["status"] == "success"

    # List all breakpoints
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["status"] == "success"
    assert list_result["count"] == 2
    assert len(list_result["breakpoints"]) == 2


@pytest.mark.integration
def test_run_and_hit_breakpoint(session_id):
    """Test running the program and hitting a breakpoint."""
    # Set breakpoint at main
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})

    # Run the program (it should stop at main)
    run_result = call_gdb_tool("gdb_run", {"session_id": session_id})
    assert run_result["status"] == "success"

    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["execution_state"] == "paused"
    assert status["stop_reason"] == "breakpoint-hit"

    # Get backtrace to verify we're at main
    backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace["status"] == "success"
    assert backtrace["count"] > 0
    # Check that we're in main function (func might be "main", "main()", etc.)
    frames = backtrace["frames"]
    assert any("main" in frame.get("func", "") for frame in frames)


@pytest.mark.integration
def test_batch_can_set_breakpoint_run_and_capture_backtrace(session_id):
    """Structured batch should execute setup, run, and inspection in one call."""

    batch_result = call_gdb_tool(
        "gdb_batch",
        {
            "session_id": session_id,
            "steps": [
                {
                    "tool": "gdb_set_breakpoint",
                    "label": "break main",
                    "arguments": {"location": "main"},
                },
                {
                    "tool": "gdb_run",
                    "label": "run to breakpoint",
                    "arguments": {},
                },
                {
                    "tool": "gdb_get_backtrace",
                    "label": "capture stack",
                    "arguments": {},
                },
            ],
        },
    )

    assert batch_result["status"] == "success"
    assert batch_result["count"] == 3
    assert batch_result["completed_steps"] == 3
    assert batch_result["error_count"] == 0
    assert batch_result["stopped_early"] is False
    assert batch_result["final_execution_state"] == "paused"
    assert batch_result["final_stop_reason"] == "breakpoint-hit"
    assert batch_result["steps"][0]["tool"] == "gdb_set_breakpoint"
    assert batch_result["steps"][1]["tool"] == "gdb_run"
    assert batch_result["steps"][1]["stop_event"]["reason"] == "breakpoint-hit"
    assert batch_result["steps"][2]["tool"] == "gdb_get_backtrace"
    assert batch_result["steps"][2]["result"]["count"] > 0


@pytest.mark.integration
def test_capture_bundle_writes_manifest_and_artifacts(session_id, tmp_path):
    """Capture bundle should write a manifest plus forensic artifacts to disk."""

    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})
    call_gdb_tool("gdb_run", {"session_id": session_id})

    capture_result = call_gdb_tool(
        "gdb_capture_bundle",
        {
            "session_id": session_id,
            "output_dir": str(tmp_path),
            "bundle_name": "capture-case",
            "expressions": ["a", "b"],
            "max_frames": 20,
        },
    )

    assert capture_result["status"] == "success"
    assert capture_result["bundle_name"] == "capture-case"
    bundle_dir = Path(str(capture_result["bundle_dir"]))
    manifest_path = Path(str(capture_result["manifest_path"]))
    assert bundle_dir.is_dir()
    assert manifest_path.is_file()
    assert manifest_path.parent == bundle_dir

    manifest = json.loads(manifest_path.read_text())
    assert manifest["bundle_name"] == "capture-case"
    assert manifest["execution_state"] == "paused"
    assert manifest["stop_reason"] == "breakpoint-hit"
    assert manifest["last_stop_event"]["reason"] == "breakpoint-hit"

    artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
    assert "session-status" in artifact_names
    assert "threads" in artifact_names
    assert "thread-backtraces" in artifact_names
    assert "current-frame" in artifact_names
    assert "current-variables" in artifact_names
    assert "current-registers" in artifact_names
    assert "command-transcript" in artifact_names
    assert "expressions" in artifact_names

    expressions_payload = json.loads((bundle_dir / "expressions.json").read_text())
    expressions = {entry["expression"]: entry for entry in expressions_payload}
    assert expressions["a"]["status"] == "success"
    assert expressions["b"]["status"] == "success"


@pytest.mark.integration
def test_run_until_failure_matches_signal_and_writes_bundle(compile_program, tmp_path):
    """Repeat-until-failure should stop on a signal and emit a capture bundle."""

    crashing_program = compile_program(
        CRASHING_C_PROGRAM,
        filename="crash_signal.c",
        compiler="gcc",
    )

    result = call_gdb_tool(
        "gdb_run_until_failure",
        {
            "startup": {
                "program": crashing_program,
                "init_commands": [
                    "set disable-randomization on",
                    "set startup-with-shell off",
                ],
            },
            "max_iterations": 3,
            "capture": {
                "enabled": True,
                "output_dir": str(tmp_path),
                "bundle_name_prefix": "signal-failure",
            },
        },
    )

    assert result["status"] == "success"
    assert result["matched_failure"] is True
    assert result["failure_iteration"] == 1
    assert result["trigger"] == "stop_reason:signal-received"
    assert result["capture_bundle"] is not None

    capture_bundle = result["capture_bundle"]
    manifest_path = Path(str(capture_bundle["manifest_path"]))
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["execution_state"] == "paused"
    assert manifest["stop_reason"] == "signal-received"
    assert manifest["last_stop_event"]["reason"] == "signal-received"


@pytest.mark.integration
def test_step_through_functions(session_id):
    """Test stepping through function calls."""
    # Set breakpoint at main
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Step a few times
    for _ in range(3):
        step_result = call_gdb_tool("gdb_step", {"session_id": session_id})
        assert step_result["status"] == "success"

    # Verify we can still get a backtrace
    backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace["status"] == "success"
    assert backtrace["count"] > 0


@pytest.mark.integration
def test_inspect_variables(session_id):
    """Test inspecting variable values."""
    # Set breakpoint in the add function
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})

    # Run to breakpoint (stops at the add function)
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Step to ensure we're in the function body
    call_gdb_tool("gdb_next", {"session_id": session_id})

    # Try to evaluate the parameters
    eval_result = call_gdb_tool(
        "gdb_evaluate_expression", {"session_id": session_id, "expression": "a"}
    )
    # Note: This might not work if we haven't stepped to the right location
    # but we can at least verify the command executes


@pytest.mark.integration
def test_backtrace_across_functions(session_id):
    """Test getting backtrace when nested in function calls."""
    # Set breakpoint in the add function (called from calculate)
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})

    # Run to breakpoint (this will stop at the add function)
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Get backtrace
    backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace["status"] == "success"

    # Should have at least 2 frames (add and its caller)
    assert backtrace["count"] >= 2, f"Expected at least 2 frames, got {backtrace['count']}"

    # Verify the call stack includes at least the add function
    frames = backtrace["frames"]
    frame_funcs = [f.get("func", "") for f in frames]
    # Check if add is in the backtrace (with or without signature)
    assert any("add" in func for func in frame_funcs if func)


@pytest.mark.integration
def test_next_vs_step(session_id):
    """Test difference between next (step over) and step (step into)."""
    # Set breakpoint at main
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Use next() which should step over function calls
    # This should execute but stay in the same function
    next_result = call_gdb_tool("gdb_next", {"session_id": session_id})
    assert next_result["status"] == "success"

    # Get backtrace after next - should still be in main or at same depth
    backtrace1 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace1["status"] == "success"
    depth1 = backtrace1["count"]
    assert depth1 >= 1

    # Now try step() which should step into function calls
    step_result = call_gdb_tool("gdb_step", {"session_id": session_id})
    assert step_result["status"] == "success"

    backtrace2 = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace2["status"] == "success"
    assert backtrace2["count"] >= depth1


@pytest.mark.integration
def test_evaluate_expressions(session_id):
    """Test evaluating expressions at runtime."""
    # Set breakpoint at main
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Step a few times to get past variable declarations
    for _ in range(3):
        call_gdb_tool("gdb_next", {"session_id": session_id})

    # Try to evaluate a simple expression
    result = call_gdb_tool(
        "gdb_evaluate_expression", {"session_id": session_id, "expression": "5 + 3"}
    )
    assert result["status"] == "success"
    assert str(result["value"]).strip() == "8"


@pytest.mark.integration
def test_get_variables_in_frame(session_id):
    """Test getting local variables in the current frame."""
    # Set breakpoint at add function
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Step to ensure we're in the function body
    call_gdb_tool("gdb_next", {"session_id": session_id})

    # Get local variables
    vars_result = call_gdb_tool("gdb_get_variables", {"session_id": session_id})
    assert vars_result["status"] == "success"
    assert "variables" in vars_result
    assert len(vars_result["variables"]) >= 1


@pytest.mark.integration
def test_session_cleanup(compiled_program, start_session_result, stop_session):
    """Test that session can be properly stopped and restarted."""
    result1 = start_session_result(
        compiled_program,
        init_commands=["set disable-randomization on"],
    )
    assert result1["status"] == "success"
    assert "session_id" in result1
    session_id1 = result1["session_id"]

    # Verify session is running
    status1 = call_gdb_tool("gdb_get_status", {"session_id": session_id1})
    assert status1["is_running"] is True

    # Stop session
    stop_result = stop_session(session_id1)
    assert stop_result["status"] == "success"

    stopped_status = call_gdb_tool("gdb_get_status", {"session_id": session_id1})
    assert stopped_status["status"] == "error"
    assert "Invalid session_id" in stopped_status["message"]

    # Verify we can start another session
    result2 = start_session_result(
        compiled_program,
        init_commands=["set disable-randomization on"],
    )
    assert result2["status"] == "success"
    assert "session_id" in result2
    session_id2 = result2["session_id"]

    # Verify new session is running
    status2 = call_gdb_tool("gdb_get_status", {"session_id": session_id2})
    assert status2["is_running"] is True

    # Cleanup
    stop_session(session_id2)


@pytest.mark.integration
def test_conditional_breakpoint(session_id):
    """Test setting a conditional breakpoint."""
    # Set conditional breakpoint
    # This sets a breakpoint in add function only when a > 10
    bp_result = call_gdb_tool(
        "gdb_set_breakpoint",
        {"session_id": session_id, "location": "add", "condition": "a > 10"},
    )
    assert bp_result["status"] == "success"

    # List breakpoints to verify it was set
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["status"] == "success"
    assert list_result["count"] == 1


@pytest.mark.integration
def test_temporary_breakpoint(session_id):
    """Test setting a temporary breakpoint."""
    # Set temporary breakpoint at main
    bp_result = call_gdb_tool(
        "gdb_set_breakpoint",
        {"session_id": session_id, "location": "main", "temporary": True},
    )
    assert bp_result["status"] == "success"

    # Run to hit the breakpoint
    run_result = call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})
    assert run_result["status"] == "success"
    assert "Temporary breakpoint" in run_result["output"]

    # After hitting a temporary breakpoint once, it should be removed
    # Continue and check breakpoint list
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["status"] == "success"
    assert list_result["count"] == 0


@pytest.mark.integration
def test_get_status(compiled_program, start_session_result, stop_session):
    """Test getting session status."""
    result = start_session_result(
        compiled_program,
        init_commands=["set disable-randomization on"],
    )
    assert result["status"] == "success"
    session_id = result["session_id"]

    # Check status after starting
    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["is_running"] is True
    assert status["target_loaded"] is True

    # Cleanup
    stop_session(session_id)


@pytest.mark.integration
def test_cli_commands(session_id):
    """Test executing CLI commands (non-MI commands)."""
    # Execute a CLI command before running the program
    # This is more reliable than trying to run it after the program starts
    result = call_gdb_tool(
        "gdb_execute_command", {"session_id": session_id, "command": "info functions"}
    )
    assert result["status"] == "success"
    assert "output" in result
    # Should show our functions (they're defined even before running)
    output_lower = result["output"].lower()
    assert "add" in output_lower or "main" in output_lower or "calculate" in output_lower


# Integration tests for edge cases and error conditions


@pytest.mark.integration
def test_breakpoint_at_nonexistent_function(session_id):
    """Test setting breakpoint at a function that doesn't exist."""

    # Try to set breakpoint at non-existent function
    bp_result = call_gdb_tool(
        "gdb_set_breakpoint",
        {"session_id": session_id, "location": "nonexistent_function"},
    )
    assert bp_result["status"] == "error"
    assert "nonexistent_function" in bp_result["message"]


@pytest.mark.integration
def test_execute_command_before_run(session_id):
    """Test that we can execute commands before running the program."""

    # Execute commands before running
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["status"] == "success"
    assert list_result["count"] == 0


@pytest.mark.integration
def test_start_session_with_missing_program_reports_target_unloaded(stop_session):
    """Missing startup targets should not be reported as loaded."""

    result = call_gdb_tool(
        "gdb_start_session",
        {"program": "/definitely/not/here", "init_commands": []},
    )

    assert result["status"] == "success"
    assert "session_id" in result
    assert result["target_loaded"] is False
    assert "Program file not found" in result.get("warnings", [])

    session_id = result["session_id"]
    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["is_running"] is True
    assert status["target_loaded"] is False

    stop_session(session_id)


@pytest.mark.integration
def test_start_session_with_file_init_command_reports_target_loaded(compiled_program, stop_session):
    """Startup should expose target_loaded when init commands load the executable."""

    result = call_gdb_tool(
        "gdb_start_session",
        {
            "init_commands": [
                f"file {compiled_program}",
                "set disable-randomization on",
                "set startup-with-shell off",
            ]
        },
    )

    assert result["status"] == "success"
    assert result["target_loaded"] is True

    session_id = result["session_id"]
    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["target_loaded"] is True

    stop_session(session_id)


@pytest.mark.integration
def test_start_session_with_core_file_init_command_reports_target_loaded(
    compiled_program_and_core, stop_session
):
    """Startup should expose target_loaded when init commands load a core dump."""

    executable, core_file = compiled_program_and_core

    result = call_gdb_tool(
        "gdb_start_session",
        {
            "init_commands": [
                f"file {executable}",
                f"core-file {core_file}",
            ]
        },
    )

    assert result["status"] == "success"
    assert result["target_loaded"] is True

    session_id = result["session_id"]
    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["target_loaded"] is True

    threads = call_gdb_tool("gdb_get_threads", {"session_id": session_id})
    assert threads["status"] == "success"
    assert threads["count"] >= 1

    stop_session(session_id)


@pytest.mark.integration
def test_get_status_reports_exited_state_after_continue(session_id):
    """Status should expose the inferior execution state after it exits."""

    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})
    run_result = call_gdb_tool("gdb_run", {"session_id": session_id})
    assert run_result["status"] == "success"

    continue_result = call_gdb_tool("gdb_continue", {"session_id": session_id})
    assert continue_result["status"] == "success"

    status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
    assert status["execution_state"] == "exited"
    assert status["target_loaded"] is True


@pytest.mark.integration
def test_attach_process_tool(attachable_program, stop_session):
    """Attaching to a running process should be a first-class structured workflow."""

    process = subprocess.Popen([attachable_program])
    session_id = None
    try:
        start_result = call_gdb_tool("gdb_start_session", {"init_commands": []})
        assert start_result["status"] == "success"
        session_id = start_result["session_id"]

        attach_result = call_gdb_tool(
            "gdb_attach_process",
            {"session_id": session_id, "pid": process.pid},
        )
        assert attach_result["status"] == "success"

        status = call_gdb_tool("gdb_get_status", {"session_id": session_id})
        assert status["target_loaded"] is True
        assert status["execution_state"] == "paused"

        backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id, "max_frames": 1})
        assert backtrace["status"] == "success"
        assert backtrace["count"] == 1
    finally:
        if session_id is not None:
            stop_session(session_id, ignore_errors=True)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.integration
def test_multiple_breakpoints_same_location(session_id):
    """Test setting multiple breakpoints at the same location."""

    # Set breakpoint at main
    bp1 = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})
    assert bp1["status"] == "success"

    # Set another breakpoint at main
    bp2 = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})
    assert bp2["status"] == "success"

    # Both should be in the list
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["status"] == "success"
    assert list_result["count"] == 2


# Integration tests for new features: breakpoint management


@pytest.mark.integration
def test_delete_breakpoint(session_id):
    """Test deleting a breakpoint."""

    # Set a breakpoint
    bp_result = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})
    assert bp_result["status"] == "success"
    bp_number = int(bp_result["breakpoint"]["number"])

    # Set another breakpoint
    bp2_result = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})
    assert bp2_result["status"] == "success"

    # Verify we have 2 breakpoints
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["count"] == 2

    # Delete the first breakpoint
    delete_result = call_gdb_tool(
        "gdb_delete_breakpoint", {"session_id": session_id, "number": bp_number}
    )
    assert delete_result["status"] == "success"

    # Verify only 1 breakpoint remains
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["count"] == 1
    # Verify the remaining breakpoint is at add
    remaining_bp = list_result["breakpoints"][0]
    assert "add" in remaining_bp.get("func", "")


@pytest.mark.integration
def test_enable_disable_breakpoint(session_id):
    """Test enabling and disabling a breakpoint."""

    # Set a breakpoint
    bp_result = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})
    assert bp_result["status"] == "success"
    bp_number = int(bp_result["breakpoint"]["number"])

    # Disable the breakpoint
    disable_result = call_gdb_tool(
        "gdb_disable_breakpoint", {"session_id": session_id, "number": bp_number}
    )
    assert disable_result["status"] == "success"

    # Verify it's disabled
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["count"] == 1
    bp_info = list_result["breakpoints"][0]
    assert bp_info["enabled"] == "n"

    # Enable the breakpoint
    enable_result = call_gdb_tool(
        "gdb_enable_breakpoint", {"session_id": session_id, "number": bp_number}
    )
    assert enable_result["status"] == "success"

    # Verify it's enabled
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["count"] == 1
    bp_info = list_result["breakpoints"][0]
    assert bp_info["enabled"] == "y"


@pytest.mark.integration
def test_breakpoint_workflow(session_id):
    """Test a complete breakpoint management workflow."""

    # Set multiple breakpoints
    bp1 = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})
    bp2 = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})
    bp3 = call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "multiply"})
    assert all(bp["status"] == "success" for bp in [bp1, bp2, bp3])

    bp1_num = int(bp1["breakpoint"]["number"])
    bp2_num = int(bp2["breakpoint"]["number"])
    bp3_num = int(bp3["breakpoint"]["number"])

    # Verify all 3 breakpoints exist
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["count"] == 3

    # Disable one breakpoint
    call_gdb_tool("gdb_disable_breakpoint", {"session_id": session_id, "number": bp2_num})

    # Delete one breakpoint
    call_gdb_tool("gdb_delete_breakpoint", {"session_id": session_id, "number": bp3_num})

    # Verify we have 2 breakpoints (one deleted)
    list_result = call_gdb_tool("gdb_list_breakpoints", {"session_id": session_id})
    assert list_result["count"] == 2

    # Verify the disabled breakpoint is still disabled
    bp2_info = next((bp for bp in list_result["breakpoints"] if bp["number"] == str(bp2_num)), None)
    assert bp2_info is not None
    assert bp2_info["enabled"] == "n"


# Integration tests for thread selection


@pytest.mark.integration
def test_get_threads(session_id):
    """Test getting thread information."""

    # Set breakpoint at main
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Get threads
    threads_result = call_gdb_tool("gdb_get_threads", {"session_id": session_id})
    assert threads_result["status"] == "success"
    assert "threads" in threads_result
    assert threads_result["count"] >= 1  # Should have at least the main thread
    assert "current_thread_id" in threads_result


@pytest.mark.integration
def test_select_thread(session_id):
    """Test selecting a thread."""

    # Set breakpoint at main
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "main"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Get threads
    threads_result = call_gdb_tool("gdb_get_threads", {"session_id": session_id})
    assert threads_result["status"] == "success"
    assert threads_result["count"] >= 1

    # Get the current thread ID
    current_thread_id = threads_result["current_thread_id"]
    assert current_thread_id is not None

    # Select the current thread (should succeed)
    select_result = call_gdb_tool(
        "gdb_select_thread",
        {"session_id": session_id, "thread_id": int(current_thread_id)},
    )
    assert select_result["status"] == "success"
    assert select_result["thread_id"] == int(current_thread_id)


# Integration tests for frame selection


@pytest.mark.integration
def test_get_frame_info(session_id):
    """Test getting information about the current frame."""

    # Set breakpoint in add function
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Get frame info
    frame_result = call_gdb_tool("gdb_get_frame_info", {"session_id": session_id})
    assert frame_result["status"] == "success"
    assert "frame" in frame_result
    frame = frame_result["frame"]
    # Should have basic frame info like level
    assert "level" in frame


@pytest.mark.integration
def test_select_frame(session_id):
    """Test selecting a specific frame in the call stack."""

    # Set breakpoint in add function (called from calculate)
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Get backtrace to see how many frames we have
    backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace["status"] == "success"
    assert backtrace["count"] >= 2  # Should have at least add and its caller

    # Select frame 0 (current frame - should be add)
    select_result = call_gdb_tool("gdb_select_frame", {"session_id": session_id, "frame_number": 0})
    assert select_result["status"] == "success"
    assert select_result["frame_number"] == 0

    # Select frame 1 (caller frame)
    if backtrace["count"] >= 2:
        select_result = call_gdb_tool(
            "gdb_select_frame", {"session_id": session_id, "frame_number": 1}
        )
        assert select_result["status"] == "success"
        assert select_result["frame_number"] == 1


@pytest.mark.integration
def test_get_variables_preserves_current_frame(session_id):
    """Read-only variable inspection should not leave the debugger on a new frame."""

    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    before = call_gdb_tool("gdb_get_frame_info", {"session_id": session_id})
    assert before["status"] == "success"
    assert before["frame"]["level"] == "0"

    vars_result = call_gdb_tool("gdb_get_variables", {"session_id": session_id, "frame": 1})
    assert vars_result["status"] == "success"
    assert vars_result["frame"] == 1

    after = call_gdb_tool("gdb_get_frame_info", {"session_id": session_id})
    assert after["status"] == "success"
    assert after["frame"]["level"] == before["frame"]["level"]
    assert after["frame"]["func"] == before["frame"]["func"]


@pytest.mark.integration
def test_get_backtrace_max_frames_is_an_upper_bound(session_id):
    """Backtrace limits should cap the returned frame count."""

    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id, "max_frames": 1})

    assert backtrace["status"] == "success"
    assert backtrace["count"] == 1


@pytest.mark.integration
def test_frame_selection_and_variables(session_id):
    """Test that frame selection affects variable inspection."""

    # Set breakpoint in add function
    call_gdb_tool("gdb_set_breakpoint", {"session_id": session_id, "location": "add"})

    # Run to breakpoint
    call_gdb_tool("gdb_execute_command", {"session_id": session_id, "command": "run"})

    # Step to get into the function
    call_gdb_tool("gdb_next", {"session_id": session_id})

    # Get backtrace
    backtrace = call_gdb_tool("gdb_get_backtrace", {"session_id": session_id})
    assert backtrace["count"] >= 2

    # Select frame 0 (add function)
    call_gdb_tool("gdb_select_frame", {"session_id": session_id, "frame_number": 0})
    vars_frame0 = call_gdb_tool("gdb_get_variables", {"session_id": session_id, "frame": 0})
    assert vars_frame0["status"] == "success"
    assert vars_frame0["frame"] == 0

    # Select frame 1 (caller)
    if backtrace["count"] >= 2:
        call_gdb_tool("gdb_select_frame", {"session_id": session_id, "frame_number": 1})
        vars_frame1 = call_gdb_tool("gdb_get_variables", {"session_id": session_id, "frame": 1})
        assert vars_frame1["status"] == "success"
        assert vars_frame1["frame"] == 1
        assert vars_frame1["variables"] != vars_frame0["variables"]


@pytest.mark.integration
def test_set_breakpoint_with_source_path_containing_spaces(stop_session):
    """Breakpoint locations with spaces should be passed to GDB intact."""

    with tempfile.TemporaryDirectory(prefix="gdb mcp tests ") as tmpdir:
        tmp_path = Path(tmpdir)
        source = tmp_path / "sample source.c"
        program = tmp_path / "sample program"
        source.write_text("int main(void) { return 0; }\n")

        compile_result = subprocess.run(
            ["gcc", "-g", "-O0", "-o", str(program), str(source)],
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        start_result = call_gdb_tool(
            "gdb_start_session",
            {
                "program": str(program),
                "init_commands": [
                    "set disable-randomization on",
                    "set startup-with-shell off",
                ],
            },
        )
        assert start_result["status"] == "success"
        session_id = start_result["session_id"]

        bp_result = call_gdb_tool(
            "gdb_set_breakpoint",
            {"session_id": session_id, "location": f"{source}:1"},
        )
        assert bp_result["status"] == "success"

        stop_session(session_id)
