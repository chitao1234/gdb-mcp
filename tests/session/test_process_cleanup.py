"""Real subprocess regressions for bounded debugger termination and cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest

from gdb_mcp.domain import OperationError, OperationSuccess, SessionStartInfo
from gdb_mcp.session.factory import create_gdb_controller
from gdb_mcp.session.lldb_bridge import BridgeClient
from gdb_mcp.session.lldb_bridge_types import BridgeError, BridgeLaunch
from gdb_mcp.session.process_cleanup import cleanup_process
from gdb_mcp.session.registry import SessionRegistry


@pytest.fixture
def fast_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the production escalation sequence without ten-second tests."""

    bounded = partial(cleanup_process, timeout_sec=0.4)
    monkeypatch.setattr("gdb_mcp.session.factory.cleanup_process", bounded)
    monkeypatch.setattr("gdb_mcp.session.lldb_bridge.cleanup_process", bounded)


def _debugger(tmp_path: Path, mode: str, *, mi: bool = False) -> tuple[Path, Path]:
    if os.name == "nt":
        pytest.skip("The fake debugger uses a Unix executable script and signals")
    executable = tmp_path / "fake-debugger"
    transcript = tmp_path / "requests.log"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"log = {str(transcript)!r}\n"
        "with open(log, 'a') as output: output.write(str(os.getpid()) + '\\n')\n"
        "for line in sys.stdin:\n"
        "    with open(log, 'a') as output: output.write(line)\n"
        f"    if {mode!r} == 'crash': os._exit(23)\n"
        f"    if {mode!r} == 'hang':\n"
        "        while True: time.sleep(1)\n"
        f"    if {mi!r}: continue\n"
        "    request = json.loads(line)\n"
        f"    if {mode!r} == 'hang_shutdown' and request['op'] == 'shutdown':\n"
        "        while True: time.sleep(1)\n"
        "    print(json.dumps({'request_id': request['request_id'], 'ok': True}), flush=True)\n"
        "    if request['op'] == 'shutdown': break\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, transcript


def _kill_debugger(transcript: Path) -> None:
    if transcript.is_file():
        pid = int(transcript.read_text().splitlines()[0])
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _bounded_call(action: Callable[[], None]) -> None:
    failures: list[Exception] = []

    def run() -> None:
        try:
            action()
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive(), "Debugger cleanup exceeded its budget"
    if failures:
        raise failures[0]


@pytest.mark.parametrize("working_dir", [False, True])
def test_gdb_stop_kills_a_process_that_ignores_termination(
    tmp_path: Path, fast_cleanup: None, working_dir: bool
) -> None:
    executable, transcript = _debugger(tmp_path, "hang", mi=True)
    controller = create_gdb_controller(
        command=[str(executable), "--interpreter=mi"],
        time_to_check_for_additional_output_sec=0.01,
        cwd=str(tmp_path) if working_dir else None,
    )
    # Wait until the child has installed its signal handler before stopping it.
    deadline = time.monotonic() + 2
    while not transcript.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    process = controller.gdb_process
    assert process is not None
    try:
        _bounded_call(controller.exit)
        assert process.poll() == -signal.SIGKILL
        assert controller.gdb_process is None
        controller.exit()
    finally:
        _kill_debugger(transcript)


@pytest.mark.parametrize("mode", ["responsive", "hang_shutdown"])
def test_lldb_shutdown_is_bounded_and_preserves_the_detach_request(
    tmp_path: Path, fast_cleanup: None, mode: str
) -> None:
    executable, transcript = _debugger(tmp_path, mode)
    client = BridgeClient(BridgeLaunch(executable, None, dict(os.environ)))
    try:
        client.request("initialize", timeout_sec=2)
        _bounded_call(lambda: client.close(detach=True))
        assert client._process.poll() is not None
        assert '"detach":true' in transcript.read_text()
        if mode == "hang_shutdown":
            assert client._process.returncode == -signal.SIGKILL
        client.close()
    finally:
        _kill_debugger(transcript)


def test_lldb_cleanup_skips_shutdown_after_a_request_timeout(
    tmp_path: Path, fast_cleanup: None
) -> None:
    executable, transcript = _debugger(tmp_path, "hang")
    client = BridgeClient(BridgeLaunch(executable, None, dict(os.environ)))
    try:
        with pytest.raises(BridgeError, match="did not answer"):
            client.request("initialize", timeout_sec=0.2)
        _bounded_call(client.close)
        assert client._process.poll() == -signal.SIGKILL
        assert '"op":"shutdown"' not in transcript.read_text()
    finally:
        _kill_debugger(transcript)


def test_lldb_pipe_cleanup_is_bounded_when_a_descendant_keeps_stdout_open(
    tmp_path: Path, fast_cleanup: None
) -> None:
    executable, transcript = _debugger(tmp_path, "responsive")
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"with open({str(transcript)!r}, 'w') as output: output.write(str(child.pid))\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    print(json.dumps({'request_id': request['request_id'], 'ok': True}), flush=True)\n"
        "    if request['op'] == 'shutdown': break\n",
        encoding="utf-8",
    )
    client = BridgeClient(BridgeLaunch(executable, None, dict(os.environ)))
    try:
        client.request("initialize", timeout_sec=2)
        with pytest.raises(TimeoutError, match="cleanup did not finish"):
            _bounded_call(client.close)
        assert client._process.poll() is not None
    finally:
        _kill_debugger(transcript)
        client._reader.join(timeout=2)
        client._process.kill()
        client._process.wait(timeout=2)


def test_cleanup_returns_even_if_the_shutdown_callback_blocks() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    release = threading.Event()

    def shutdown() -> None:
        release.wait()

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="cleanup did not finish"):
            cleanup_process(process, graceful_shutdown=shutdown, timeout_sec=0.2)
        assert time.monotonic() - started < 2
        assert process.poll() is not None
    finally:
        release.set()
        process.kill()
        process.wait(timeout=2)


@pytest.mark.parametrize("debugger", ["gdb", "lldb"])
@pytest.mark.parametrize("mode", ["crash", "hang"])
def test_failed_start_returns_an_error_without_registering_a_session(
    tmp_path: Path, fast_cleanup: None, debugger: str, mode: str
) -> None:
    executable, transcript = _debugger(tmp_path, mode, mi=debugger == "gdb")
    registry = SessionRegistry()
    results: list[OperationSuccess[SessionStartInfo] | OperationError] = []
    launch = BridgeLaunch(executable, None, dict(os.environ))
    original_request = BridgeClient.request

    def fast_request(
        self: BridgeClient, operation: str, *, timeout_sec: float = 0.2, **payload: object
    ) -> dict[str, object]:
        return original_request(self, operation, timeout_sec=timeout_sec, **payload)

    def start() -> None:
        results.append(registry.start_session(debugger=debugger, debugger_path=str(executable)))

    worker = threading.Thread(target=start, daemon=True)
    try:
        with (
            patch("gdb_mcp.session.lifecycle.DEFAULT_TIMEOUT_SEC", 0.2),
            patch("gdb_mcp.session.lldb_backend.prepare_bridge", return_value=launch),
            patch.object(BridgeClient, "request", fast_request),
        ):
            worker.start()
            worker.join(timeout=3)
            assert not worker.is_alive(), "Failed startup hung during cleanup"
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, OperationError)
        assert registry.active_session_count() == 0
        if debugger == "lldb":
            assert result.code == ("command_timeout" if mode == "hang" else "not_started")
        else:
            assert "failed to initialize" in result.message.lower()
    finally:
        _kill_debugger(transcript)
        worker.join(timeout=3)
