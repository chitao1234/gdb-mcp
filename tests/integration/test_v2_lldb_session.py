"""Integration tests for the LLDB backend against a real ``lldb-dap``."""

from __future__ import annotations

import shutil

import pytest

from tests.integration.conftest import (
    CallGdbTool,
    CompileProgram,
    StartSession,
    StopSession,
    status_query,
)
from tests.support import dig, mapping_list, text

LLDB_PROGRAM = """
#include <stdio.h>

int describe(int value) {
    int doubled = value * 2;
    printf("value=%d doubled=%d\\n", value, doubled);
    return doubled;
}

int main(void) {
    int total = describe(21);
    return total == 42 ? 0 : 1;
}
"""


def _lldb_dap() -> str | None:
    """Return whichever lldb-dap this environment has."""

    for candidate in (
        "lldb-dap",
        "lldb-dap-20",
        "lldb-dap-19",
        "lldb-dap-18",
        "lldb-dap-17",
        "lldb-dap-16",
        "lldb-dap-15",
        "lldb-dap-14",
        "lldb-vscode-14",
    ):
        found = shutil.which(candidate)
        if found:
            return found
    return None


LLDB_DAP = _lldb_dap()

pytestmark = pytest.mark.skipif(LLDB_DAP is None, reason="lldb-dap is not installed")


@pytest.mark.integration
def test_lldb_stops_at_a_function_breakpoint(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """A lldb session should debug a real program over DAP end to end."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        created = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "describe"},
            },
        )
        assert created["status"] == "success", created
        assert dig(created, "result", "breakpoint", "verified") is True

        run = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "run"},
        )
        assert run["status"] == "success", run

        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "execution_state") == "paused"
        assert dig(status, "result", "stop_reason") == "breakpoint"
        assert dig(status, "result", "stop_kind") == "breakpoint"

        backtrace = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id, "action": "backtrace", "query": {}},
        )
        frames = mapping_list(dig(backtrace, "result", "frames"))
        assert dig(frames[0], "func") == "describe"

        variables = call_gdb_tool(
            "gdb_inspect_query",
            {"session_id": session_id, "action": "variables", "query": {}},
        )
        names = {
            dig(variable, "name")
            for variable in mapping_list(dig(variables, "result", "variables"))
        }
        assert "value" in names or "doubled" in names or "value" in names

        evaluated = call_gdb_tool(
            "gdb_inspect_query",
            {"session_id": session_id, "action": "evaluate", "query": {"expression": "value * 3"}},
        )
        assert dig(evaluated, "result", "value") == str(63)

        command = call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id, "command": "image list"},
        )
        assert command["status"] == "success"
        assert "lldb_probe" in text(command["output"])

        taken = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id, "action": "list", "query": {}},
        )
        assert dig(taken, "result", "count") == 1
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_lldb_rejects_a_core_dump(
    compile_program: CompileProgram,
    start_session: StartSession,
    stop_session: StopSession,
    call_gdb_tool: CallGdbTool,
) -> None:
    """A gdb-only launch mode should be refused before any adapter is spawned."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    started = call_gdb_tool(
        "gdb_session_start",
        {
            "program": program,
            "debugger": "lldb",
            "gdb_path": LLDB_DAP,
            "core": "/tmp/does-not-exist",
        },
    )

    assert started["status"] == "error"
    assert started["code"] == "unsupported_operation"
    assert "core dump" in text(started["message"])


@pytest.mark.integration
def test_lldb_parses_memory_disassembly_source_and_watchpoints(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """The command fallbacks should return structured payloads, not raw text."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "describe"},
            },
        )
        run = call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run"})
        assert run["status"] == "success", run

        memory = call_gdb_tool(
            "gdb_inspect_query",
            {
                "session_id": session_id,
                "action": "memory",
                "query": {"address": "&value", "count": 4},
            },
        )
        assert memory["status"] == "success", memory
        assert dig(memory, "result", "captured_bytes") == 4
        block = mapping_list(dig(memory, "result", "blocks"))[0]
        assert len(text(block["contents"])) == 8
        assert text(block["begin"]).startswith("0x")

        disassembly = call_gdb_tool(
            "gdb_inspect_query",
            {
                "session_id": session_id,
                "action": "disassembly",
                "query": {
                    "location": {"kind": "function", "function": "describe"},
                    "instruction_count": 16,
                },
            },
        )
        assert disassembly["status"] == "success", disassembly
        instructions = mapping_list(dig(disassembly, "result", "instructions"))
        assert instructions
        assert dig(instructions[0], "address") is not None
        assert dig(instructions[0], "instruction")
        # Mixed mode interleaves source lines: the stop's own instruction is marked
        # current and carries the line the debugger is stopped on.
        current = [instruction for instruction in instructions if instruction.get("is_current")]
        assert len(current) == 1
        assert dig(current[0], "line") is not None

        source = call_gdb_tool(
            "gdb_inspect_query",
            {
                "session_id": session_id,
                "action": "source",
                "query": {
                    "location": {"kind": "function", "function": "describe"},
                    "context_before": 2,
                    "context_after": 2,
                },
            },
        )
        assert source["status"] == "success", source
        lines = mapping_list(dig(source, "result", "lines"))
        assert lines
        assert any("describe" in text(line["text"]) for line in lines)

        watchpoint = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "watch", "expression": "doubled"},
            },
        )
        assert watchpoint["status"] == "success", watchpoint
        assert dig(watchpoint, "result", "breakpoint", "type") == "watchpoint"

        resumed = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "continue"},
        )
        assert resumed["status"] == "success", resumed
        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "stop_kind") == "watchpoint"

        catchpoint = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "catch", "event": "syscall"},
            },
        )
        assert catchpoint["status"] == "error"
        assert catchpoint["code"] == "unsupported_operation"
    finally:
        stop_session(session_id, ignore_errors=True)


THROW_PROGRAM = """
#include <stdexcept>
#include <cstdio>

int risky(int value) {
    if (value > 0) {
        throw std::runtime_error("boom");
    }
    return value;
}

int main() {
    try {
        risky(1);
    } catch (const std::exception &error) {
        printf("caught: %s\\n", error.what());
    }
    return 0;
}
"""


@pytest.mark.integration
def test_lldb_read_watchpoint_stops_on_a_read(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """A read watchpoint is expressible: `watchpoint set variable -w read`."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "describe"},
            },
        )
        call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run"})

        watchpoint = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "watch", "expression": "doubled", "access": "read"},
            },
        )
        assert watchpoint["status"] == "success", watchpoint
        assert dig(watchpoint, "result", "breakpoint", "type") == "watchpoint"

        resumed = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "continue"},
        )
        assert resumed["status"] == "success", resumed
        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "stop_kind") == "watchpoint"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_lldb_address_breakpoint(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """`*<address expression>` goes through LLDB's own address breakpoint."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        created = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "*describe"},
            },
        )
        assert created["status"] == "success", created
        assert dig(created, "result", "breakpoint", "addr") is not None

        run = call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run"})
        assert run["status"] == "success", run
        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "execution_state") == "paused"
        assert dig(status, "result", "stop_reason") == "breakpoint"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_lldb_throw_catchpoint_reports_an_exception_stop(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """A C++ throw maps to LLDB's exception breakpoint and a canonical exception stop."""

    program = compile_program(THROW_PROGRAM, filename="lldb_throw.cpp", compiler="g++")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        created = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "catch", "event": "throw"},
            },
        )
        assert created["status"] == "success", created
        assert dig(created, "result", "breakpoint", "type") == "catchpoint"

        run = call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run"})
        assert run["status"] == "success", run
        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "execution_state") == "paused"
        assert dig(status, "result", "stop_kind") == "exception"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_lldb_follow_fork_mode_and_detach_on_fork(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """LLDB has follow-fork-mode but no detach-on-fork setting."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        followed = call_gdb_tool(
            "gdb_inferior_manage",
            {
                "session_id": session_id,
                "action": "set_follow_fork_mode",
                "inferior": {"mode": "child"},
            },
        )
        assert followed["status"] == "success", followed
        assert dig(followed, "result", "mode") == "child"

        detached = call_gdb_tool(
            "gdb_inferior_manage",
            {
                "session_id": session_id,
                "action": "set_detach_on_fork",
                "inferior": {"enabled": False},
            },
        )
        assert detached["status"] == "error"
        assert detached["code"] == "unsupported_operation"
        assert "detach-on-fork" in text(detached["message"])
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_lldb_temporary_breakpoint_is_one_shot(
    compile_program: CompileProgram,
    start_session: StartSession,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """A temporary breakpoint stops once and leaves the tracked inventory."""

    program = compile_program(LLDB_PROGRAM, filename="lldb_probe.c", compiler="gcc")
    session_id = start_session(program, debugger="lldb", gdb_path=LLDB_DAP)
    try:
        created = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "describe", "temporary": True},
            },
        )
        assert created["status"] == "success", created
        assert dig(created, "result", "breakpoint", "temporary") is True

        run = call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run"})
        assert run["status"] == "success", run
        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "execution_state") == "paused"

        listed = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id, "action": "list", "query": {}},
        )
        assert dig(listed, "result", "count") == 0
    finally:
        stop_session(session_id, ignore_errors=True)
