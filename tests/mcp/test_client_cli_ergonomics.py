"""Tests for CLI invocation ergonomics: env fallback, raw payloads, exit codes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import build_parser, parse_client_args
from gdb_mcp.client.daemon import DaemonError, ResolvedServer
from gdb_mcp.contracts import ACTION_THREADS, TOOL_CONTEXT_QUERY
from tests.support import json_object

from .cli_support import SERVER_URL, invoke_cli


@pytest.mark.parametrize("failed", [False, True])
def test_json_output_keeps_unknown_and_dashed_response_fields(failed: bool) -> None:
    payload: dict[str, object] = {
        "status": "error" if failed else "success",
        "action": ACTION_THREADS,
        "future-field": {"nested-key": "kept"},
        "future_field": "distinct",
    }
    if failed:
        payload.update(code="future_error", message="unfamiliar debugger failure")
    else:
        payload["result"] = {
            "threads": [{"id": "1", "target-id": "native-thread", "future-key": "kept"}]
        }

    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            "--json",
            TOOL_CONTEXT_QUERY,
            "--payload-json",
            '{"session_id":7,"action":"threads"}',
        ],
        response=payload,
        is_error=failed,
    )

    assert result.exit_code == int(failed)
    assert result.stderr == ""
    assert json_object(result.stdout) == payload


def test_server_url_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GDB_MCP_SERVER_URL`` supplies the URL when no flag is given."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    args = parse_client_args(
        ["status"],
        parser=build_parser(),
    )

    assert args.server_url == SERVER_URL


def test_explicit_server_url_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ``--server-url`` beats the environment fallback."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", "http://elsewhere.invalid/mcp")

    args = parse_client_args(
        ["--server-url", SERVER_URL, "status"],
        parser=build_parser(),
    )

    assert args.server_url == SERVER_URL


def test_missing_server_url_is_resolved_from_the_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--server-url`` the CLI asks the daemon for a resolved endpoint."""

    monkeypatch.delenv("GDB_MCP_SERVER_URL", raising=False)
    resolved = ResolvedServer(
        url="http://127.0.0.1:41234/mcp",
        token="secret-token",
        spawned=True,
        managed=False,
    )

    with patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.return_value = resolved
        result = invoke_cli(["status"], response={"status": "success", "result": {}})

    assert result.exit_code == 0
    mock_resolve.assert_awaited_once_with(explicit_url=None)
    result.assert_invoked(
        "gdb_session_query",
        {"action": "list"},
        auth_token="secret-token",
        url=resolved.url,
    )


def test_daemon_failure_is_reported_with_exit_code_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon startup failure exits nonzero and never reaches the transport."""

    monkeypatch.delenv("GDB_MCP_SERVER_URL", raising=False)

    with patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.side_effect = DaemonError("background server did not become ready")
        result = invoke_cli(["status"])

    assert result.exit_code == 1
    assert "background server did not become ready" in result.stderr
    result.invoke.assert_not_awaited()


def test_payload_json_sends_the_raw_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--payload-json`` forwards the parsed object unchanged."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    result = invoke_cli(
        [
            "gdb_breakpoint_manage",
            "--payload-json",
            '{"session_id": 7, "action": "disable", "breakpoint": {"number": 3}}',
        ],
        response={"status": "success", "result": {}},
    )

    assert result.exit_code == 0
    result.assert_invoked(
        "gdb_breakpoint_manage",
        {"session_id": 7, "action": "disable", "breakpoint": {"number": 3}},
    )


def test_payload_json_rejects_field_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixing a raw payload with field flags is an argparse error."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    result = invoke_cli(
        [
            "gdb_session_query",
            "--action",
            "list",
            "--payload-json",
            '{"action": "list"}',
        ],
        expect_exit=True,
    )

    assert result.exit_code == 2
    assert "cannot be combined" in result.stderr
    result.invoke.assert_not_awaited()


def test_payload_json_is_validated_against_the_tool_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw payload that violates the tool schema is rejected before transport."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    result = invoke_cli(
        [
            "gdb_breakpoint_manage",
            "--payload-json",
            '{"session_id": 7, "action": "explode"}',
        ],
        expect_exit=True,
    )

    assert result.exit_code == 2
    assert result.stderr.strip()
    result.invoke.assert_not_awaited()


def test_batch_step_failures_set_a_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch that reports failures should make the CLI exit with status one."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    result = invoke_cli(
        [
            "gdb_workflow_batch",
            "--payload-json",
            '{"session_id": 7, "steps": [{"tool": "gdb_capture_bundle"}]}',
        ],
        response={
            "status": "success",
            "result": {
                "requested_steps": 2,
                "steps": [{"result": {"status": "error", "code": "error", "message": "failed"}}],
            },
        },
    )

    assert result.exit_code == 1


def test_subcommand_help_lists_validated_payload_examples(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--help`` documents the validated ``--payload-json`` form."""

    result = invoke_cli(["tool", "gdb_breakpoint_manage", "--help"], expect_exit=True)

    assert result.exit_code == 0
    output = capsys.readouterr().out
    assert "--payload-json" in output
    assert '"kind": "code"' in output


def test_version_flag_prints_the_client_version(capsys: pytest.CaptureFixture[str]) -> None:
    """``--version`` reports the client name without running a command."""

    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--version"])

    assert exc_info.value.code == 0
    assert "gdb-mcp-client" in capsys.readouterr().out


def test_session_id_is_accepted_before_the_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A global ``--session-id`` should be folded into the built payload."""

    monkeypatch.setenv("GDB_MCP_SERVER_URL", SERVER_URL)

    result = invoke_cli(
        ["--session-id", "4", "exec", "run"],
        response={"status": "success", "result": {}},
    )

    assert result.exit_code == 0
    result.assert_invoked(
        "gdb_execute_command",
        {"session_id": 4, "command": "run", "timeout_sec": 30},
    )


def test_session_id_before_the_subcommand_is_parsed() -> None:
    """The parser keeps a leading ``--session-id`` on the namespace."""

    args = parse_client_args(
        ["--session-id", "4", "exec", "run"],
        parser=build_parser(),
    )

    assert args.session_id == 4
