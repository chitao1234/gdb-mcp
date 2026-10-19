"""Real-GDB integration coverage for core direct-v2 session workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gdb_mcp.session.execution import SessionExecutionService
from tests.support import dig, integer, mapping_list, text

from .conftest import (
    CallGdbTool,
    CompileProgram,
    StartSession,
    StartSessionResult,
    StopSession,
    compile_default_program,
    create_breakpoint,
    run,
    status_query,
)
from .program_sources import (
    CRASHING_C_PROGRAM,
    DELAY_EXIT_C_PROGRAM,
    FORKING_C_PROGRAM,
    WATCH_MEMORY_C_PROGRAM,
)


@pytest.fixture
def compiled_program(compile_program: CompileProgram) -> str:
    """Compile the shared C++ test program."""

    return compile_default_program(compile_program, filename="test_program.cpp")


@pytest.mark.integration
def test_session_lifecycle_and_status_v2(
    compiled_program: str,
    start_session_payload: StartSessionResult,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """Session lifecycle queries should expose the direct v2 action envelope."""

    start = start_session_payload(compiled_program)
    assert start["status"] == "success"
    assert start["target_loaded"] is True
    session_id = integer(start["session_id"])

    status = status_query(call_gdb_tool, session_id)
    assert status["status"] == "success"
    assert status["action"] == "status"
    assert dig(status, "result", "is_running") is True
    assert dig(status, "result", "target_loaded") is True
    assert dig(status, "result", "execution_state") == "not_started"

    stop = stop_session(session_id)
    assert stop["status"] == "success"
    assert stop["action"] == "stop"
    assert "stopped" in text(dig(stop, "result", "message")).lower()

    after_stop = status_query(call_gdb_tool, session_id)
    assert after_stop["status"] == "error"
    assert "Invalid session_id" in text(after_stop["message"])


@pytest.mark.integration
def test_breakpoint_query_run_and_status_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """Code breakpoints should use direct v2 manage/query calls."""

    create = create_breakpoint(call_gdb_tool, session_id, "main")
    assert create["status"] == "success"
    assert create["action"] == "create"
    assert "main" in text(dig(create, "result", "breakpoint", "func"))

    listed = call_gdb_tool(
        "gdb_breakpoint_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert listed["action"] == "list"
    assert dig(listed, "result", "count") == 1

    execution = run(call_gdb_tool, session_id)
    assert execution["status"] == "success"
    assert execution["action"] == "run"

    status = status_query(call_gdb_tool, session_id)
    assert dig(status, "result", "execution_state") == "paused"
    assert dig(status, "result", "stop_reason") == "breakpoint-hit"


@pytest.mark.integration
def test_breakpoint_manage_flow_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """Representative breakpoint mutations should stay on the v2 surface."""

    first = create_breakpoint(call_gdb_tool, session_id, "main")
    second = create_breakpoint(call_gdb_tool, session_id, "add")

    number = int(text(dig(first, "result", "breakpoint", "number")))
    other_number = int(text(dig(second, "result", "breakpoint", "number")))

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
    assert dig(listed, "result", "count") == 1


@pytest.mark.integration
def test_workflow_batch_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
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
    steps = mapping_list(batch["steps"])
    assert steps[0]["tool"] == "gdb_breakpoint_manage"
    assert steps[1]["tool"] == "gdb_execution_manage"
    assert dig(steps[1], "stop_event", "reason") == "breakpoint-hit"
    assert steps[2]["tool"] == "gdb_context_query"
    assert steps[2]["action"] == "backtrace"
    assert integer(dig(steps[2], "result", "result", "count")) > 0


@pytest.mark.integration
def test_capture_bundle_v2(session_id: int, tmp_path: Path, call_gdb_tool: CallGdbTool) -> None:
    """Capture bundles should write artifacts from a live direct-v2 stop state."""

    create_breakpoint(call_gdb_tool, session_id, "add")
    run(call_gdb_tool, session_id)

    bundle = call_gdb_tool(
        "gdb_capture_bundle",
        {"session_id": session_id, "output_dir": str(tmp_path), "bundle_name": "capture"},
    )
    assert bundle["status"] == "success"
    assert integer(bundle["artifact_count"]) > 0
    manifest = json.loads(Path(text(bundle["manifest_path"])).read_text())
    assert manifest["bundle_name"] == "capture"
    assert manifest["stop_reason"] == "breakpoint-hit"


@pytest.mark.integration
def test_run_until_failure_v2(
    compile_program: CompileProgram,
    default_init_commands: list[str],
    tmp_path: Path,
    call_gdb_tool: CallGdbTool,
) -> None:
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
                "init_commands": default_init_commands,
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
    assert dig(result, "capture_bundle", "bundle_name") == "failure"


@pytest.mark.integration
def test_watchpoint_v2(
    compile_program: CompileProgram,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
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

        execution = run(call_gdb_tool, session_id)
        assert execution["status"] == "success"

        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "stop_reason") in {
            "watchpoint-trigger",
            "watchpoint-scope",
        }
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_catchpoint_v2(
    compile_program: CompileProgram,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
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

        execution = run(call_gdb_tool, session_id)
        assert execution["status"] == "success"

        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "execution_state") == "paused"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_wait_for_stop_after_background_run_v2(
    compile_program: CompileProgram,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """Background runs should be paired with an explicit wait_for_stop action."""

    program = compile_program(
        DELAY_EXIT_C_PROGRAM,
        filename="delay.c",
        compiler="gcc",
    )
    session_id = start_session(program)
    try:
        background = run(call_gdb_tool, session_id, wait_until="acknowledged")
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
        assert dig(waited, "result", "matched") is True
        assert dig(waited, "result", "execution_state") == "exited"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_inferior_and_fork_controls_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """Inferior lifecycle and fork policy changes should use their v2 family tools."""

    create = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "create", "inferior": {"make_current": False}},
    )
    assert create["status"] == "success"
    inferior_id = integer(dig(create, "result", "inferior_id"))

    listed = call_gdb_tool(
        "gdb_inferior_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert integer(dig(listed, "result", "count")) >= 2

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

    status = status_query(call_gdb_tool, session_id)
    assert dig(status, "result", "follow_fork_mode") == "child"
    assert dig(status, "result", "detach_on_fork") is False

    remove = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "remove", "inferior": {"inferior_id": inferior_id}},
    )
    assert remove["status"] == "success"


@pytest.mark.integration
def test_inferior_create_falls_back_to_cli_v2(
    session_id: int, call_gdb_tool: CallGdbTool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI `add-inferior` fallback should create an inferior on a real GDB."""

    monkeypatch.setattr(
        SessionExecutionService,
        "_create_inferior",
        lambda self: self._create_inferior_legacy(),
    )

    create = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "create", "inferior": {"make_current": False}},
    )
    assert create["status"] == "success", create
    assert integer(dig(create, "result", "inferior_id")) >= 2

    listed = call_gdb_tool(
        "gdb_inferior_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert integer(dig(listed, "result", "count")) >= 2
