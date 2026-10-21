"""Unit tests for the per-project daemon helpers used by the CLI."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typing_extensions import Self

from gdb_mcp.client import daemon
from gdb_mcp.client.daemon import (
    ResolvedServer,
    daemon_paths,
    daemon_status,
    project_root,
    resolve_server,
    stop_daemon,
)


@pytest.fixture
def state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Root the daemon at an isolated state directory inside ``tmp_path``."""

    state_path = tmp_path / "state"
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(state_path))
    monkeypatch.chdir(tmp_path)
    return state_path


def test_state_dir_honors_the_override(state_dir: Path) -> None:
    """``GDB_MCP_STATE_DIR`` overrides every platform convention."""

    assert daemon.state_dir() == state_dir


def test_state_dir_uses_xdg_cache_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state_dir: Path
) -> None:
    """On Linux the XDG cache home is preferred when no override is set."""

    monkeypatch.delenv("GDB_MCP_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "platform", "linux")

    assert daemon.state_dir() == tmp_path / "cache" / "gdb-mcp"


def test_state_dir_follows_platform_conventions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state_dir: Path
) -> None:
    """macOS and Windows use their native cache directories."""

    monkeypatch.delenv("GDB_MCP_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _: tmp_path))

    monkeypatch.setattr(sys, "platform", "darwin")
    assert daemon.state_dir() == tmp_path / "Library" / "Caches" / "gdb-mcp"

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert daemon.state_dir() == tmp_path / "AppData" / "gdb-mcp"


def test_project_root_finds_the_vcs_root(tmp_path: Path) -> None:
    """A nested directory resolves to the nearest VCS root."""

    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert project_root(nested) == tmp_path


def test_project_root_falls_back_to_the_directory(tmp_path: Path) -> None:
    """Without a VCS marker the starting directory is returned."""

    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert project_root(nested) == nested


def test_daemon_paths_are_stable_and_keyed_by_root(state_dir: Path, tmp_path: Path) -> None:
    """Cookie paths are deterministic per root and isolated between roots."""

    first = daemon_paths(tmp_path / "one")
    again = daemon_paths(tmp_path / "one")
    other = daemon_paths(tmp_path / "two")

    assert first.cookie == again.cookie
    assert first.cookie != other.cookie
    assert first.cookie.parent == state_dir / "daemons"


def test_cookie_round_trip_is_private(state_dir: Path, tmp_path: Path) -> None:
    """Cookies round-trip and are readable only by their owner on POSIX."""

    paths = daemon_paths(tmp_path)

    daemon._write_cookie(paths, {"pid": 42, "url": "http://127.0.0.1:1/mcp"})

    assert daemon._read_cookie(paths) == {"pid": 42, "url": "http://127.0.0.1:1/mcp"}
    if os.name != "nt":
        assert (paths.cookie.stat().st_mode & 0o777) == 0o600


def test_resolve_server_returns_explicit_urls_untouched(
    monkeypatch: pytest.MonkeyPatch, state_dir: Path
) -> None:
    """An explicit URL bypasses daemon discovery and state creation."""

    monkeypatch.delenv("GDB_MCP_AUTH_TOKEN", raising=False)

    resolved = asyncio.run(resolve_server(explicit_url="http://example.test/mcp"))

    assert resolved == ResolvedServer("http://example.test/mcp", None, False)
    assert not state_dir.exists()


def test_resolve_server_reads_the_token_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, state_dir: Path
) -> None:
    """An explicit URL still picks up the environment auth token."""

    monkeypatch.setenv("GDB_MCP_AUTH_TOKEN", "env-secret")

    resolved = asyncio.run(resolve_server(explicit_url="http://example.test/mcp"))

    assert resolved == ResolvedServer("http://example.test/mcp", "env-secret", False)


def test_resolve_server_reuses_a_live_cookie(state_dir: Path) -> None:
    """A reachable cookie endpoint is reused without spawning a daemon."""

    paths = daemon_paths()
    daemon._write_cookie(paths, {"pid": 1, "url": "http://127.0.0.1:9/mcp", "token": "tok"})

    with (
        patch.object(daemon, "_server_reachable", new=AsyncMock(return_value=True)),
        patch.object(daemon, "_spawn", new=AsyncMock()) as mock_spawn,
    ):
        resolved = asyncio.run(resolve_server(explicit_url=None))

    assert resolved == ResolvedServer("http://127.0.0.1:9/mcp", "tok", False, True)
    mock_spawn.assert_not_awaited()


def test_resolve_server_replaces_a_stale_cookie(state_dir: Path) -> None:
    """An unreachable cookie is discarded and replaced by a spawned daemon."""

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


def test_daemon_status_without_cookie(state_dir: Path) -> None:
    """With no cookie the status command reports that nothing is running."""

    assert "no background server" in asyncio.run(daemon_status())


def test_stop_daemon_without_cookie_is_idempotent(state_dir: Path) -> None:
    """Stopping when no daemon exists succeeds without side effects."""

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

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        del args, kwargs
        return self._response


def test_stop_daemon_reports_a_refusal_and_keeps_the_cookie(state_dir: Path) -> None:
    """A daemon with attached sessions refuses to stop and keeps its cookie."""

    paths = daemon_paths()
    daemon._write_cookie(paths, {"pid": 1, "url": "http://127.0.0.1:9/mcp", "token": "tok"})
    response = _FakeResponse(409, {"message": "active sessions are still attached"})

    with patch.object(httpx, "AsyncClient", return_value=_FakeClient(response)):
        result = asyncio.run(stop_daemon())

    assert not result.stopped
    assert "active sessions" in result.message
    assert paths.cookie.exists()


def test_stop_daemon_waits_for_the_process_and_clears_the_cookie(state_dir: Path) -> None:
    """A successful stop waits for the process and removes the cookie."""

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    try:
        paths = daemon_paths()
        daemon._write_cookie(
            paths, {"pid": process.pid, "url": "http://127.0.0.1:9/mcp", "token": "tok"}
        )

        with patch.object(httpx, "AsyncClient", return_value=_FakeClient(_FakeResponse(200))):
            result = asyncio.run(stop_daemon())
    finally:
        process.wait(timeout=10)

    assert result.stopped
    assert not paths.cookie.exists()


def test_wait_for_ready_reads_the_ready_file(state_dir: Path, tmp_path: Path) -> None:
    """The ready file supplies the host, port, and path once written."""

    paths = daemon_paths(tmp_path)
    paths.ready.parent.mkdir(parents=True, exist_ok=True)
    paths.ready.write_text(
        json.dumps({"host": "127.0.0.1", "port": 4242, "path": "/mcp"}), encoding="utf-8"
    )

    class _LiveProcess:
        def poll(self) -> None:
            return None

    # `_wait_for_ready` only calls `poll()`, so this stand-in is safe to treat
    # as the Popen it stands in for.
    ready = asyncio.run(
        daemon._wait_for_ready(paths, cast("subprocess.Popen[bytes]", _LiveProcess()))
    )

    assert ready["port"] == 4242


def test_async_exclusive_lock_serializes_acquisitions(state_dir: Path, tmp_path: Path) -> None:
    """Two lock acquisitions cannot interleave their critical sections."""

    if os.name == "nt":  # pragma: no cover - flock semantics differ on Windows
        return
    paths = daemon_paths(tmp_path)
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with daemon._async_exclusive_lock(paths.lock):
            order.append(f"{tag}-enter")
            await asyncio.sleep(0.05)
            order.append(f"{tag}-exit")

    async def run() -> None:
        await asyncio.gather(worker("a"), worker("b"))

    asyncio.run(run())

    assert order in (
        ["a-enter", "a-exit", "b-enter", "b-exit"],
        ["b-enter", "b-exit", "a-enter", "a-exit"],
    )


def test_process_alive_detects_running_and_exited_processes() -> None:
    """A child process counts as alive until it has actually exited."""

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
    try:
        assert daemon._process_alive(process.pid)
    finally:
        process.wait(timeout=10)

    assert not daemon._process_alive(process.pid)
