"""Unit tests for the per-project daemon helpers used by the CLI."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gdb_mcp.client import daemon
from gdb_mcp.client.daemon import (
    ResolvedServer,
    daemon_paths,
    daemon_status,
    project_root,
    resolve_server,
    state_dir,
    stop_daemon,
)


def test_state_dir_honors_the_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))

    assert state_dir() == tmp_path / "state"


def test_state_dir_uses_xdg_cache_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GDB_MCP_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "platform", "linux")

    assert state_dir() == tmp_path / "cache" / "gdb-mcp"


def test_state_dir_follows_platform_conventions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GDB_MCP_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    monkeypatch.setattr(sys, "platform", "darwin")
    assert state_dir() == tmp_path / "Library" / "Caches" / "gdb-mcp"

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert state_dir() == tmp_path / "AppData" / "gdb-mcp"


def test_project_root_finds_the_vcs_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert project_root(nested) == tmp_path


def test_project_root_falls_back_to_the_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert project_root(nested) == nested


def test_daemon_paths_are_stable_and_keyed_by_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))

    first = daemon_paths(tmp_path / "one")
    again = daemon_paths(tmp_path / "one")
    other = daemon_paths(tmp_path / "two")

    assert first.cookie == again.cookie
    assert first.cookie != other.cookie
    assert first.cookie.parent == tmp_path / "state" / "daemons"


def test_cookie_round_trip_is_private(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    paths = daemon_paths(tmp_path)

    daemon._write_cookie(paths, {"pid": 42, "url": "http://127.0.0.1:1/mcp"})

    assert daemon._read_cookie(paths) == {"pid": 42, "url": "http://127.0.0.1:1/mcp"}
    if os.name != "nt":
        assert (paths.cookie.stat().st_mode & 0o777) == 0o600


def test_resolve_server_returns_explicit_urls_untouched(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("GDB_MCP_AUTH_TOKEN", raising=False)

    resolved = asyncio.run(resolve_server(explicit_url="http://example.test/mcp"))

    assert resolved == ResolvedServer("http://example.test/mcp", None, False)
    assert not (tmp_path / "state").exists()


def test_resolve_server_reads_the_token_from_the_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GDB_MCP_AUTH_TOKEN", "env-secret")

    resolved = asyncio.run(resolve_server(explicit_url="http://example.test/mcp"))

    assert resolved == ResolvedServer("http://example.test/mcp", "env-secret", False)


def test_resolve_server_reuses_a_live_cookie(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    paths = daemon_paths()
    daemon._write_cookie(paths, {"pid": 1, "url": "http://127.0.0.1:9/mcp", "token": "tok"})

    with (
        patch.object(daemon, "_server_reachable", new=AsyncMock(return_value=True)),
        patch.object(daemon, "_spawn", new=AsyncMock()) as mock_spawn,
    ):
        resolved = asyncio.run(resolve_server(explicit_url=None))

    assert resolved == ResolvedServer("http://127.0.0.1:9/mcp", "tok", False, True)
    mock_spawn.assert_not_awaited()


def test_resolve_server_replaces_a_stale_cookie(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    paths = daemon_paths()
    daemon._write_cookie(paths, {"pid": 1, "url": "http://127.0.0.1:9/mcp", "token": "tok"})
    spawned = ResolvedServer("http://127.0.0.1:10/mcp", "fresh", True)

    with (
        patch.object(daemon, "_server_reachable", new=AsyncMock(return_value=False)),
        patch.object(daemon, "_spawn", new=AsyncMock(return_value=spawned)) as mock_spawn,
    ):
        resolved = asyncio.run(resolve_server(explicit_url=None))

    assert resolved == spawned
    mock_spawn.assert_awaited_once()
    assert not paths.cookie.exists()


def test_daemon_status_without_cookie(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)

    assert "no background server" in asyncio.run(daemon_status())


def test_stop_daemon_without_cookie_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(stop_daemon())

    assert result.stopped
    assert "no background server" in result.message


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        return self._response


def test_stop_daemon_reports_a_refusal_and_keeps_the_cookie(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    paths = daemon_paths()
    daemon._write_cookie(paths, {"pid": 1, "url": "http://127.0.0.1:9/mcp", "token": "tok"})
    response = _FakeResponse(409, {"message": "active sessions are still attached"})

    with patch.object(daemon.httpx, "AsyncClient", return_value=_FakeClient(response)):
        result = asyncio.run(stop_daemon())

    assert not result.stopped
    assert "active sessions" in result.message
    assert paths.cookie.exists()


def test_stop_daemon_waits_for_the_process_and_clears_the_cookie(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    try:
        paths = daemon_paths()
        daemon._write_cookie(
            paths, {"pid": process.pid, "url": "http://127.0.0.1:9/mcp", "token": "tok"}
        )

        with patch.object(
            daemon.httpx, "AsyncClient", return_value=_FakeClient(_FakeResponse(200))
        ):
            result = asyncio.run(stop_daemon())
    finally:
        process.wait(timeout=10)

    assert result.stopped
    assert not paths.cookie.exists()


def test_process_alive_detects_running_and_exited_processes() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
    try:
        assert daemon._process_alive(process.pid)
    finally:
        process.wait(timeout=10)

    assert not daemon._process_alive(process.pid)
