"""Unit tests for the MCP CLI client command surface."""

from __future__ import annotations

import builtins

import pytest

import gdb_mcp.client.specs as client_specs
from gdb_mcp.client.cli import build_parser, parse_client_args
from gdb_mcp.client.parsers import serialize_model
from gdb_mcp.client.renderers import render_action_payload
from gdb_mcp.client.specs import CLIENT_TOOL_SPECS
from tests.support import json_object

from .cli_support import SERVER_URL, invoke_cli

SESSION_ID = 7


class TestClientCli:
    def test_render_action_payload_preserves_top_level_fields_on_key_collision(self) -> None:
        """Preserve top-level fields when a nested result reuses their names."""

        rendered = render_action_payload(
            {
                "status": "success",
                "action": "run",
                "result": {
                    "status": "nested-status",
                    "action": "nested-action",
                    "execution_state": "paused",
                },
            }
        )

        assert rendered == (
            "status: success\n"
            "action: run\n"
            "result:\n"
            "  status: nested-status\n"
            "  action: nested-action\n"
            "  execution_state: paused"
        )

    def test_parse_client_args_allows_missing_server_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Allow omitting the server URL when no environment override is set."""

        monkeypatch.delenv("GDB_MCP_SERVER_URL", raising=False)

        args = parse_client_args(["start", "--program", "/bin/true"])

        assert args.server_url is None

    def test_build_parser_formats_help_with_percent_descriptions(self) -> None:
        """Escape percent signs in top-level help descriptions."""

        help_text = build_parser().format_help()

        assert "Call an MCP tool by name" in help_text

    def test_build_parser_exposes_the_facade_commands(self) -> None:
        """Expose the facade commands in the parser help."""

        help_text = build_parser().format_help()

        assert "start" in help_text
        assert "status" in help_text
        assert "break" in help_text

    def test_subcommand_help_preserves_percent_description(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Escape percent signs in subcommand help descriptions."""

        with pytest.raises(SystemExit) as exc_info:
            build_parser().parse_args(["tool", "gdb_call_function", "--help"])

        assert exc_info.value.code == 0
        help_text = capsys.readouterr().out
        assert "x=%d\\n" in help_text
        assert "x=%%d\\n" not in help_text

    def test_main_invokes_session_start_with_flat_flags(self) -> None:
        """Forward start flags as a flat session-start payload."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "start",
                "--program",
                "/bin/true",
                "--arg=--mode",
                "--arg",
                "fast",
                "--env",
                "TERM=dumb",
                "--init-command",
                "set pagination off",
            ],
            response={
                "status": "success",
                "result": {
                    "session_id": SESSION_ID,
                    "message": "GDB session started successfully",
                    "target_loaded": True,
                    "execution_state": "not_started",
                },
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_session_start",
            {
                "program": "/bin/true",
                "args": ["--mode", "fast"],
                "init_commands": ["set pagination off"],
                "env": {"TERM": "dumb"},
            },
        )
        rendered = result.stdout
        assert "session_id: 7" in rendered
        assert "target_loaded: True" in rendered

    def test_main_connects_to_external_endpoint(self) -> None:
        """Forward connect endpoint and init commands."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "connect",
                "tcp://127.0.0.1:4399",
                "--init-command",
                "set pagination off",
            ],
            response={
                "status": "success",
                "result": {
                    "session_id": 3,
                    "message": "GDB session started",
                    "target_loaded": False,
                    "execution_state": "not_started",
                },
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_session_connect",
            {
                "endpoint": "tcp://127.0.0.1:4399",
                "init_commands": ["set pagination off"],
            },
        )

    def test_main_json_mode_prints_raw_payload(self) -> None:
        """Print the raw JSON payload in JSON mode."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "--json",
                "attach",
                "--session-id",
                str(SESSION_ID),
                "--pid",
                "1234",
            ],
            response={"status": "success", "result": {"message": "attached"}},
        )

        assert result.exit_code == 0
        assert json_object(result.stdout) == {
            "status": "success",
            "result": {"message": "attached"},
        }

    def test_main_accepts_global_flags_after_subcommand(self) -> None:
        """Accept global flags after the subcommand."""

        result = invoke_cli(
            [
                "attach",
                "--session-id",
                str(SESSION_ID),
                "--pid",
                "1234",
                "--json",
                "--server-url",
                SERVER_URL,
            ],
            response={"status": "success", "result": {"message": "attached"}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_attach_process",
            {"session_id": SESSION_ID, "pid": 1234, "timeout_sec": 30},
        )
        assert json_object(result.stdout) == {
            "status": "success",
            "result": {"message": "attached"},
        }

    def test_main_reports_schema_validation_as_cli_error(self) -> None:
        """Report schema validation failures as a CLI error."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "exec",
                "--session-id",
                str(SESSION_ID),
                "--command",
                "info breakpoints",
                "--timeout-sec",
                "0",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "timeout_sec" in result.stderr

    def test_main_invokes_execute_command_payload(self) -> None:
        """Forward exec flags to the execute-command tool."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "exec",
                "--session-id",
                str(SESSION_ID),
                "--command",
                "info breakpoints",
                "--timeout-sec",
                "9",
            ],
            response={"status": "success", "result": {"message": "ok"}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_execute_command",
            {"session_id": SESSION_ID, "command": "info breakpoints", "timeout_sec": 9},
        )

    def test_main_invokes_call_function_payload(self) -> None:
        """Forward call flags to the call-function tool."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "call",
                "--session-id",
                str(SESSION_ID),
                "--function-call",
                'printf("hello\\n")',
            ],
            response={"status": "success", "result": {"message": "called"}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_call_function",
            {
                "session_id": SESSION_ID,
                "function_call": 'printf("hello\\n")',
                "timeout_sec": 30,
            },
        )

    def test_main_invokes_capture_bundle_payload(self) -> None:
        """Forward capture flags to the capture-bundle tool."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "capture",
                "--session-id",
                str(SESSION_ID),
                "--output-dir",
                "/tmp/out",
                "--bundle-name",
                "capture",
                "--expression",
                "errno",
                "--memory-range",
                "0x401000:64",
                "--max-frames",
                "12",
                "--no-include-registers",
            ],
            response={"status": "success", "result": {"artifact_count": 3}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_capture_bundle",
            {
                "session_id": SESSION_ID,
                "output_dir": "/tmp/out",
                "bundle_name": "capture",
                "expressions": ["errno"],
                "memory_ranges": ["0x401000:64"],
                "max_frames": 12,
                "include_threads": True,
                "include_backtraces": True,
                "include_frame": True,
                "include_variables": True,
                "include_registers": False,
                "include_transcript": True,
                "include_stop_history": True,
            },
        )

    def test_main_reports_runtime_failures_without_traceback(self) -> None:
        """Report transport failures without a traceback."""

        exception_group_type = getattr(builtins, "ExceptionGroup", None)
        if exception_group_type is None:
            pytest.skip("ExceptionGroup is not available on this Python version")

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "attach",
                "--session-id",
                str(SESSION_ID),
                "--pid",
                "1234",
            ],
            side_effect=exception_group_type(
                "transport failed",
                [RuntimeError("connection refused")],
            ),
        )

        assert result.exit_code == 1
        assert "connection refused" in result.stderr

    def test_main_builds_session_query_status_payload(self) -> None:
        """Build a status query from --action status."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_session_query",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "status",
            ],
            response={
                "status": "success",
                "action": "status",
                "result": {"is_running": False},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked("gdb_session_query", {"session_id": SESSION_ID, "action": "status"})

    def test_main_rejects_session_query_list_with_session_id(self) -> None:
        """Reject a session list query that includes a session id."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "status",
                "--session-id",
                str(SESSION_ID),
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--session-id" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_session_query_status_without_session_id(self) -> None:
        """Reject a status query that omits the session id."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_session_query",
                "--action",
                "status",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "session_id" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_session_query_list_payload(self) -> None:
        """Build an unfiltered session list query."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "status",
            ],
            response={
                "status": "success",
                "action": "list",
                "result": {"sessions": []},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked("gdb_session_query", {"action": "list"})

    def test_main_builds_session_manage_stop_payload(self) -> None:
        """Build a session stop request."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "stop",
                "--session-id",
                str(SESSION_ID),
            ],
            response={
                "status": "success",
                "action": "stop",
                "result": {"stopped": True},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_session_manage",
            {"session_id": SESSION_ID, "action": "stop", "session": {}},
        )

    def test_main_builds_inferior_manage_create_payload(self) -> None:
        """Build an inferior create request."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_inferior_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "create",
                "--executable",
                "/bin/true",
                "--make-current",
            ],
            response={
                "status": "success",
                "action": "create",
                "result": {"inferior_id": 3},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inferior_manage",
            {
                "session_id": SESSION_ID,
                "action": "create",
                "inferior": {"executable": "/bin/true", "make_current": True},
            },
        )

    def test_main_builds_inferior_query_current_payload(self) -> None:
        """Build an inferior current-query request."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_inferior_query",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "current",
            ],
            response={
                "status": "success",
                "action": "current",
                "result": {"inferior_id": 1},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked("gdb_inferior_query", {"session_id": SESSION_ID, "action": "current"})

    def test_main_rejects_inferior_remove_with_executable_flag(self) -> None:
        """Reject executable flags when removing an inferior."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_inferior_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "remove",
                "--inferior-id",
                "2",
                "--executable",
                "/bin/true",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--executable" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_inferior_set_detach_on_fork_default_payload(self) -> None:
        """Default detach-on-fork to enabled."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_inferior_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "set_detach_on_fork",
            ],
            response={
                "status": "success",
                "action": "set_detach_on_fork",
                "result": {"enabled": True},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inferior_manage",
            {
                "session_id": SESSION_ID,
                "action": "set_detach_on_fork",
                "inferior": {"enabled": True},
            },
        )

    def test_main_rejects_execution_interrupt_with_wait_flags(self) -> None:
        """Reject wait flags on interrupt."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "interrupt",
                "--session-id",
                str(SESSION_ID),
                "--wait-until",
                "stop",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--wait-until" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_execution_interrupt_with_args(self) -> None:
        """Reject run arguments on interrupt."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "interrupt",
                "--session-id",
                str(SESSION_ID),
                "--arg",
                "x",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--arg" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_execution_run_action_payload(self) -> None:
        """Build an execution run request and render its result."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "run",
                "--session-id",
                str(SESSION_ID),
                "--arg=--mode",
                "--arg",
                "fast",
                "--wait-until",
                "stop",
                "--timeout-sec",
                "30",
            ],
            response={
                "status": "success",
                "action": "run",
                "result": {"execution_state": "paused"},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_execution_manage",
            {
                "session_id": SESSION_ID,
                "action": "run",
                "execution": {
                    "args": ["--mode", "fast"],
                    "wait_until": "stop",
                    "timeout_sec": 30,
                },
            },
        )
        rendered = result.stdout
        assert "action: run" in rendered
        assert "execution_state: paused" in rendered

    def test_main_builds_execution_wait_for_stop_payload(self) -> None:
        """Build a wait-for-stop request with stop reasons."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "wait-stop",
                "--session-id",
                str(SESSION_ID),
                "--timeout-sec",
                "9",
                "--stop-reason",
                "breakpoint-hit",
                "--stop-reason",
                "end-stepping-range",
            ],
            response={
                "status": "success",
                "action": "wait_for_stop",
                "result": {"matched": True},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_execution_manage",
            {
                "session_id": SESSION_ID,
                "action": "wait_for_stop",
                "execution": {
                    "timeout_sec": 9,
                    "stop_reasons": ["breakpoint-hit", "end-stepping-range"],
                },
            },
        )

    def test_main_rejects_execution_wait_for_stop_zero_timeout(self) -> None:
        """Reject a zero wait timeout."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "wait-stop",
                "--session-id",
                str(SESSION_ID),
                "--timeout-sec",
                "0",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "timeout_sec" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_context_threads_with_thread_flag(self) -> None:
        """Reject a thread id on the threads listing."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "threads",
                "--session-id",
                str(SESSION_ID),
                "--thread-id",
                "2",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--thread-id" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_context_query_backtrace_payload(self) -> None:
        """Build a backtrace query with thread and frame limits."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "bt",
                "--session-id",
                str(SESSION_ID),
                "--thread-id",
                "2",
                "--max-frames",
                "10",
            ],
            response={
                "status": "success",
                "action": "backtrace",
                "result": {"count": 3},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_context_query",
            {
                "session_id": SESSION_ID,
                "action": "backtrace",
                "query": {"thread_id": 2, "max_frames": 10},
            },
        )

    def test_main_rejects_context_backtrace_with_frame_flag(self) -> None:
        """Reject a frame selector on a backtrace."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "bt",
                "--session-id",
                str(SESSION_ID),
                "--frame",
                "2",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--frame" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_context_manage_select_frame_payload(self) -> None:
        """Build a select-frame context request."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_context_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "select_frame",
                "--frame",
                "2",
            ],
            response={
                "status": "success",
                "action": "select_frame",
                "result": {"frame": 2},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_context_manage",
            {
                "session_id": SESSION_ID,
                "action": "select_frame",
                "context": {"frame": 2},
            },
        )

    def test_main_rejects_context_select_thread_with_frame_flag(self) -> None:
        """Reject a frame selector on select-thread."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_context_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "select_thread",
                "--thread-id",
                "3",
                "--frame",
                "2",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--frame" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_breakpoint_query_list_payload(self) -> None:
        """Build an unfiltered breakpoint list query."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "break",
                "list",
                "--session-id",
                str(SESSION_ID),
            ],
            response={
                "status": "success",
                "action": "list",
                "result": {"count": 0},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked("gdb_breakpoint_query", {"session_id": SESSION_ID, "action": "list"})

    def test_main_builds_breakpoint_query_filtered_list_payload(self) -> None:
        """Build a filtered breakpoint list query."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "break",
                "list",
                "--session-id",
                str(SESSION_ID),
                "--kind",
                "code",
                "--kind",
                "watch",
                "--no-enabled",
            ],
            response={
                "status": "success",
                "action": "list",
                "result": {"count": 1},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_breakpoint_query",
            {
                "session_id": SESSION_ID,
                "action": "list",
                "query": {"kinds": ["code", "watch"], "enabled": False},
            },
        )

    def test_main_renders_action_payload_top_level_list(self) -> None:
        """Render top-level list fields one item per line."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "break",
                "list",
                "--session-id",
                str(SESSION_ID),
            ],
            response={
                "status": "success",
                "action": "list",
                "warnings": ["deprecated-filter", "partial-symbols"],
                "result": {"count": 0},
            },
        )

        assert result.exit_code == 0
        rendered = result.stdout
        assert "warnings:" in rendered
        assert "- deprecated-filter" in rendered
        assert "- partial-symbols" in rendered
        assert "warnings: ['deprecated-filter', 'partial-symbols']" not in rendered

    def test_main_builds_breakpoint_query_get_payload(self) -> None:
        """Build a breakpoint get query by number."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_query",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "get",
                "--number",
                "1",
            ],
            response={
                "status": "success",
                "action": "get",
                "result": {"breakpoint": {"number": 1}},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_breakpoint_query",
            {
                "session_id": SESSION_ID,
                "action": "get",
                "query": {"number": 1},
            },
        )

    def test_main_rejects_breakpoint_query_get_with_list_flags(self) -> None:
        """Reject list filters on a breakpoint get query."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_query",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "get",
                "--number",
                "1",
                "--kind",
                "code",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--kind" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_breakpoint_create_payload(self) -> None:
        """Build a breakpoint create request."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "break",
                "add",
                "--session-id",
                str(SESSION_ID),
                "--breakpoint-kind",
                "code",
                "--location",
                "main",
                "--temporary",
            ],
            response={
                "status": "success",
                "action": "create",
                "result": {"breakpoint": {"number": "1"}},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_breakpoint_manage",
            {
                "session_id": SESSION_ID,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main", "temporary": True},
            },
        )

    def test_main_builds_breakpoint_update_payload(self) -> None:
        """Build a breakpoint update request."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "update",
                "--number",
                "1",
                "--condition",
                "i == 3",
            ],
            response={
                "status": "success",
                "action": "update",
                "result": {"updated": True},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_breakpoint_manage",
            {
                "session_id": SESSION_ID,
                "action": "update",
                "breakpoint": {"number": 1},
                "changes": {"condition": "i == 3"},
            },
        )

    def test_main_rejects_breakpoint_update_with_create_flags(self) -> None:
        """Reject create flags on a breakpoint update."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "update",
                "--number",
                "1",
                "--breakpoint-kind",
                "code",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--breakpoint-kind" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_breakpoint_update_with_location_flag_only(self) -> None:
        """Reject a location without a breakpoint kind on update."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "update",
                "--number",
                "1",
                "--location",
                "main",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--location" in result.stderr
        assert "--breakpoint-kind" not in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_breakpoint_update_with_explicit_no_temporary(self) -> None:
        """Reject an explicit no-temporary flag on update."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "update",
                "--number",
                "1",
                "--no-temporary",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--temporary" in result.stderr
        result.invoke.assert_not_awaited()

    def test_build_breakpoint_update_omits_default_clear_condition_before_validation(self) -> None:
        """Omit the default clear-condition flag before validation."""

        args = parse_client_args(
            [
                "--server-url",
                SERVER_URL,
                "tool",
                "gdb_breakpoint_manage",
                "--session-id",
                str(SESSION_ID),
                "--action",
                "update",
                "--number",
                "1",
                "--condition",
                "i == 3",
            ],
            parser=build_parser(),
        )
        typed_input = client_specs.parse_breakpoint_manage_input(args)

        payload = serialize_model(client_specs._build_breakpoint_manage(typed_input))

        assert payload == {
            "session_id": SESSION_ID,
            "action": "update",
            "breakpoint": {"number": 1},
            "changes": {"condition": "i == 3"},
        }

    def test_main_builds_inspect_source_location_payload(self) -> None:
        """Build a source query for a file and line."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "list",
                "--session-id",
                str(SESSION_ID),
                "--location-kind",
                "file-line",
                "--file",
                "src/main.c",
                "--line",
                "42",
                "--context-before",
                "2",
                "--context-after",
                "3",
            ],
            response={
                "status": "success",
                "action": "source",
                "result": {"line_start": 40},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inspect_query",
            {
                "session_id": SESSION_ID,
                "action": "source",
                "query": {
                    "location": {"kind": "file_line", "file": "src/main.c", "line": 42},
                    "context_before": 2,
                    "context_after": 3,
                },
            },
        )

    def test_main_builds_inspect_source_address_location_payload(self) -> None:
        """Build a source query for an address."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "list",
                "--session-id",
                str(SESSION_ID),
                "--location-kind",
                "address",
                "--address",
                "0x401000",
            ],
            response={
                "status": "success",
                "action": "source",
                "result": {"line_start": 40},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inspect_query",
            {
                "session_id": SESSION_ID,
                "action": "source",
                "query": {"location": {"kind": "address", "address": "0x401000"}},
            },
        )

    def test_main_builds_inspect_disassembly_address_location_payload(self) -> None:
        """Build a disassembly query for an address."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "disasm",
                "--session-id",
                str(SESSION_ID),
                "--location-kind",
                "address",
                "--address",
                "0x401000",
            ],
            response={
                "status": "success",
                "action": "disassembly",
                "result": {"count": 4},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inspect_query",
            {
                "session_id": SESSION_ID,
                "action": "disassembly",
                "query": {"location": {"kind": "address", "address": "0x401000"}},
            },
        )

    def test_main_defaults_source_location_to_current(self) -> None:
        """Default the source location to the current frame."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "list",
                "--session-id",
                str(SESSION_ID),
            ],
            response={
                "status": "success",
                "action": "source",
                "result": {"line_start": 40},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inspect_query",
            {
                "session_id": SESSION_ID,
                "action": "source",
                "query": {"location": {"kind": "current"}},
            },
        )

    def test_main_defaults_disassembly_location_to_current(self) -> None:
        """Default the disassembly location to the current frame."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "disasm",
                "--session-id",
                str(SESSION_ID),
            ],
            response={
                "status": "success",
                "action": "disassembly",
                "result": {"count": 4},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_inspect_query",
            {
                "session_id": SESSION_ID,
                "action": "disassembly",
                "query": {"location": {"kind": "current"}},
            },
        )

    def test_main_honors_an_explicit_session_query_status_action(self) -> None:
        """Honour an explicit status action on the status command."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "status",
                "--action",
                "status",
                "--session-id",
                str(SESSION_ID),
            ],
            response={
                "status": "success",
                "action": "status",
                "result": {"is_running": False},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked("gdb_session_query", {"session_id": SESSION_ID, "action": "status"})

    def test_main_normalizes_short_workflow_step_tool_names(self) -> None:
        """Normalize short workflow step tool names."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "context_query",
                "--step-arg",
                "action=threads",
            ],
            response={"status": "success", "result": {"count": 1, "error_count": 0}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_workflow_batch",
            {
                "session_id": SESSION_ID,
                "steps": [{"tool": "gdb_context_query", "arguments": {"action": "threads"}}],
            },
        )

    def test_main_builds_workflow_batch_from_grouped_step_flags(self) -> None:
        """Build a workflow batch from grouped step flags."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_context_query",
                "--step-label",
                "stack",
                "--step-arg",
                "action=backtrace",
                "--step-arg",
                "query.max_frames=20",
                "--step",
                "gdb_inspect_query",
                "--step-arg",
                "action=evaluate",
                "--step-arg",
                "query.expression=counter",
                "--no-fail-fast",
            ],
            response={"status": "success", "result": {"count": 2, "error_count": 0}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_workflow_batch",
            {
                "session_id": SESSION_ID,
                "steps": [
                    {
                        "tool": "gdb_context_query",
                        "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
                        "label": "stack",
                    },
                    {
                        "tool": "gdb_inspect_query",
                        "arguments": {"action": "evaluate", "query": {"expression": "counter"}},
                    },
                ],
                "fail_fast": False,
            },
        )

    def test_main_renders_workflow_batch_human_output(self) -> None:
        """Render workflow batch steps as readable text."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_context_query",
                "--step-arg",
                "action=backtrace",
            ],
            response={
                "status": "success",
                "result": {
                    "count": 1,
                    "error_count": 0,
                    "steps": [
                        {
                            "tool": "gdb_context_query",
                            "label": "stack",
                            "action": "backtrace",
                            "status": "success",
                            "result": {
                                "status": "success",
                                "action": "backtrace",
                                "result": {"count": 1},
                            },
                        }
                    ],
                },
            },
        )

        assert result.exit_code == 0
        rendered = result.stdout
        assert "steps:" in rendered
        assert "tool: gdb_context_query" in rendered
        assert "label: stack" in rendered
        assert "{'tool':" not in rendered

    def test_build_workflow_batch_omits_default_flags_before_validation(self) -> None:
        """Omit default workflow flags before validation."""

        args = parse_client_args(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_context_query",
                "--step-label",
                "stack",
                "--step-arg",
                "action=backtrace",
                "--step-arg",
                "query.max_frames=20",
                "--no-fail-fast",
            ],
            parser=build_parser(),
        )
        typed_input = client_specs.parse_workflow_batch_input(args)

        payload = serialize_model(client_specs._build_workflow_batch(typed_input))

        assert payload == {
            "session_id": SESSION_ID,
            "steps": [
                {
                    "tool": "gdb_context_query",
                    "label": "stack",
                    "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
                }
            ],
            "fail_fast": False,
        }

    def test_main_builds_workflow_batch_preserves_string_step_arguments(self) -> None:
        """Keep numeric step arguments as strings."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_inspect_query",
                "--step-arg",
                "action=evaluate",
                "--step-arg",
                "query.expression=123",
            ],
            response={"status": "success", "result": {"count": 1, "error_count": 0}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_workflow_batch",
            {
                "session_id": SESSION_ID,
                "steps": [
                    {
                        "tool": "gdb_inspect_query",
                        "arguments": {"action": "evaluate", "query": {"expression": "123"}},
                    }
                ],
            },
        )

    def test_main_builds_workflow_batch_list_step_arguments(self) -> None:
        """Collect repeated list step arguments."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_execution_manage",
                "--step-arg",
                "action=run",
                "--step-arg",
                "execution.args=one",
                "--step-arg",
                "execution.args=two",
                "--step-arg",
                "execution.wait_until=stop",
            ],
            response={"status": "success", "result": {"count": 1, "error_count": 0}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_workflow_batch",
            {
                "session_id": SESSION_ID,
                "steps": [
                    {
                        "tool": "gdb_execution_manage",
                        "arguments": {
                            "action": "run",
                            "execution": {"wait_until": "stop", "args": ["one", "two"]},
                        },
                    }
                ],
            },
        )

    def test_main_builds_workflow_batch_single_list_step_argument(self) -> None:
        """Wrap a single list step argument in a list."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_breakpoint_query",
                "--step-arg",
                "action=list",
                "--step-arg",
                "query.kinds=code",
            ],
            response={"status": "success", "result": {"count": 1, "error_count": 0}},
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_workflow_batch",
            {
                "session_id": SESSION_ID,
                "steps": [
                    {
                        "tool": "gdb_breakpoint_query",
                        "arguments": {"action": "list", "query": {"kinds": ["code"]}},
                    }
                ],
            },
        )

    def test_main_rejects_workflow_batch_step_session_id_argument(self) -> None:
        """Reject a step argument that sets session_id."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_context_query",
                "--step-arg",
                "session_id=9",
                "--step-arg",
                "action=threads",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "must not include session_id" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_workflow_batch_session_query_list_step(self) -> None:
        """Reject a session list query inside a workflow step."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_session_query",
                "--step-arg",
                "action=list",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "gdb_session_query(action=list) is not valid inside workflow steps" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_workflow_batch_conflicting_dotted_assignments(self) -> None:
        """Reject conflicting dotted step assignments."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_context_query",
                "--step-arg",
                "query=raw",
                "--step-arg",
                "query.max_frames=1",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "Conflicting dotted assignment" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_workflow_batch_malformed_dotted_assignment(self) -> None:
        """Reject malformed dotted step assignments."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "batch",
                "--session-id",
                str(SESSION_ID),
                "--step",
                "gdb_context_query",
                "--step-arg",
                "query..max_frames=1",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "non-empty dotted segments" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_builds_run_until_failure_with_prefixed_flags(self) -> None:
        """Build a failure campaign from prefixed flags."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--startup-program",
                "/bin/true",
                "--startup-init-command",
                "set pagination off",
                "--max-iterations",
                "2",
                "--run-timeout-sec",
                "15",
                "--failure-stop-reason",
                "signal-received",
                "--capture-expression",
                "errno",
                "--capture-memory-range",
                "&errno:8",
            ],
            response={
                "status": "success",
                "result": {"matched_failure": False, "iterations_completed": 1},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_run_until_failure",
            {
                "startup": {"program": "/bin/true", "init_commands": ["set pagination off"]},
                "run_timeout_sec": 15,
                "max_iterations": 2,
                "failure": {"stop_reasons": ["signal-received"]},
                "capture": {"expressions": ["errno"], "memory_ranges": ["&errno:8"]},
            },
        )

    def test_main_renders_run_until_failure_human_output(self) -> None:
        """Render campaign iterations and artifacts as text."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--startup-program",
                "/bin/true",
            ],
            response={
                "status": "success",
                "result": {
                    "matched_failure": True,
                    "iterations_completed": 1,
                    "iterations": [
                        {"iteration": 1, "status": "success", "trigger": "signal-received"}
                    ],
                    "capture_bundle": {
                        "bundle_name": "cli-failure",
                        "artifacts": [
                            {
                                "name": "manifest.json",
                                "path": "/tmp/cli-failure/manifest.json",
                                "status": "written",
                            }
                        ],
                    },
                },
            },
        )

        assert result.exit_code == 0
        rendered = result.stdout
        assert "iterations:" in rendered
        assert "iteration: 1" in rendered
        assert "artifacts:" in rendered
        assert "name: manifest.json" in rendered
        assert "{'iteration':" not in rendered

    def test_main_builds_run_until_failure_setup_steps_from_prefixed_flags(self) -> None:
        """Build campaign setup steps from prefixed flags."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--setup-step",
                "gdb_capture_bundle",
                "--setup-step-arg",
                "expressions=errno",
                "--setup-step-arg",
                "expressions=counter",
            ],
            response={
                "status": "success",
                "result": {"matched_failure": False, "iterations_completed": 1},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_run_until_failure",
            {
                "setup_steps": [
                    {
                        "tool": "gdb_capture_bundle",
                        "arguments": {"expressions": ["errno", "counter"]},
                    }
                ]
            },
        )

    def test_main_builds_run_until_failure_setup_step_single_list_argument(self) -> None:
        """Wrap a single setup-step list argument in a list."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--setup-step",
                "gdb_capture_bundle",
                "--setup-step-arg",
                "expressions=errno",
            ],
            response={
                "status": "success",
                "result": {"matched_failure": False, "iterations_completed": 1},
            },
        )

        assert result.exit_code == 0
        result.assert_invoked(
            "gdb_run_until_failure",
            {
                "setup_steps": [
                    {"tool": "gdb_capture_bundle", "arguments": {"expressions": ["errno"]}}
                ]
            },
        )

    def test_build_run_until_failure_omits_default_sections_before_validation(self) -> None:
        """Omit default campaign sections before validation."""

        args = parse_client_args(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--startup-program",
                "/bin/true",
                "--max-iterations",
                "2",
                "--run-timeout-sec",
                "15",
                "--failure-stop-reason",
                "signal-received",
                "--capture-expression",
                "errno",
                "--capture-memory-range",
                "&errno:8",
            ],
            parser=build_parser(),
        )
        typed_input = client_specs.parse_run_until_failure_input(args)

        payload = serialize_model(client_specs._build_run_until_failure(typed_input))

        assert payload == {
            "startup": {"program": "/bin/true"},
            "run_timeout_sec": 15,
            "max_iterations": 2,
            "failure": {"stop_reasons": ["signal-received"]},
            "capture": {
                "expressions": ["errno"],
                "memory_ranges": ["&errno:8"],
            },
        }

    def test_main_rejects_run_until_failure_invalid_setup_step(self) -> None:
        """Reject a session-manage setup step."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--setup-step",
                "gdb_session_manage",
                "--setup-step-arg",
                "action=stop",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "gdb_session_manage is not valid inside workflow steps" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_inspect_source_with_disassembly_flags(self) -> None:
        """Reject disassembly flags on a source listing."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "list",
                "--session-id",
                str(SESSION_ID),
                "--location-kind",
                "current",
                "--instruction-count",
                "16",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--instruction-count" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_inspect_variables_with_file_flag_only(self) -> None:
        """Reject a file without a line for variable listing."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "locals",
                "--session-id",
                str(SESSION_ID),
                "--file",
                "src/main.c",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--file" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_inspect_variables_with_location_selector_flags(self) -> None:
        """Reject location selector flags on variable listing."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "locals",
                "--session-id",
                str(SESSION_ID),
                "--location-kind",
                "file-line",
                "--file",
                "src/main.c",
                "--line",
                "42",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "--location-kind" in result.stderr
        assert "--file" in result.stderr
        assert "--line" in result.stderr
        result.invoke.assert_not_awaited()

    def test_main_rejects_run_until_failure_conflicting_capture_names(self) -> None:
        """Reject conflicting capture bundle naming flags."""

        result = invoke_cli(
            [
                "--server-url",
                SERVER_URL,
                "campaign",
                "--capture-bundle-name",
                "exact-name",
                "--capture-bundle-name-prefix",
                "prefix",
            ],
            expect_exit=True,
        )

        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr
        result.invoke.assert_not_awaited()

    def test_client_tool_specs_preserve_cli_help_order(self) -> None:
        """Preserve the documented client tool order."""

        assert tuple(CLIENT_TOOL_SPECS) == (
            "gdb_session_start",
            "gdb_session_connect",
            "gdb_session_query",
            "gdb_session_manage",
            "gdb_inferior_query",
            "gdb_inferior_manage",
            "gdb_execution_manage",
            "gdb_breakpoint_query",
            "gdb_breakpoint_manage",
            "gdb_execute_command",
            "gdb_attach_process",
            "gdb_context_query",
            "gdb_context_manage",
            "gdb_inspect_query",
            "gdb_workflow_batch",
            "gdb_call_function",
            "gdb_capture_bundle",
            "gdb_run_until_failure",
        )
