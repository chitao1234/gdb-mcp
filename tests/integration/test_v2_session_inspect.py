"""Real-GDB integration coverage for direct-v2 inspect and startup workflows."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gdb_mcp.session.inspection.disassembly import InspectionDisassembly
from gdb_mcp.session.inspection.memory import InspectionMemory
from tests.support import dig, integer, mapping, mapping_list

from .conftest import (
    CallGdbTool,
    CompileProgram,
    CompileProgramWithCore,
    StartSessionResult,
    StopSession,
    compile_default_program,
    create_breakpoint,
    run,
    status_query,
    terminate_process,
)
from .program_sources import (
    ATTACHABLE_C_PROGRAM,
    CRASHING_C_PROGRAM,
)


@pytest.fixture
def compiled_program(compile_program: CompileProgram) -> str:
    """Compile the shared C++ program used by inspect-oriented integration tests."""

    return compile_default_program(compile_program, filename="inspect_program.cpp")


@pytest.fixture
def compiled_program_and_core(
    compile_program_with_core: CompileProgramWithCore,
) -> tuple[str, str]:
    """Compile a crashing program and return its executable/core-dump paths."""

    return compile_program_with_core(
        CRASHING_C_PROGRAM,
        filename="inspect_crash.c",
        compiler="gcc",
    )


@pytest.fixture
def attachable_program(compile_program: CompileProgram) -> str:
    """Compile a long-running program that can be attached to."""

    return compile_program(
        ATTACHABLE_C_PROGRAM,
        filename="attachable.c",
        compiler="gcc",
    )


@pytest.mark.integration
def test_context_and_inspect_queries_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """Representative context and inspect queries should use the v2 envelopes."""

    create_breakpoint(call_gdb_tool, session_id, "add")
    run(call_gdb_tool, session_id)

    threads = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "threads", "query": {}},
    )
    assert threads["status"] == "success"
    assert threads["action"] == "threads"
    assert len(mapping_list(dig(threads, "result", "threads"))) >= 1

    backtrace = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "backtrace", "query": {"max_frames": 8}},
    )
    assert backtrace["status"] == "success"
    assert backtrace["action"] == "backtrace"
    assert len(mapping_list(dig(backtrace, "result", "frames"))) > 0

    frame = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "frame", "query": {}},
    )
    assert frame["status"] == "success"
    assert frame["action"] == "frame"
    assert "func" in mapping(dig(frame, "result", "frame"))

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
    assert isinstance(dig(variables, "result", "variables"), list)

    evaluated = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "evaluate", "query": {"expression": "a"}},
    )
    assert evaluated["status"] == "success"
    assert evaluated["action"] == "evaluate"
    assert dig(evaluated, "result", "value") == "5"


@pytest.mark.integration
def test_source_and_disassembly_queries_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """Source and disassembly queries should use the location union directly."""

    create_breakpoint(call_gdb_tool, session_id, "add")
    run(call_gdb_tool, session_id)

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
    assert len(mapping_list(dig(disassembly, "result", "instructions"))) > 0

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
    assert len(mapping_list(dig(source, "result", "lines"))) > 0
    assert any(line.get("is_current") for line in mapping_list(dig(source, "result", "lines")))


@pytest.mark.integration
def test_source_context_honours_substitute_path(
    compile_program: CompileProgram,
    start_session_payload: StartSessionResult,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
    tmp_path: Path,
) -> None:
    """Source reads must follow GDB's path rewriting, not the raw DWARF path.

    The build directory is moved after compiling, so the path recorded in the
    debug info no longer exists on disk; only ``set substitute-path`` can
    resolve it. Source context has to come from GDB, which applies that
    rewriting, instead of being read from the recorded path directly.
    """

    program = compile_default_program(compile_program, filename="substitute_program.cpp")
    build_dir = Path(program).parent
    moved_dir = tmp_path / "moved-sources"
    shutil.move(str(build_dir), str(moved_dir))
    moved_program = str(moved_dir / Path(program).name)

    started = start_session_payload(
        moved_program,
        debugger_args=["-ex", f"set substitute-path {build_dir} {moved_dir}"],
    )
    session_id = integer(dig(started, "result", "session_id"))

    try:
        create_breakpoint(call_gdb_tool, session_id, "add")
        run(call_gdb_tool, session_id)

        source = call_gdb_tool(
            "gdb_inspect_query",
            {
                "session_id": session_id,
                "action": "source",
                "query": {
                    "location": {"kind": "function", "function": "add"},
                    "context_before": 1,
                    "context_after": 1,
                },
            },
        )
    finally:
        stop_session(session_id)

    assert source["status"] == "success", source
    lines = mapping_list(dig(source, "result", "lines"))
    current = [line for line in lines if line.get("is_current")]
    assert current, lines
    assert "int add(int a, int b)" in str(current[0]["text"])


@pytest.mark.integration
def test_disassembly_fallback_without_a_option_v2(
    session_id: int, call_gdb_tool: CallGdbTool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-8.3 disassembly fallback should work against a real GDB."""

    monkeypatch.setattr(InspectionDisassembly, "_supports_disassemble_a", lambda self: False)

    create_breakpoint(call_gdb_tool, session_id, "add")
    run(call_gdb_tool, session_id)

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
    assert disassembly["status"] == "success", disassembly
    assert disassembly["action"] == "disassembly"
    assert len(mapping_list(dig(disassembly, "result", "instructions"))) > 0


@pytest.mark.integration
def test_memory_query_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """Memory inspection should remain available through gdb_inspect_query."""

    create_breakpoint(call_gdb_tool, session_id, "main")
    run(call_gdb_tool, session_id)

    memory = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "memory", "query": {"address": "&main", "count": 8}},
    )
    assert memory["status"] == "success"
    assert memory["action"] == "memory"
    assert integer(dig(memory, "result", "captured_bytes")) > 0


@pytest.mark.integration
def test_memory_fallback_without_bytes_command_v2(
    session_id: int, call_gdb_tool: CallGdbTool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-7.3 memory fallback should work against a real GDB."""

    monkeypatch.setattr(InspectionMemory, "_supports_read_memory_bytes", lambda self: False)

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
        {
            "session_id": session_id,
            "action": "memory",
            "query": {"address": "&main", "count": 8},
        },
    )
    assert memory["status"] == "success", memory
    assert memory["action"] == "memory"
    assert integer(dig(memory, "result", "captured_bytes")) > 0


@pytest.mark.integration
def test_startup_missing_program_reports_target_not_loaded_v2(
    compiled_program: str,
    start_session_payload: StartSessionResult,
    stop_session: StopSession,
) -> None:
    """A missing program should open a session but report no loaded target."""

    result = start_session_payload(f"{compiled_program}.missing", init_commands=[])
    assert result["status"] == "success"
    assert dig(result, "result", "target_loaded") is False

    if dig(result, "result", "session_id") is not None:
        stop_session(integer(dig(result, "result", "session_id")), ignore_errors=True)


@pytest.mark.integration
def test_startup_existing_program_reports_target_loaded_v2(
    compiled_program: str,
    start_session_payload: StartSessionResult,
    stop_session: StopSession,
) -> None:
    """Loading an existing executable should report the target as loaded."""

    result = start_session_payload(compiled_program, init_commands=[])
    assert result["status"] == "success"
    assert dig(result, "result", "target_loaded") is True

    if dig(result, "result", "session_id") is not None:
        stop_session(integer(dig(result, "result", "session_id")), ignore_errors=True)


@pytest.mark.integration
def test_startup_program_with_core_reports_target_loaded_v2(
    compiled_program_and_core: tuple[str, str],
    start_session_payload: StartSessionResult,
    stop_session: StopSession,
) -> None:
    """Loading an executable together with its core should report a target."""

    program, core = compiled_program_and_core
    result = start_session_payload(program, core=core, init_commands=[])
    assert result["status"] == "success"
    assert dig(result, "result", "target_loaded") is True

    if dig(result, "result", "session_id") is not None:
        stop_session(integer(dig(result, "result", "session_id")), ignore_errors=True)


@pytest.mark.integration
def test_execute_command_escape_hatch_v2(session_id: int, call_gdb_tool: CallGdbTool) -> None:
    """The escape hatch should remain a direct public tool, not a test shim."""

    result = call_gdb_tool(
        "gdb_execute_command",
        {"session_id": session_id, "command": "info functions", "timeout_sec": 30},
    )
    assert result["status"] == "success"
    assert dig(result, "result", "command") == "info functions"
    assert isinstance(dig(result, "result", "backend_data", "console_output"), str)


@pytest.mark.integration
def test_get_status_reports_exited_state_after_continue_v2(
    session_id: int, call_gdb_tool: CallGdbTool
) -> None:
    """Status should update through the v2 query envelope after continue completes."""

    create_breakpoint(call_gdb_tool, session_id, "main")
    run(call_gdb_tool, session_id)
    call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "continue", "execution": {}},
    )

    status = status_query(call_gdb_tool, session_id)
    assert status["status"] == "success"
    assert dig(status, "result", "execution_state") in {"paused", "exited"}


@pytest.mark.integration
def test_attach_process_v2(
    attachable_program: str,
    start_session_payload: StartSessionResult,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """Attach-by-PID should work without legacy wrappers or response flattening."""

    process = subprocess.Popen([attachable_program])
    session_id: int | None = None
    try:
        start = start_session_payload(attachable_program)
        session_id = integer(dig(start, "result", "session_id"))

        attach = call_gdb_tool(
            "gdb_attach_process",
            {"session_id": session_id, "pid": process.pid, "timeout_sec": 30},
        )
        assert attach["status"] == "success", attach

        status = status_query(call_gdb_tool, session_id)
        assert status["status"] == "success"
        assert dig(status, "result", "execution_state") == "paused"
    finally:
        if session_id is not None:
            stop_session(session_id, ignore_errors=True)
        terminate_process(process)
