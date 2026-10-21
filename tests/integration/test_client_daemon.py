"""Integration test for the auto-spawned per-project daemon."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from io import StringIO
from pathlib import Path
from typing import Protocol

import pytest

from gdb_mcp.client.cli import main
from gdb_mcp.client.daemon import _process_alive, daemon_paths, stop_daemon
from tests.support import integer, json_object

DAEMON_LIFECYCLE_IDLE_SEC = "120"
DAEMON_IDLE_EXIT_SEC = "2"
DAEMON_STOP_TIMEOUT_SEC = 10
DAEMON_IDLE_TIMEOUT_SEC = 30


class DaemonEnvironment(Protocol):
    """Configure the daemon for one test and guarantee it is stopped."""

    def __call__(self, *, idle_sec: str) -> AbstractContextManager[None]: ...


def _cookie(root: Path) -> dict[str, object]:
    return json_object(daemon_paths(root).cookie.read_text(encoding="utf-8"))


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def daemon_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DaemonEnvironment:
    """Run the daemon against ``tmp_path`` and stop it after the test."""

    @contextmanager
    def environment(*, idle_sec: str) -> Iterator[None]:
        monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("GDB_MCP_DAEMON_IDLE_SEC", idle_sec)
        monkeypatch.chdir(tmp_path)
        try:
            yield
        finally:
            asyncio.run(stop_daemon(force=True))

    return environment


@pytest.mark.integration
def test_cli_daemon_lifecycle(daemon_environment: DaemonEnvironment, tmp_path: Path) -> None:
    """The CLI reuses one spawned daemon until it is explicitly stopped."""

    with daemon_environment(idle_sec=DAEMON_LIFECYCLE_IDLE_SEC):
        stdout = StringIO()
        assert asyncio.run(main(["status"], stdout=stdout)) == 0
        cookie = _cookie(tmp_path)
        assert str(cookie["url"]).startswith("http://127.0.0.1:")
        assert cookie["token"]

        assert asyncio.run(main(["status"], stdout=stdout)) == 0
        assert _cookie(tmp_path)["pid"] == cookie["pid"]

        assert asyncio.run(main(["status"], stdout=stdout)) == 0

        status = StringIO()
        assert asyncio.run(main(["daemon", "status"], stdout=status)) == 0
        assert "running" in status.getvalue()

        assert asyncio.run(main(["daemon", "stop"], stdout=StringIO())) == 0
        assert _wait_for_exit(integer(cookie["pid"]), DAEMON_STOP_TIMEOUT_SEC)
        assert not daemon_paths(tmp_path).cookie.exists()


@pytest.mark.integration
def test_daemon_exits_when_idle(daemon_environment: DaemonEnvironment, tmp_path: Path) -> None:
    """An idle daemon shuts itself down after the configured timeout."""

    with daemon_environment(idle_sec=DAEMON_IDLE_EXIT_SEC):
        assert asyncio.run(main(["status"], stdout=StringIO())) == 0
        pid = integer(_cookie(tmp_path)["pid"])

        assert _wait_for_exit(pid, DAEMON_IDLE_TIMEOUT_SEC)
