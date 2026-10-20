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
                    "instruction_count": 4,
                },
            },
        )
        assert disassembly["status"] == "success", disassembly
        instructions = mapping_list(dig(disassembly, "result", "instructions"))
        assert instructions
        assert dig(instructions[0], "address") is not None
        assert dig(instructions[0], "instruction")

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
