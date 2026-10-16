"""Real-GDB integration coverage for connecting to an externally started gdb."""

from __future__ import annotations

import os
import pty
import signal
import subprocess

import pytest

_CONNECT_C_PROGRAM = """
#include <stdio.h>

int foo(int x) { return x + 1; }

int main(void) {
    printf("%d\\n", foo(1));
    return 0;
}
"""


@pytest.mark.integration
def test_connect_to_externally_started_gdb_over_pty(compile_program, call_gdb_tool):
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
        session_id = start["session_id"]

        breakpoint = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main"},
            },
        )
        assert breakpoint["status"] == "success", breakpoint
        assert breakpoint["result"]["breakpoint"]["func"] == "main"

        stopped = call_gdb_tool(
            "gdb_session_manage",
            {"session_id": session_id, "action": "stop", "session": {}},
        )
        assert stopped["status"] == "success", stopped

        # Stopping gdb-mcp's session must only detach, not kill the external gdb.
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
