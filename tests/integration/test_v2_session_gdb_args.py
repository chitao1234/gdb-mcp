"""Real-GDB integration coverage for caller-supplied GDB command-line arguments."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import integer, text

from .conftest import CallGdbTool, CompileProgram, StartSessionResult, StopSession
from .program_sources import TEST_CPP_PROGRAM

pytestmark = pytest.mark.integration


def test_debugger_args_reach_gdb_before_the_program_loads(
    compile_program: CompileProgram,
    start_session_payload: StartSessionResult,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
    tmp_path: Path,
) -> None:
    """debugger_args must arrive on GDB's command line and take effect.

    The server inserts them before the program argument, which is the channel
    that configures GDB's own search directories and settings. Only options
    every supported GDB understands belong here.
    """

    program = compile_program(
        TEST_CPP_PROGRAM, filename="debugger_args_program.cpp", compiler="g++"
    )
    script_dir = tmp_path / "pretty-printers"
    script_dir.mkdir()

    started = start_session_payload(
        program,
        debugger_args=[
            f"--directory={script_dir}",
            "-ex",
            "set print elements 4242",
        ],
    )
    assert started["status"] == "success"
    session_id = integer(started["session_id"])

    try:
        elements = call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id, "command": "show print elements"},
        )
        directories = call_gdb_tool(
            "gdb_execute_command",
            {"session_id": session_id, "command": "show directories"},
        )
    finally:
        stop_session(session_id)

    assert "4242" in text(elements.get("output"))
    assert str(script_dir) in text(directories.get("output"))
