"""Tests for the `session use` / `session current` meta commands."""

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gdb_mcp.client import daemon
from gdb_mcp.client.cli import main
from gdb_mcp.client.daemon import ResolvedServer
from gdb_mcp.client.runtime import ClientToolResponse

SERVER_URL = "http://127.0.0.1:41000/mcp"


def _managed(monkeypatch, tmp_path: Path) -> ResolvedServer:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return ResolvedServer(SERVER_URL, "tok", False, True)


def _call(argv: list[str], resolved: ResolvedServer, *, response: ClientToolResponse):
    stdout = StringIO()
    stderr = StringIO()
    with (
        patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as resolve,
        patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock) as invoke,
    ):
        resolve.return_value = resolved
        invoke.return_value = response
        exit_code = asyncio.run(main(argv, stdout=stdout, stderr=stderr))
    return exit_code, invoke, stdout.getvalue(), stderr.getvalue()


def test_session_current_without_a_default(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, _invoke, _stdout, stderr = _call(
        ["session", "current"],
        resolved,
        response=ClientToolResponse(payload={"status": "success"}, is_error=False),
    )

    assert exit_code == 1
    assert "no default session" in stderr


def test_session_current_reports_the_stored_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, _invoke, stdout, _stderr = _call(
        ["session", "current"],
        resolved,
        response=ClientToolResponse(payload={"status": "success"}, is_error=False),
    )

    assert exit_code == 0
    assert stdout == "3\n"


def test_session_use_validates_and_stores(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, invoke, stdout, _stderr = _call(
        ["session", "use", "3"],
        resolved,
        response=ClientToolResponse(payload={"status": "success"}, is_error=False),
    )

    assert exit_code == 0
    assert "default session: 3" in stdout
    assert daemon.remembered_session_id() == 3
    assert invoke.await_args.args[1] == "gdb_session_query"
    assert invoke.await_args.args[2] == {"action": "status", "session_id": 3}


def test_session_use_rejects_an_unknown_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, _invoke, _stdout, stderr = _call(
        ["session", "use", "9"],
        resolved,
        response=ClientToolResponse(
            payload={"status": "error", "message": "session 9 not found"},
            is_error=True,
        ),
    )

    assert exit_code == 1
    assert "session 9 not found" in stderr
    assert daemon.remembered_session_id() is None


def test_session_use_requires_the_background_server(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    resolved = ResolvedServer(SERVER_URL, None, False, False)

    exit_code, invoke, _stdout, stderr = _call(
        ["session", "use", "3", "--server-url", SERVER_URL],
        resolved,
        response=ClientToolResponse(payload={"status": "success"}, is_error=False),
    )

    assert exit_code == 1
    assert "background server" in stderr
    invoke.assert_not_awaited()


def test_status_marks_the_default_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, _invoke, stdout, _stderr = _call(
        ["status"],
        resolved,
        response=ClientToolResponse(
            payload={
                "status": "success",
                "action": "list",
                "result": {"sessions": [{"session_id": 3}], "count": 1},
            },
            is_error=False,
        ),
    )

    assert exit_code == 0
    assert "default session: 3\n" in stdout
    assert "(not running)" not in stdout


def test_status_marks_a_session_that_is_gone(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, _invoke, stdout, _stderr = _call(
        ["status"],
        resolved,
        response=ClientToolResponse(
            payload={"status": "success", "action": "list", "result": {"sessions": [], "count": 0}},
            is_error=False,
        ),
    )

    assert exit_code == 0
    assert "default session: 3 (not running)" in stdout


def test_stopping_the_default_session_forgets_it(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, _invoke, _stdout, _stderr = _call(
        ["stop"],
        resolved,
        response=ClientToolResponse(
            payload={"status": "success", "action": "stop", "result": {"message": "stopped"}},
            is_error=False,
        ),
    )

    assert exit_code == 0
    assert daemon.remembered_session_id() is None


def test_stopping_another_session_keeps_the_default(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, _invoke, _stdout, _stderr = _call(
        ["stop", "--session-id", "7"],
        resolved,
        response=ClientToolResponse(
            payload={"status": "success", "action": "stop", "result": {"message": "stopped"}},
            is_error=False,
        ),
    )

    assert exit_code == 0
    assert daemon.remembered_session_id() == 3
