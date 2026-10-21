"""Tests for the facade command surface and session defaultization."""

from __future__ import annotations

from gdb_mcp.client import daemon
from gdb_mcp.client.cli import build_parser
from gdb_mcp.client.daemon import ResolvedServer
from tests.support import mapping

from .cli_support import SERVER_URL, invoke_cli


def test_facade_run_uses_the_remembered_session(managed_server: ResolvedServer) -> None:
    """``run`` should send the stored default session to gdb_execution_manage."""

    daemon.remember_session_id(5)

    result = invoke_cli(["run"], resolved=managed_server)

    assert result.exit_code == 0
    assert result.sent_url == SERVER_URL
    assert result.sent_tool == "gdb_execution_manage"
    assert result.sent_payload["action"] == "run"
    assert result.sent_payload["session_id"] == 5


def test_facade_start_takes_a_positional_program(managed_server: ResolvedServer) -> None:
    """A positional program should be forwarded as the sole start payload."""

    result = invoke_cli(["start", "/bin/true"], resolved=managed_server)

    assert result.exit_code == 0
    assert result.sent_payload == {"program": "/bin/true"}
    assert daemon.remembered_session_id() is None


def test_facade_start_remembers_the_new_session(managed_server: ResolvedServer) -> None:
    """A successful start should record the returned session id as the default."""

    result = invoke_cli(
        ["start", "/bin/true"],
        resolved=managed_server,
        response={"status": "success", "result": {"session_id": 9}},
    )

    assert result.exit_code == 0
    assert daemon.remembered_session_id() == 9


def test_facade_break_add_sets_the_code_kind(managed_server: ResolvedServer) -> None:
    """``break add`` should build a code-kind breakpoint at the given location."""

    daemon.remember_session_id(3)

    result = invoke_cli(["break", "add", "main"], resolved=managed_server)

    assert result.exit_code == 0
    payload = result.sent_payload
    assert payload["action"] == "create"
    assert payload["session_id"] == 3
    breakpoint = mapping(payload["breakpoint"])
    assert breakpoint["kind"] == "code"
    assert breakpoint["location"] == "main"


def test_facade_watch_and_catch_fix_the_kind(managed_server: ResolvedServer) -> None:
    """``watch`` and ``catch`` should pin the watch/catch kinds and expressions."""

    daemon.remember_session_id(3)

    watch = invoke_cli(["watch", "value"], resolved=managed_server)
    catch = invoke_cli(["catch", "throw"], resolved=managed_server)

    watch_breakpoint = mapping(watch.sent_payload["breakpoint"])
    catch_breakpoint = mapping(catch.sent_payload["breakpoint"])
    assert watch_breakpoint["kind"] == "watch"
    assert watch_breakpoint["expression"] == "value"
    assert catch_breakpoint["kind"] == "catch"
    assert catch_breakpoint["event"] == "throw"


def test_facade_break_rm_uses_the_positional_number(managed_server: ResolvedServer) -> None:
    """``break rm`` should map the positional number to a delete action."""

    daemon.remember_session_id(3)

    result = invoke_cli(["break", "rm", "4"], resolved=managed_server)

    assert result.exit_code == 0
    payload = result.sent_payload
    assert payload["action"] == "delete"
    assert payload["breakpoint"] == {"number": 4}


def test_facade_status_needs_no_session(managed_server: ResolvedServer) -> None:
    """``status`` should issue a session list without a session id."""

    result = invoke_cli(["status"], resolved=managed_server)

    assert result.exit_code == 0
    assert result.sent_payload == {"action": "list"}


def test_facade_rejects_two_sources_for_one_field(managed_server: ResolvedServer) -> None:
    """Supplying one field both positionally and by flag should be rejected."""

    result = invoke_cli(
        ["start", "/bin/true", "--program", "/bin/false"],
        resolved=managed_server,
        expect_exit=True,
    )

    assert result.exit_code == 2
    assert "not both" in result.stderr


def test_facade_reports_a_missing_session(managed_server: ResolvedServer) -> None:
    """A session-scoped command without a stored session should error."""

    result = invoke_cli(["bt"], resolved=managed_server, expect_exit=True)

    assert result.exit_code == 2
    assert "no debug session is recorded" in result.stderr


def test_tool_fallback_accepts_short_names(managed_server: ResolvedServer) -> None:
    """The ``tool`` fallback should expand short names to their full tool ids."""

    result = invoke_cli(["tool", "session_query", "--action", "list"], resolved=managed_server)

    assert result.exit_code == 0
    assert result.sent_tool == "gdb_session_query"
    assert result.sent_payload == {"action": "list"}


def test_payload_json_accepts_the_tool_prefix(managed_server: ResolvedServer) -> None:
    """``--payload-json`` should forward the raw object under the tool prefix."""

    result = invoke_cli(
        ["tool", "session_query", "--payload-json", '{"action": "list"}'],
        resolved=managed_server,
    )

    assert result.exit_code == 0
    assert result.sent_tool == "gdb_session_query"
    assert result.sent_payload == {"action": "list"}


def test_unknown_tool_is_rejected(managed_server: ResolvedServer) -> None:
    """An unknown tool name should be rejected before any dispatch."""

    result = invoke_cli(["tool", "nonsense"], resolved=managed_server, expect_exit=True)

    assert result.exit_code == 2
    assert "nonsense" in result.stderr


def test_facade_exec_takes_a_positional_command(managed_server: ResolvedServer) -> None:
    """``exec`` should forward the command text with the stored session id."""

    daemon.remember_session_id(2)

    result = invoke_cli(["exec", "info files"], resolved=managed_server)

    assert result.exit_code == 0
    payload = result.sent_payload
    assert payload["command"] == "info files"
    assert payload["session_id"] == 2


def test_help_lists_facade_commands_and_the_tool_fallback() -> None:
    """Help output should advertise the facade commands and the tool fallback."""

    help_text = build_parser().format_help()

    assert "break" in help_text
    assert "status" in help_text
    assert "daemon" in help_text
    assert "Call an MCP tool by name" in help_text
