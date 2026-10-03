"""Regression test: the stdio server must exit on SIGINT after startup."""

from __future__ import annotations

import json
import select
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

_INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "shutdown-probe", "version": "0"},
    },
}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_stdio_server_exits_on_sigint_after_startup() -> None:
    """A fully started server must exit on the first SIGINT.

    Before the daemon-stdin reader was introduced, SIGINT cancelled the server
    task but shutdown waited for the SDK's worker thread blocked in read(0),
    so the process never exited.
    """

    process = subprocess.Popen(
        [sys.executable, "-m", "gdb_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(_INITIALIZE_REQUEST) + "\n")
        process.stdin.flush()

        ready, _, _ = select.select([process.stdout], [], [], 30)
        assert ready, "server did not answer the initialize request"
        response = json.loads(process.stdout.readline())
        assert "result" in response

        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)

        assert process.returncode == 130
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
