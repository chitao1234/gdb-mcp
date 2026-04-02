"""Real-GDB integration coverage for core direct-v2 session workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .program_sources import (
    CRASHING_C_PROGRAM,
    DELAY_EXIT_C_PROGRAM,
    FORKING_C_PROGRAM,
    TEST_CPP_PROGRAM,
    WATCH_MEMORY_C_PROGRAM,
)


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


@pytest.mark.integration
def test_session_lifecycle_and_status_v2(
    compiled_program, start_session_result, call_gdb_tool
):
    """Session lifecycle queries should expose the direct v2 action envelope."""

    start = start_session_result(compiled_program)
    assert start["status"] == "success"
    assert start["target_loaded"] is True
    session_id = start["session_id"]

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert status["status"] == "success"
    assert status["action"] == "status"
    assert status["result"]["is_running"] is True
    assert status["result"]["target_loaded"] is True
    assert status["result"]["execution_state"] == "not_started"

    stop = call_gdb_tool(
        "gdb_session_manage",
        {"session_id": session_id, "action": "stop", "session": {}},
    )
    assert stop["status"] == "success"
    assert stop["action"] == "stop"
    assert "stopped" in stop["result"]["message"].lower()

    after_stop = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert after_stop["status"] == "error"
    assert "Invalid session_id" in after_stop["message"]


@pytest.mark.integration
def test_breakpoint_query_run_and_status_v2(session_id, call_gdb_tool):
    """Code breakpoints should use direct v2 manage/query calls."""

    create = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    assert create["status"] == "success"
    assert create["action"] == "create"
    assert "main" in create["result"]["breakpoint"]["func"]

    listed = call_gdb_tool(
        "gdb_breakpoint_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert listed["action"] == "list"
    assert listed["result"]["count"] == 1

    run = call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": {}},
    )
    assert run["status"] == "success"
    assert run["action"] == "run"

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert status["result"]["execution_state"] == "paused"
    assert status["result"]["stop_reason"] == "breakpoint-hit"


@pytest.mark.integration
def test_breakpoint_manage_flow_v2(session_id, call_gdb_tool):
    """Representative breakpoint mutations should stay on the v2 surface."""

    first = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    second = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )

    number = int(first["result"]["breakpoint"]["number"])
    other_number = int(second["result"]["breakpoint"]["number"])

    disable = call_gdb_tool(
        "gdb_breakpoint_manage",
        {"session_id": session_id, "action": "disable", "breakpoint": {"number": number}},
    )
    assert disable["status"] == "success"

    enable = call_gdb_tool(
        "gdb_breakpoint_manage",
        {"session_id": session_id, "action": "enable", "breakpoint": {"number": number}},
    )
    assert enable["status"] == "success"

    delete = call_gdb_tool(
        "gdb_breakpoint_manage",
        {"session_id": session_id, "action": "delete", "breakpoint": {"number": other_number}},
    )
    assert delete["status"] == "success"

    listed = call_gdb_tool(
        "gdb_breakpoint_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["result"]["count"] == 1


@pytest.mark.integration
def test_workflow_batch_v2(session_id, call_gdb_tool):
    """Workflow batch should report v2 step metadata without test-only translation."""

    batch = call_gdb_tool(
        "gdb_workflow_batch",
        {
            "session_id": session_id,
            "steps": [
                {
                    "tool": "gdb_breakpoint_manage",
                    "label": "break main",
                    "arguments": {
                        "action": "create",
                        "breakpoint": {"kind": "code", "location": "main"},
                    },
                },
                {
                    "tool": "gdb_execution_manage",
                    "label": "run",
                    "arguments": {"action": "run", "execution": {}},
                },
                {
                    "tool": "gdb_context_query",
                    "label": "stack",
                    "arguments": {"action": "backtrace", "query": {}},
                },
            ],
        },
    )

    assert batch["status"] == "success"
    assert batch["count"] == 3
    assert batch["error_count"] == 0
    assert batch["steps"][0]["tool"] == "gdb_breakpoint_manage"
    assert batch["steps"][1]["tool"] == "gdb_execution_manage"
    assert batch["steps"][1]["stop_event"]["reason"] == "breakpoint-hit"
    assert batch["steps"][2]["tool"] == "gdb_context_query"
    assert batch["steps"][2]["action"] == "backtrace"
    assert batch["steps"][2]["result"]["result"]["count"] > 0


@pytest.mark.integration
def test_capture_bundle_v2(session_id, tmp_path, call_gdb_tool):
    """Capture bundles should write artifacts from a live direct-v2 stop state."""

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

    bundle = call_gdb_tool(
        "gdb_capture_bundle",
        {"session_id": session_id, "output_dir": str(tmp_path), "bundle_name": "capture"},
    )
    assert bundle["status"] == "success"
    assert bundle["artifact_count"] > 0
    manifest = json.loads(Path(bundle["manifest_path"]).read_text())
    assert manifest["bundle_name"] == "capture"
    assert manifest["stop_reason"] == "breakpoint-hit"


@pytest.mark.integration
def test_run_until_failure_v2(compile_program, tmp_path, call_gdb_tool):
    """Failure campaign should operate on the public v2 workflow tool."""

    crashing = compile_program(
        CRASHING_C_PROGRAM,
        filename="crash_signal.c",
        compiler="gcc",
    )
    result = call_gdb_tool(
        "gdb_run_until_failure",
        {
            "startup": {
                "program": crashing,
                "init_commands": [
                    "set disable-randomization on",
                    "set startup-with-shell off",
                ],
            },
            "max_iterations": 1,
            "failure": {"stop_reasons": ["signal-received"]},
            "capture": {
                "enabled": True,
                "output_dir": str(tmp_path),
                "bundle_name": "failure",
            },
        },
    )
    assert result["status"] == "success"
    assert result["matched_failure"] is True
    assert result["capture_bundle"]["bundle_name"] == "failure"


@pytest.mark.integration
def test_watchpoint_v2(compile_program, start_session, stop_session, call_gdb_tool):
    """Watchpoint creation should route through breakpoint_manage(create)."""

    program = compile_program(
        WATCH_MEMORY_C_PROGRAM,
        filename="watch.c",
        compiler="gcc",
    )
    session_id = start_session(program)
    try:
        watch = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "watch", "expression": "watched", "access": "write"},
            },
        )
        assert watch["status"] == "success"

        run = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "run", "execution": {}},
        )
        assert run["status"] == "success"

        status = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id, "action": "status", "query": {}},
        )
        assert status["result"]["stop_reason"] in {
            "watchpoint-trigger",
            "watchpoint-scope",
        }
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_catchpoint_v2(compile_program, start_session, stop_session, call_gdb_tool):
    """Catchpoint creation should stay on the public v2 breakpoint tool."""

    program = compile_program(
        FORKING_C_PROGRAM,
        filename="forking.c",
        compiler="gcc",
    )
    session_id = start_session(program)
    try:
        catch = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "catch", "event": "fork"},
            },
        )
        assert catch["status"] == "success"

        run = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "run", "execution": {}},
        )
        assert run["status"] == "success"

        status = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id, "action": "status", "query": {}},
        )
        assert status["result"]["execution_state"] == "paused"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_wait_for_stop_after_background_run_v2(
    compile_program, start_session, stop_session, call_gdb_tool
):
    """Background runs should be paired with an explicit wait_for_stop action."""

    program = compile_program(
        DELAY_EXIT_C_PROGRAM,
        filename="delay.c",
        compiler="gcc",
    )
    session_id = start_session(program)
    try:
        background = call_gdb_tool(
            "gdb_execution_manage",
            {
                "session_id": session_id,
                "action": "run",
                "execution": {"wait": {"until": "acknowledged"}},
            },
        )
        assert background["status"] == "success"

        waited = call_gdb_tool(
            "gdb_execution_manage",
            {
                "session_id": session_id,
                "action": "wait_for_stop",
                "execution": {"timeout_sec": 10},
            },
        )
        assert waited["status"] == "success"
        assert waited["result"]["matched"] is True
        assert waited["result"]["execution_state"] == "exited"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_inferior_and_fork_controls_v2(session_id, call_gdb_tool):
    """Inferior lifecycle and fork policy changes should use their v2 family tools."""

    create = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "create", "inferior": {"make_current": False}},
    )
    assert create["status"] == "success"
    inferior_id = create["result"]["inferior_id"]

    listed = call_gdb_tool(
        "gdb_inferior_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert listed["result"]["count"] >= 2

    follow = call_gdb_tool(
        "gdb_inferior_manage",
        {
            "session_id": session_id,
            "action": "set_follow_fork_mode",
            "inferior": {"mode": "child"},
        },
    )
    assert follow["status"] == "success"

    detach = call_gdb_tool(
        "gdb_inferior_manage",
        {
            "session_id": session_id,
            "action": "set_detach_on_fork",
            "inferior": {"enabled": False},
        },
    )
    assert detach["status"] == "success"

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert status["result"]["follow_fork_mode"] == "child"
    assert status["result"]["detach_on_fork"] is False

    remove = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "remove", "inferior": {"inferior_id": inferior_id}},
    )
    assert remove["status"] == "success"
