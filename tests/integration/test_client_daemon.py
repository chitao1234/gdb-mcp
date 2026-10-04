"""Integration test for the auto-spawned per-project daemon."""

from __future__ import annotations

import asyncio
import json
import time
from io import StringIO
from pathlib import Path

import pytest

from gdb_mcp.client.cli import main
from gdb_mcp.client.daemon import _process_alive, daemon_paths, stop_daemon


def _cookie(root: Path) -> dict[str, object]:
    return json.loads(daemon_paths(root).cookie.read_text(encoding="utf-8"))


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.integration
def test_cli_daemon_lifecycle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GDB_MCP_DAEMON_IDLE_SEC", "120")
    monkeypatch.chdir(tmp_path)

    try:
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
        assert _wait_for_exit(int(cookie["pid"]), 10)
        assert not daemon_paths(tmp_path).cookie.exists()
    finally:
        asyncio.run(stop_daemon(force=True))


@pytest.mark.integration
def test_daemon_exits_when_idle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GDB_MCP_DAEMON_IDLE_SEC", "2")
    monkeypatch.chdir(tmp_path)

    try:
        assert asyncio.run(main(["status"], stdout=StringIO())) == 0
        pid = int(_cookie(tmp_path)["pid"])

        assert _wait_for_exit(pid, 30)
    finally:
        asyncio.run(stop_daemon(force=True))
