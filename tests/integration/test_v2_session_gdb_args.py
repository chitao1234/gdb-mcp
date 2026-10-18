"""Real-GDB integration coverage for caller-supplied GDB command-line arguments."""

from __future__ import annotations

from pathlib import Path

from tests.support import integer, text

from .conftest import CallGdbTool, CompileProgram, StopSession
from .program_sources import TEST_CPP_PROGRAM


def test_gdb_args_reach_gdb_before_the_program_loads(
    compile_program: CompileProgram,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
    tmp_path: Path,
) -> None:
    """gdb_args must arrive on GDB's command line and take effect.

    This is the only channel that can configure GDB before it reads the target,
    which is what auto-load safe-path and search directories need.
    """

    program = compile_program(TEST_CPP_PROGRAM, filename="gdb_args_program.cpp", compiler="g++")
    script_dir = tmp_path / "pretty-printers"
    script_dir.mkdir()

    started = call_gdb_tool(
        "gdb_session_start",
        {
            "program": program,
            "gdb_args": [
                f"--directory={script_dir}",
                "-iex",
                "set print elements 4242",
            ],
        },
    )
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

    assert started["status"] == "success"
    assert "4242" in text(elements.get("output"))
    assert str(script_dir) in text(directories.get("output"))
