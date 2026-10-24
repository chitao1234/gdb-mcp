"""Real-GDB integration coverage for connecting to an externally started gdb."""

from __future__ import annotations

import os
import socket
import subprocess
import threading

import pytest

from tests.support import dig, integer, mapping_list

from .conftest import (
    CallGdbTool,
    CompileProgram,
    StopSession,
    create_breakpoint,
    run,
    status_query,
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


class _MiTcpEndpoint:
    """Expose one GDB/MI child over a loopback TCP port.

    GDB only speaks MI on its own stdio, so the test bridges that stream to a
    socket the same way an external tool such as socat would.
    """

    def __init__(self, program: str) -> None:
        self._program = program
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(15)
        self._connection: socket.socket | None = None
        self._gdb: subprocess.Popen[bytes] | None = None
        self._pumps: list[threading.Thread] = []

    @property
    def endpoint(self) -> str:
        host, port = self._listener.getsockname()[:2]
        return f"tcp://{host}:{port}"

    def start(self) -> None:
        self._gdb = subprocess.Popen(
            ["gdb", "--quiet", "--interpreter=mi2", self._program],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        threading.Thread(target=self._accept, name="mi-tcp-accept", daemon=True).start()

    def _accept(self) -> None:
        connection, _ = self._listener.accept()
        self._connection = connection
        assert self._gdb is not None
        self._pumps = [
            threading.Thread(target=self._pump_gdb, name="mi-tcp-out", daemon=True),
            threading.Thread(target=self._pump_socket, name="mi-tcp-in", daemon=True),
        ]
        for pump in self._pumps:
            pump.start()

    def _pump_gdb(self) -> None:
        assert self._gdb is not None and self._gdb.stdout is not None
        connection = self._connection
        assert connection is not None
        read_fd = self._gdb.stdout.fileno()
        while True:
            chunk = os.read(read_fd, 65536)
            if not chunk:
                return
            connection.sendall(chunk)

    def _pump_socket(self) -> None:
        assert self._gdb is not None and self._gdb.stdin is not None
        connection = self._connection
        assert connection is not None
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                self._gdb.stdin.write(chunk)
                self._gdb.stdin.flush()
        except OSError:
            return

    def close(self) -> None:
        self._listener.close()
        if self._connection is not None:
            self._connection.close()
        if self._gdb is not None and self._gdb.poll() is None:
            self._gdb.terminate()
            try:
                self._gdb.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._gdb.kill()


@pytest.mark.integration
def test_connect_to_externally_started_gdb_over_tcp(
    compile_program: CompileProgram,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """gdb-mcp should attach to a gdb whose MI stream is bridged to a TCP port."""

    program = compile_program(
        _CONNECT_C_PROGRAM,
        filename="connect_tcp_target.c",
        compiler="gcc",
    )
    bridge = _MiTcpEndpoint(program)
    bridge.start()
    try:
        start = call_gdb_tool("gdb_session_connect", {"endpoint": bridge.endpoint})
        assert start["status"] == "success", start
        assert dig(start, "result", "endpoint") == bridge.endpoint
        assert dig(start, "result", "target_loaded") is True
        session_id = integer(dig(start, "result", "session_id"))

        breakpoint = create_breakpoint(call_gdb_tool, session_id, "foo")
        assert breakpoint["status"] == "success", breakpoint

        execution = run(call_gdb_tool, session_id)
        assert execution["status"] == "success", execution
        status = status_query(call_gdb_tool, session_id)
        assert dig(status, "result", "execution_state") == "paused"
        assert dig(status, "result", "stop_reason") == "breakpoint-hit"

        stop_session(session_id)
    finally:
        bridge.close()


@pytest.mark.integration
def test_connect_to_externally_started_gdb_over_pty(
    compile_program: CompileProgram,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """gdb-mcp should attach to a gdb that another process started on a pty."""

    pty = pytest.importorskip("pty", reason="pty endpoints require POSIX")
    program = compile_program(
        _CONNECT_C_PROGRAM,
        filename="connect_target.c",
        compiler="gcc",
    )

    # The external process owns the pty master; gdb-mcp opens the slave path.
    master, slave = pty.openpty()
    ttyname = getattr(os, "ttyname")  # noqa: B009 - POSIX-only attribute
    slave_name = ttyname(slave)
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
        assert dig(start, "result", "endpoint") == f"pty://{slave_name}"
        assert dig(start, "result", "target_loaded") is True
        session_id = integer(dig(start, "result", "session_id"))

        breakpoint = create_breakpoint(call_gdb_tool, session_id, "main")
        assert breakpoint["status"] == "success", breakpoint
        assert (
            mapping_list(dig(breakpoint, "result", "breakpoint", "locations"))[0]["func"] == "main"
        )

        stopped = stop_session(session_id)
        assert stopped["status"] == "success", stopped
    finally:
        terminate_process(process)
