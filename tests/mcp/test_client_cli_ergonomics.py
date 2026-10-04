"""Tests for CLI invocation ergonomics: env fallback, raw payloads, exit codes."""

from __future__ import annotations

import asyncio
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import build_parser, main, parse_client_args
from gdb_mcp.client.daemon import DaemonError, ResolvedServer
from gdb_mcp.client.runtime import ClientToolResponse

SERVER_URL = "http://127.0.0.1:8000/mcp"


def test_server_url_falls_back_to_environment(monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    args = parse_client_args(
        ["status"],
        parser=build_parser(),
    )

    assert args.server_url == SERVER_URL


def test_explicit_server_url_wins_over_environment(monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_SERVER_URL", "http://elsewhere.invalid/mcp")

    args = parse_client_args(
        ["--server-url", SERVER_URL, "status"],
        parser=build_parser(),
    )

    assert args.server_url == SERVER_URL


@patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
def test_missing_server_url_is_resolved_from_the_daemon(mock_invoke_tool, monkeypatch) -> None:
    monkeypatch.delenv("GDB_MCP_SERVER_URL", raising=False)
    mock_invoke_tool.return_value = ClientToolResponse(
        payload={"status": "success"}, is_error=False
    )
    daemon = ResolvedServer("http://127.0.0.1:41234/mcp", "secret-token", True)

    with patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.return_value = daemon
        exit_code = asyncio.run(main(["status"], stdout=StringIO()))

    assert exit_code == 0
    mock_resolve.assert_awaited_once_with(explicit_url=None)
    mock_invoke_tool.assert_awaited_once_with(
        daemon.url,
        "gdb_session_query",
        {"action": "list"},
        http_client=None,
        auth_token="secret-token",
    )


@patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
def test_daemon_failure_is_reported_with_exit_code_one(mock_invoke_tool, monkeypatch) -> None:
    monkeypatch.delenv("GDB_MCP_SERVER_URL", raising=False)
    stderr = StringIO()

    with patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.side_effect = DaemonError("background server did not become ready")
        exit_code = asyncio.run(main(["status"], stderr=stderr))

    assert exit_code == 1
    assert "background server did not become ready" in stderr.getvalue()
    mock_invoke_tool.assert_not_awaited()


@patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
def test_payload_json_sends_the_raw_payload(mock_invoke_tool, monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)
    mock_invoke_tool.return_value = ClientToolResponse(
        payload={"status": "success"}, is_error=False
    )

    exit_code = asyncio.run(
        main(
            [
                "gdb_breakpoint_manage",
                "--payload-json",
                '{"session_id": 7, "action": "disable", "breakpoint": {"number": 3}}',
            ],
            stdout=StringIO(),
        )
    )

    assert exit_code == 0
    mock_invoke_tool.assert_awaited_once_with(
        SERVER_URL,
        "gdb_breakpoint_manage",
        {"session_id": 7, "action": "disable", "breakpoint": {"number": 3}},
        http_client=None,
        auth_token=None,
    )


@patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
def test_payload_json_rejects_field_flags(mock_invoke_tool, monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)
    stderr = StringIO()

    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(
            main(
                [
                    "gdb_session_query",
                    "--action",
                    "list",
                    "--payload-json",
                    '{"action": "list"}',
                ],
                stderr=stderr,
            )
        )

    assert exc_info.value.code == 2
    assert "cannot be combined" in stderr.getvalue()
    mock_invoke_tool.assert_not_awaited()


@patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
def test_payload_json_is_validated_against_the_tool_schema(mock_invoke_tool, monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)
    stderr = StringIO()

    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(
            main(
                [
                    "gdb_breakpoint_manage",
                    "--payload-json",
                    '{"session_id": 7, "action": "explode"}',
                ],
                stderr=stderr,
            )
        )

    assert exc_info.value.code == 2
    assert stderr.getvalue().strip()
    mock_invoke_tool.assert_not_awaited()


@patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
def test_batch_step_failures_set_a_nonzero_exit_code(mock_invoke_tool, monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)
    mock_invoke_tool.return_value = ClientToolResponse(
        payload={"status": "success", "count": 2, "error_count": 1},
        is_error=False,
    )

    exit_code = asyncio.run(
        main(
            [
                "gdb_workflow_batch",
                "--payload-json",
                '{"session_id": 7, "steps": [{"tool": "gdb_capture_bundle"}]}',
            ],
            stdout=StringIO(),
        )
    )

    assert exit_code == 1


def test_subcommand_help_lists_validated_payload_examples(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(main(["tool", "gdb_breakpoint_manage", "--help"]))

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--payload-json" in output
    assert '"kind": "code"' in output
