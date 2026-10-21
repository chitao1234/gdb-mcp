"""Native jump/until behavior through the public MCP surface on both debuggers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from gdb_mcp.contracts import (
    ACTION_JUMP,
    ACTION_UNTIL,
    DEBUGGER_NAMES,
    TOOL_EXECUTION_MANAGE,
    TOOL_INSPECT_QUERY,
    DebuggerName,
)
from tests.support import dig, mapping, mapping_list, text

from .conftest import (
    CallGdbTool,
    CompileProgram,
    StartSession,
    StopSession,
    create_breakpoint,
    run,
)

pytestmark = [pytest.mark.integration, pytest.mark.parametrize("debugger", DEBUGGER_NAMES)]

_PROGRAM = """#include <unistd.h>
volatile int total = 0;
__attribute__((noinline)) int replacement(int early) {
    total += 2000; /* replacement_target */
    return total; /* replacement_stop */
}
__attribute__((noinline)) int worker(int early) {
    total += 1; /* entry */
    if (early == 1) return total; /* early_return */
    while (early == 2) usleep(10000); /* wait_loop */
    if (early == 3) worker(0); /* recursive_call */
    total += 10; /* target */
    total += 100; /* after */
    return total;
} /* worker_end */
int main(int argc, char **argv) {
    int result = worker(0);
    total += 1000; /* caller */
    return result == 0;
}
"""


def _line(marker: str) -> int:
    return next(
        index for index, line in enumerate(_PROGRAM.splitlines(), 1) if f"/* {marker} */" in line
    )


@pytest.fixture
def location_session(
    debugger: DebuggerName,
    compile_program: CompileProgram,
    start_session: StartSession,
    stop_session: StopSession,
) -> Iterator[tuple[int, str]]:
    program = compile_program(
        _PROGRAM,
        filename="execution locations.c",
        compiler="gcc",
        compiler_args=["-g", "-gdwarf-2", "-O0"],
    )
    kwargs: dict[str, object] = {"debugger": debugger}
    if debugger == "lldb" and os.environ.get("LLDB_PREFIX"):
        kwargs["debugger_path"] = os.environ["LLDB_PREFIX"]
    session_id = start_session(program, init_commands=[], **kwargs)
    try:
        yield session_id, str(Path(program).with_suffix(".c"))
    finally:
        stop_session(session_id, ignore_errors=True)


def _execute(
    call: CallGdbTool, session_id: int, action: str, location: str, **wait: object
) -> dict[str, object]:
    return call(
        TOOL_EXECUTION_MANAGE,
        {
            "session_id": session_id,
            "action": action,
            "execution": {"location": location, "timeout_sec": 5, **wait},
        },
    )


def _total(call: CallGdbTool, session_id: int) -> int:
    result = call(
        TOOL_INSPECT_QUERY,
        {"session_id": session_id, "action": "evaluate", "query": {"expression": "total"}},
    )
    assert result["status"] == "success", result
    return int(text(dig(result, "result", "value")))


def _set_mode(call: CallGdbTool, session_id: int, mode: int) -> None:
    result = call(
        TOOL_INSPECT_QUERY,
        {
            "session_id": session_id,
            "action": "evaluate",
            "query": {"expression": f"early = {mode}"},
        },
    )
    assert result["status"] == "success", result


@pytest.mark.parametrize("kind", ["line", "file_line", "address", "address_expression"])
def test_until_runs_to_the_destination(
    debugger: DebuggerName, location_session: tuple[int, str], call_gdb_tool: CallGdbTool, kind: str
) -> None:
    session_id, file = location_session
    create_breakpoint(call_gdb_tool, session_id, "worker")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    location = str(_line("target"))
    if kind == "file_line":
        location = f"{file}:{_line('target')}"
    if kind in {"address", "address_expression"}:
        breakpoint = create_breakpoint(call_gdb_tool, session_id, f"{file}:{_line('target')}")
        record = mapping(dig(breakpoint, "result", "breakpoint"))
        address = mapping_list(record["locations"])[0]["addr"]
        call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "delete",
                "breakpoint": {"number": int(text(record["number"]))},
            },
        )
        location = f"*{address} + 0" if kind == "address_expression" else f"*{address}"
    result = _execute(call_gdb_tool, session_id, ACTION_UNTIL, location)

    assert result["status"] == "success", result
    assert dig(result, "result", "execution_state") == "paused"
    assert dig(result, "result", "stop_kind") == "step"
    assert dig(result, "result", "last_stop_event", "frame", "line") in {
        _line("target"),
        str(_line("target")),
    }
    assert _total(call_gdb_tool, session_id) == 1


def test_until_stops_at_the_starting_frames_return_boundary(
    debugger: DebuggerName, location_session: tuple[int, str], call_gdb_tool: CallGdbTool
) -> None:
    session_id, _ = location_session
    create_breakpoint(call_gdb_tool, session_id, "worker")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    _set_mode(call_gdb_tool, session_id, 1)
    result = _execute(call_gdb_tool, session_id, ACTION_UNTIL, str(_line("target")))

    assert result["status"] == "success", result
    frame = mapping(dig(result, "result", "last_stop_event", "frame"))
    assert frame["func"] == "main" or (
        frame["func"] == "worker" and int(str(frame["line"])) == _line("worker_end")
    )
    assert _total(call_gdb_tool, session_id) == 1


@pytest.mark.parametrize("location_form", ["file_line", "function"])
def test_until_preserves_the_debuggers_cross_function_behavior(
    debugger: DebuggerName,
    location_session: tuple[int, str],
    call_gdb_tool: CallGdbTool,
    location_form: str,
) -> None:
    session_id, file = location_session
    create_breakpoint(call_gdb_tool, session_id, "worker")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    result = _execute(
        call_gdb_tool,
        session_id,
        ACTION_UNTIL,
        f"{file}:{_line('replacement_target')}" if location_form == "file_line" else "replacement",
    )

    if debugger == "gdb":
        assert result["status"] == "success", result
        assert dig(result, "result", "last_stop_event", "frame", "func") == "main"
    else:
        assert result["status"] == "error", result
        assert any(
            fragment in text(result["message"]).lower()
            for fragment in ("current function", "until target")
        )


def test_until_reports_an_intervening_breakpoint(
    debugger: DebuggerName, location_session: tuple[int, str], call_gdb_tool: CallGdbTool
) -> None:
    session_id, file = location_session
    create_breakpoint(call_gdb_tool, session_id, "worker")
    create_breakpoint(call_gdb_tool, session_id, f"{file}:{_line('early_return')}")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    result = _execute(call_gdb_tool, session_id, ACTION_UNTIL, str(_line("target")))

    assert result["status"] == "success", result
    assert dig(result, "result", "stop_kind") == "breakpoint"
    assert _total(call_gdb_tool, session_id) == 1


def test_until_ignores_the_same_destination_in_a_recursive_call(
    debugger: DebuggerName, location_session: tuple[int, str], call_gdb_tool: CallGdbTool
) -> None:
    session_id, _ = location_session
    breakpoint = create_breakpoint(call_gdb_tool, session_id, "worker")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    _set_mode(call_gdb_tool, session_id, 3)
    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "delete",
            "breakpoint": {"number": int(text(dig(breakpoint, "result", "breakpoint", "number")))},
        },
    )
    result = _execute(call_gdb_tool, session_id, ACTION_UNTIL, str(_line("target")))

    assert result["status"] == "success", result
    assert dig(result, "result", "stop_kind") == "step"
    assert _total(call_gdb_tool, session_id) == 112


@pytest.mark.parametrize("cross_function", [False, True])
def test_jump_skips_code_and_resumes_without_a_function_restriction(
    debugger: DebuggerName,
    location_session: tuple[int, str],
    call_gdb_tool: CallGdbTool,
    cross_function: bool,
) -> None:
    session_id, file = location_session
    target = "replacement_target" if cross_function else "target"
    stop = "replacement_stop" if cross_function else "after"
    create_breakpoint(call_gdb_tool, session_id, "worker")
    create_breakpoint(call_gdb_tool, session_id, f"{file}:{_line(stop)}")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    result = _execute(
        call_gdb_tool,
        session_id,
        ACTION_JUMP,
        f"{file}:{_line(target)}",
    )

    assert result["status"] == "success", result
    assert dig(result, "result", "stop_kind") == "breakpoint"
    assert _total(call_gdb_tool, session_id) == (2000 if cross_function else 10)


@pytest.mark.parametrize("wait_until", ["acknowledged", "stop"])
def test_until_running_and_timeout_results_do_not_reuse_the_previous_stop(
    debugger: DebuggerName,
    location_session: tuple[int, str],
    call_gdb_tool: CallGdbTool,
    wait_until: str,
) -> None:
    session_id, _ = location_session
    create_breakpoint(call_gdb_tool, session_id, "worker")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    _set_mode(call_gdb_tool, session_id, 2)
    result = _execute(
        call_gdb_tool,
        session_id,
        ACTION_UNTIL,
        str(_line("target")),
        wait_until=wait_until,
        timeout_sec=1,
    )

    if wait_until == "acknowledged":
        assert result["status"] == "success", result
        assert dig(result, "result", "execution_state") == "running"
        assert dig(result, "result", "last_stop_event") is None
        assert dig(result, "result", "stop_reason") is None
    else:
        assert result["status"] == "error", result
        assert result["code"] == "command_timeout"
        assert dig(result, "details", "execution_state") == "running"


def test_jump_accepts_a_function_location(
    debugger: DebuggerName, location_session: tuple[int, str], call_gdb_tool: CallGdbTool
) -> None:
    session_id, file = location_session
    create_breakpoint(call_gdb_tool, session_id, "worker")
    create_breakpoint(call_gdb_tool, session_id, f"{file}:{_line('replacement_stop')}")
    assert run(call_gdb_tool, session_id)["status"] == "success"
    result = _execute(call_gdb_tool, session_id, ACTION_JUMP, "replacement")

    assert result["status"] == "success", result
    assert dig(result, "result", "location") == "replacement"
    assert _total(call_gdb_tool, session_id) == 2000
