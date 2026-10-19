"""Tests for the `session use` / `session current` meta commands."""

from __future__ import annotations

from gdb_mcp.client import daemon
from gdb_mcp.client.daemon import ResolvedServer

from .cli_support import SERVER_URL, invoke_cli


def test_session_current_without_a_default(managed_server: ResolvedServer) -> None:
    """``session current`` should fail when no default session is stored."""

    result = invoke_cli(["session", "current"], resolved=managed_server)

    assert result.exit_code == 1
    assert "no default session" in result.stderr


def test_session_current_reports_the_stored_session(managed_server: ResolvedServer) -> None:
    """``session current`` should print the stored default session id."""

    daemon.remember_session_id(3)

    result = invoke_cli(["session", "current"], resolved=managed_server)

    assert result.exit_code == 0
    assert result.stdout == "3\n"


def test_session_use_validates_and_stores(managed_server: ResolvedServer) -> None:
    """``session use`` should validate against the server then store the id."""

    result = invoke_cli(["session", "use", "3"], resolved=managed_server)

    assert result.exit_code == 0
    assert "default session: 3" in result.stdout
    assert daemon.remembered_session_id() == 3
    assert result.sent_tool == "gdb_session_query"
    assert result.sent_payload == {"action": "status", "session_id": 3}


def test_session_use_rejects_an_unknown_session(managed_server: ResolvedServer) -> None:
    """A server error should leave the default session unset."""

    result = invoke_cli(
        ["session", "use", "9"],
        resolved=managed_server,
        response={"status": "error", "message": "session 9 not found"},
        is_error=True,
    )

    assert result.exit_code == 1
    assert "session 9 not found" in result.stderr
    assert daemon.remembered_session_id() is None


def test_session_use_requires_the_background_server() -> None:
    """``session use`` needs a managed background server to validate against."""

    unmanaged = ResolvedServer(url=SERVER_URL, token=None, spawned=False, managed=False)

    result = invoke_cli(
        ["session", "use", "3", "--server-url", SERVER_URL],
        resolved=unmanaged,
    )

    assert result.exit_code == 1
    assert "background server" in result.stderr
    result.invoke.assert_not_awaited()


def test_status_marks_the_default_session(managed_server: ResolvedServer) -> None:
    """``status`` should annotate the stored default session in its output."""

    daemon.remember_session_id(3)

    result = invoke_cli(
        ["status"],
        resolved=managed_server,
        response={
            "status": "success",
            "action": "list",
            "result": {"sessions": [{"session_id": 3}], "count": 1},
        },
    )

    assert result.exit_code == 0
    assert "default session: 3\n" in result.stdout
    assert "(not running)" not in result.stdout


def test_status_marks_a_session_that_is_gone(managed_server: ResolvedServer) -> None:
    """A stored default missing from the list should be marked not running."""

    daemon.remember_session_id(3)

    result = invoke_cli(
        ["status"],
        resolved=managed_server,
        response={"status": "success", "action": "list", "result": {"sessions": [], "count": 0}},
    )

    assert result.exit_code == 0
    assert "default session: 3 (not running)" in result.stdout


def test_stopping_the_default_session_forgets_it(managed_server: ResolvedServer) -> None:
    """Stopping the default session should clear the remembered id."""

    daemon.remember_session_id(3)

    result = invoke_cli(
        ["stop"],
        resolved=managed_server,
        response={"status": "success", "action": "stop", "result": {"message": "stopped"}},
    )

    assert result.exit_code == 0
    assert daemon.remembered_session_id() is None


def test_stopping_another_session_keeps_the_default(managed_server: ResolvedServer) -> None:
    """Stopping a different session should keep the default id."""

    daemon.remember_session_id(3)

    result = invoke_cli(
        ["stop", "--session-id", "7"],
        resolved=managed_server,
        response={"status": "success", "action": "stop", "result": {"message": "stopped"}},
    )

    assert result.exit_code == 0
    assert daemon.remembered_session_id() == 3
