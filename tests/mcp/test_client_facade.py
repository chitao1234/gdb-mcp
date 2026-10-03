"""Tests for the facade command surface and session defaultization."""

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client import daemon
from gdb_mcp.client.cli import build_parser, main
from gdb_mcp.client.daemon import ResolvedServer
from gdb_mcp.client.runtime import ClientToolResponse

SERVER_URL = "http://127.0.0.1:41000/mcp"


def _managed(monkeypatch, tmp_path: Path) -> ResolvedServer:
    """Point the CLI at a managed background server rooted at tmp_path."""

    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return ResolvedServer(SERVER_URL, "tok", False, True)


def _call(argv: list[str], resolved: ResolvedServer, *, response: dict[str, object] | None = None):
    """Run main() with a mocked transport, returning exit code, call, stdout, stderr."""

    stdout = StringIO()
    stderr = StringIO()
    with (
        patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as resolve,
        patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock) as invoke,
    ):
        resolve.return_value = resolved
        invoke.return_value = ClientToolResponse(
            payload=response if response is not None else {"status": "success"},
            is_error=False,
        )
        exit_code = asyncio.run(main(argv, stdout=stdout, stderr=stderr))
    return exit_code, invoke, stdout.getvalue(), stderr.getvalue()


def _failed_call(argv: list[str], resolved: ResolvedServer):
    """Run main() expecting an argparse-level failure."""

    stdout = StringIO()
    stderr = StringIO()
    calls: list[object] = []
    with (
        patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as resolve,
        patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock) as invoke,
    ):
        resolve.return_value = resolved
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(main(argv, stdout=stdout, stderr=stderr))
        calls.append(invoke)
    return exc_info.value.code, stderr.getvalue(), invoke


def test_facade_run_uses_the_remembered_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(5)

    exit_code, invoke, _stdout, _stderr = _call(["run"], resolved)

    assert exit_code == 0
    call = invoke.await_args
    assert call.args[0] == SERVER_URL
    assert call.args[1] == "gdb_execution_manage"
    assert call.args[2]["action"] == "run"
    assert call.args[2]["session_id"] == 5


def test_facade_start_takes_a_positional_program(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, invoke, _stdout, _stderr = _call(["start", "/bin/true"], resolved)

    assert exit_code == 0
    assert invoke.await_args.args[2] == {"program": "/bin/true"}
    assert daemon.remembered_session_id() is None


def test_facade_start_remembers_the_new_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, _invoke, _stdout, _stderr = _call(
        ["start", "/bin/true"],
        resolved,
        response={"status": "success", "session_id": 9},
    )

    assert exit_code == 0
    assert daemon.remembered_session_id() == 9


def test_facade_break_add_sets_the_code_kind(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, invoke, _stdout, _stderr = _call(["break", "add", "main"], resolved)

    assert exit_code == 0
    payload = invoke.await_args.args[2]
    assert payload["action"] == "create"
    assert payload["session_id"] == 3
    assert payload["breakpoint"]["kind"] == "code"
    assert payload["breakpoint"]["location"] == "main"


def test_facade_watch_and_catch_fix_the_kind(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    _code, watch_invoke, _stdout, _stderr = _call(["watch", "value"], resolved)
    watch_payload = watch_invoke.await_args.args[2]
    _code, catch_invoke, _stdout, _stderr = _call(["catch", "throw"], resolved)
    catch_payload = catch_invoke.await_args.args[2]

    assert watch_payload["breakpoint"]["kind"] == "watch"
    assert watch_payload["breakpoint"]["expression"] == "value"
    assert catch_payload["breakpoint"]["kind"] == "catch"
    assert catch_payload["breakpoint"]["event"] == "throw"


def test_facade_break_rm_uses_the_positional_number(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(3)

    exit_code, invoke, _stdout, _stderr = _call(["break", "rm", "4"], resolved)

    assert exit_code == 0
    payload = invoke.await_args.args[2]
    assert payload["action"] == "delete"
    assert payload["breakpoint"] == {"number": 4}


def test_facade_status_needs_no_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, invoke, _stdout, _stderr = _call(["status"], resolved)

    assert exit_code == 0
    assert invoke.await_args.args[2] == {"action": "list"}


def test_facade_rejects_two_sources_for_one_field(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, stderr, _invoke = _failed_call(
        ["start", "/bin/true", "--program", "/bin/false"],
        resolved,
    )

    assert exit_code == 2
    assert "not both" in stderr


def test_facade_reports_a_missing_session(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, stderr, _invoke = _failed_call(["bt"], resolved)

    assert exit_code == 2
    assert "no debug session is recorded" in stderr


def test_tool_fallback_accepts_short_names(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, invoke, _stdout, _stderr = _call(
        ["tool", "session_query", "--action", "list"], resolved
    )

    assert exit_code == 0
    assert invoke.await_args.args[1] == "gdb_session_query"
    assert invoke.await_args.args[2] == {"action": "list"}


def test_payload_json_accepts_the_tool_prefix(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, invoke, _stdout, _stderr = _call(
        ["tool", "session_query", "--payload-json", '{"action": "list"}'],
        resolved,
    )

    assert exit_code == 0
    assert invoke.await_args.args[1] == "gdb_session_query"
    assert invoke.await_args.args[2] == {"action": "list"}


def test_unknown_tool_is_rejected(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)

    exit_code, stderr, _invoke = _failed_call(["tool", "nonsense"], resolved)

    assert exit_code == 2
    assert "nonsense" in stderr


def test_facade_exec_takes_a_positional_command(monkeypatch, tmp_path: Path) -> None:
    resolved = _managed(monkeypatch, tmp_path)
    daemon.remember_session_id(2)

    exit_code, invoke, _stdout, _stderr = _call(["exec", "info files"], resolved)

    assert exit_code == 0
    payload = invoke.await_args.args[2]
    assert payload["command"] == "info files"
    assert payload["session_id"] == 2


def test_help_lists_facade_commands_and_the_tool_fallback() -> None:
    help_text = build_parser().format_help()

    assert "break" in help_text
    assert "status" in help_text
    assert "daemon" in help_text
    assert "Call an MCP tool by name" in help_text
