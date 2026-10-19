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


_STARTUP_TIMEOUT_SEC = 30
# The process must exit on SIGINT, so a generous bound still proves the point:
# a hung server never exits, it just fails later. CI runners share cores with
# the rest of the suite, so a tight bound reports load as a hang.
_SHUTDOWN_TIMEOUT_SEC = 60
_KILL_TIMEOUT_SEC = 30


def _kill_process(process: subprocess.Popen[str]) -> None:
    """Kill the server if it is still running and reap it."""

    if process.poll() is None:
        process.kill()
        process.wait(timeout=_KILL_TIMEOUT_SEC)


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

        ready, _, _ = select.select([process.stdout], [], [], _STARTUP_TIMEOUT_SEC)
        assert ready, "server did not answer the initialize request"
        response = json.loads(process.stdout.readline())
        assert "result" in response

        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=_SHUTDOWN_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate(timeout=_KILL_TIMEOUT_SEC)
            pytest.fail(
                f"server did not exit within {_SHUTDOWN_TIMEOUT_SEC}s of SIGINT; "
                f"stderr:\n{stderr}"
            )

        assert process.returncode == 130
    finally:
        _kill_process(process)
