"""Real-GDB integration coverage for connecting to an externally started gdb."""

from __future__ import annotations

import os
import pty
import subprocess

import pytest

from tests.support import dig, integer

from .conftest import (
    CallGdbTool,
    CompileProgram,
    StopSession,
    create_breakpoint,
    terminate_process,
)

_CONNECT_C_PROGRAM = """
#include <stdio.h>

int foo(int x) { return x + 1; }

int main(void) {
    printf("%d\\n", foo(1));
    return 0;
}
"""


@pytest.mark.integration
def test_connect_to_externally_started_gdb_over_pty(
    compile_program: CompileProgram,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """gdb-mcp should attach to a gdb that another process started on a pty."""

    program = compile_program(
        _CONNECT_C_PROGRAM,
        filename="connect_target.c",
        compiler="gcc",
    )

    # The external process owns the pty master; gdb-mcp opens the slave path.
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    process = subprocess.Popen(
        ["gdb", "--quiet", "--interpreter=mi2", program],
        stdin=master,
        stdout=master,
        stderr=master,
        close_fds=True,
    )
    os.close(master)
    os.close(slave)

    try:
        start = call_gdb_tool(
            "gdb_session_connect",
            {"endpoint": f"pty://{slave_name}"},
        )
        assert start["status"] == "success", start
        assert start["endpoint"] == f"pty://{slave_name}"
        assert start["target_loaded"] is True
        session_id = integer(start["session_id"])

        breakpoint = create_breakpoint(call_gdb_tool, session_id, "main")
        assert breakpoint["status"] == "success", breakpoint
        assert dig(breakpoint, "result", "breakpoint", "func") == "main"

        stopped = stop_session(session_id)
        assert stopped["status"] == "success", stopped

        # Stopping gdb-mcp's session must only detach, not kill the external gdb.
        assert process.poll() is None
    finally:
        terminate_process(process)
