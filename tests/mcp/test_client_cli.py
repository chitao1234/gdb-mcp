"""Unit tests for the MCP CLI client command surface."""

from __future__ import annotations

import argparse
import asyncio
import builtins
import json
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import build_parser, main, parse_client_args
from gdb_mcp.client.parsers import CliUsageError
from gdb_mcp.client.renderers import render_action_payload
from gdb_mcp.client.specs import ActionVariant, _build_action_arguments
from gdb_mcp.client.runtime import ClientToolResponse
from gdb_mcp.mcp.schemas import SessionManageArgs, SessionQueryArgs


class TestClientCli:
    def test_render_action_payload_preserves_top_level_fields_on_key_collision(self):
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

    def test_build_action_arguments_rejects_reserved_field_overrides(self):
        namespace = argparse.Namespace(action="list")

        with pytest.raises(CliUsageError, match="action, session_id"):
            _build_action_arguments(
                namespace,
                model=SessionQueryArgs,
                variants={
                    "list": ActionVariant(
                        build_fields=lambda _: {
                            "query": {},
                            "action": "shadowed",
                            "session_id": 99,
                        }
                    )
                },
            )

    def test_build_action_arguments_rejects_explicit_false_flag(self):
        namespace = argparse.Namespace(action="stop", enabled=False)

        with pytest.raises(CliUsageError, match="--enabled"):
            _build_action_arguments(
                namespace,
                model=SessionManageArgs,
                variants={
                    "stop": ActionVariant(
                        build_fields=lambda _: {"session": {}},
                    )
                },
                tracked_fields=frozenset({"enabled"}),
            )

    def test_parse_client_args_requires_server_url(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_client_args(["gdb_session_start", "--program", "/bin/true"])

        assert exc_info.value.code == 2

    def test_build_parser_formats_help_with_percent_descriptions(self):
        help_text = build_parser().format_help()

        assert "gdb_call_function" in help_text

    def test_build_parser_still_exposes_session_subcommands(self):
        help_text = build_parser().format_help()

        assert "gdb_session_start" in help_text
        assert "gdb_session_query" in help_text

    def test_subcommand_help_preserves_percent_description(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            build_parser().parse_args(["gdb_call_function", "--help"])

        assert exc_info.value.code == 0
        help_text = capsys.readouterr().out
        assert 'x=%d\\n' in help_text
        assert 'x=%%d\\n' not in help_text

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_invokes_session_start_with_flat_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "session_id": 7,
                "message": "GDB session started successfully",
                "target_loaded": True,
                "execution_state": "not_started",
            },
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_start",
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
                stdout=stdout,
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_session_start",
            {
                "program": "/bin/true",
                "args": ["--mode", "fast"],
                "init_commands": ["set pagination off"],
                "env": {"TERM": "dumb"},
            },
            http_client=None,
        )
        rendered = stdout.getvalue()
        assert "session_id: 7" in rendered
        assert "target_loaded: True" in rendered

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_json_mode_prints_raw_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "message": "attached"},
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "--json",
                    "gdb_attach_process",
                    "--session-id",
                    "7",
                    "--pid",
                    "1234",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        assert json.loads(stdout.getvalue()) == {"status": "success", "message": "attached"}

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_accepts_global_flags_after_subcommand(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "message": "attached"},
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "gdb_attach_process",
                    "--session-id",
                    "7",
                    "--pid",
                    "1234",
                    "--json",
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_attach_process",
            {"session_id": 7, "pid": 1234, "timeout_sec": 30},
            http_client=None,
        )
        assert json.loads(stdout.getvalue()) == {"status": "success", "message": "attached"}

    def test_main_reports_schema_validation_as_cli_error(self):
        stderr = StringIO()

        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_execute_command",
                        "--session-id",
                        "7",
                        "--command",
                        "info breakpoints",
                        "--timeout-sec",
                        "0",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "timeout_sec" in stderr.getvalue()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_invokes_execute_command_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "message": "ok"},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_execute_command",
                    "--session-id",
                    "7",
                    "--command",
                    "info breakpoints",
                    "--timeout-sec",
                    "9",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_execute_command",
            {"session_id": 7, "command": "info breakpoints", "timeout_sec": 9},
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_invokes_call_function_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "message": "called"},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_call_function",
                    "--session-id",
                    "7",
                    "--function-call",
                    'printf("hello\\n")',
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_call_function",
            {"session_id": 7, "function_call": 'printf("hello\\n")', "timeout_sec": 30},
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_invokes_capture_bundle_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "artifact_count": 3},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_capture_bundle",
                    "--session-id",
                    "7",
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
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_capture_bundle",
            {
                "session_id": 7,
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
            http_client=None,
        )

    @patch(
        "gdb_mcp.client.cli.invoke_tool",
        new_callable=AsyncMock,
    )
    def test_main_reports_runtime_failures_without_traceback(self, mock_invoke_tool):
        exception_group_type = getattr(builtins, "ExceptionGroup", None)
        if exception_group_type is None:
            pytest.skip("ExceptionGroup is not available on this Python version")

        mock_invoke_tool.side_effect = exception_group_type(
            "transport failed",
            [RuntimeError("connection refused")],
        )
        stderr = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_attach_process",
                    "--session-id",
                    "7",
                    "--pid",
                    "1234",
                ],
                stderr=stderr,
            )
        )

        assert exit_code == 1
        assert "connection refused" in stderr.getvalue()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_session_query_status_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "status", "result": {"is_running": False}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_query",
                    "--session-id",
                    "7",
                    "--action",
                    "status",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_session_query",
            {"session_id": 7, "action": "status", "query": {}},
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_session_query_list_with_session_id(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_session_query",
                        "--action",
                        "list",
                        "--session-id",
                        "7",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--session-id" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_session_query_status_without_session_id(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_session_query",
                        "--action",
                        "status",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "session_id" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_session_query_list_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "list", "result": {"sessions": []}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_query",
                    "--action",
                    "list",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_session_query",
            {"action": "list", "query": {}},
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_session_manage_stop_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "stop", "result": {"stopped": True}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "stop",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_session_manage",
            {"session_id": 7, "action": "stop", "session": {}},
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inferior_manage_create_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "create", "result": {"inferior_id": 3}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inferior_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "create",
                    "--executable",
                    "/bin/true",
                    "--make-current",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inferior_manage",
            {
                "session_id": 7,
                "action": "create",
                "inferior": {"executable": "/bin/true", "make_current": True},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inferior_query_current_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "current", "result": {"inferior_id": 1}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inferior_query",
                    "--session-id",
                    "7",
                    "--action",
                    "current",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inferior_query",
            {
                "session_id": 7,
                "action": "current",
                "query": {},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_inferior_remove_with_executable_flag(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_inferior_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "remove",
                        "--inferior-id",
                        "2",
                        "--executable",
                        "/bin/true",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--executable" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inferior_set_detach_on_fork_default_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "set_detach_on_fork", "result": {"enabled": True}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inferior_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "set_detach_on_fork",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inferior_manage",
            {
                "session_id": 7,
                "action": "set_detach_on_fork",
                "inferior": {"enabled": True},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_execution_interrupt_with_wait_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_execution_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "interrupt",
                        "--wait-until",
                        "stop",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--wait-until" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_execution_interrupt_with_args(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_execution_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "interrupt",
                        "--arg",
                        "x",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--arg" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_execution_run_action_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "run", "result": {"execution_state": "paused"}},
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_execution_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "run",
                    "--arg=--mode",
                    "--arg",
                    "fast",
                    "--wait-until",
                    "stop",
                    "--wait-timeout-sec",
                    "30",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_execution_manage",
            {
                "session_id": 7,
                "action": "run",
                "execution": {
                    "args": ["--mode", "fast"],
                    "wait": {"until": "stop", "timeout_sec": 30},
                },
            },
            http_client=None,
        )
        rendered = stdout.getvalue()
        assert "action: run" in rendered
        assert "execution_state: paused" in rendered

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_execution_wait_for_stop_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "wait_for_stop", "result": {"matched": True}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_execution_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "wait_for_stop",
                    "--timeout-sec",
                    "9",
                    "--stop-reason",
                    "breakpoint-hit",
                    "--stop-reason",
                    "end-stepping-range",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_execution_manage",
            {
                "session_id": 7,
                "action": "wait_for_stop",
                "execution": {
                    "timeout_sec": 9,
                    "stop_reasons": ["breakpoint-hit", "end-stepping-range"],
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_execution_wait_for_stop_zero_timeout(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_execution_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "wait_for_stop",
                        "--timeout-sec",
                        "0",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "timeout_sec" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_context_threads_with_thread_flag(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_context_query",
                        "--session-id",
                        "7",
                        "--action",
                        "threads",
                        "--thread-id",
                        "2",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--thread-id" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_context_query_backtrace_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "backtrace", "result": {"count": 3}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_context_query",
                    "--session-id",
                    "7",
                    "--action",
                    "backtrace",
                    "--thread-id",
                    "2",
                    "--max-frames",
                    "10",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_context_query",
            {
                "session_id": 7,
                "action": "backtrace",
                "query": {"thread_id": 2, "max_frames": 10},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_context_backtrace_with_frame_flag(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_context_query",
                        "--session-id",
                        "7",
                        "--action",
                        "backtrace",
                        "--frame",
                        "2",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--frame" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_context_manage_select_frame_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "select_frame", "result": {"frame": 2}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_context_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "select_frame",
                    "--frame",
                    "2",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_context_manage",
            {
                "session_id": 7,
                "action": "select_frame",
                "context": {"frame": 2},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_context_select_thread_with_frame_flag(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success"},
            is_error=False,
        )

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_context_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "select_thread",
                        "--thread-id",
                        "3",
                        "--frame",
                        "2",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--frame" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_breakpoint_query_list_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "list", "result": {"count": 0}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_query",
                    "--session-id",
                    "7",
                    "--action",
                    "list",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_breakpoint_query",
            {
                "session_id": 7,
                "action": "list",
                "query": {},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_breakpoint_query_filtered_list_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "list", "result": {"count": 1}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_query",
                    "--session-id",
                    "7",
                    "--action",
                    "list",
                    "--kind",
                    "code",
                    "--kind",
                    "watch",
                    "--no-enabled",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_breakpoint_query",
            {
                "session_id": 7,
                "action": "list",
                "query": {"kinds": ["code", "watch"], "enabled": False},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_renders_action_payload_top_level_list(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "action": "list",
                "warnings": ["deprecated-filter", "partial-symbols"],
                "result": {"count": 0},
            },
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_query",
                    "--session-id",
                    "7",
                    "--action",
                    "list",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "warnings:" in rendered
        assert "- deprecated-filter" in rendered
        assert "- partial-symbols" in rendered
        assert "warnings: ['deprecated-filter', 'partial-symbols']" not in rendered

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_breakpoint_query_get_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "get", "result": {"breakpoint": {"number": 1}}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_query",
                    "--session-id",
                    "7",
                    "--action",
                    "get",
                    "--number",
                    "1",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_breakpoint_query",
            {
                "session_id": 7,
                "action": "get",
                "query": {"number": 1},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_breakpoint_query_get_with_list_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_breakpoint_query",
                        "--session-id",
                        "7",
                        "--action",
                        "get",
                        "--number",
                        "1",
                        "--kind",
                        "code",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--kind" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_breakpoint_create_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "action": "create",
                "result": {"breakpoint": {"number": "1"}},
            },
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "create",
                    "--breakpoint-kind",
                    "code",
                    "--location",
                    "main",
                    "--temporary",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_breakpoint_manage",
            {
                "session_id": 7,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main", "temporary": True},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_breakpoint_update_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "action": "update",
                "result": {"updated": True},
            },
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "update",
                    "--number",
                    "1",
                    "--condition",
                    "i == 3",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_breakpoint_manage",
            {
                "session_id": 7,
                "action": "update",
                "breakpoint": {"number": 1},
                "changes": {"condition": "i == 3", "clear_condition": False},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_breakpoint_update_with_create_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_breakpoint_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "update",
                        "--number",
                        "1",
                        "--breakpoint-kind",
                        "code",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--breakpoint-kind" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_breakpoint_update_with_location_flag_only(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_breakpoint_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "update",
                        "--number",
                        "1",
                        "--location",
                        "main",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--location" in stderr.getvalue()
        assert "--breakpoint-kind" not in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_breakpoint_update_with_explicit_no_temporary(
        self, mock_invoke_tool
    ):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_breakpoint_manage",
                        "--session-id",
                        "7",
                        "--action",
                        "update",
                        "--number",
                        "1",
                        "--no-temporary",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--temporary" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    def test_build_breakpoint_update_omits_default_clear_condition_before_validation(self):
        import gdb_mcp.client.specs as client_specs

        args = parse_client_args(
            [
                "--server-url",
                "http://127.0.0.1:8000/mcp",
                "gdb_breakpoint_manage",
                "--session-id",
                "7",
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

        with patch(
            "gdb_mcp.client.specs.validate_model_payload",
            side_effect=lambda _model, payload: payload,
        ):
            payload = client_specs._build_breakpoint_manage(typed_input)

        assert payload == {
            "session_id": 7,
            "action": "update",
            "breakpoint": {"number": 1},
            "changes": {"condition": "i == 3"},
        }

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inspect_source_location_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "source", "result": {"line_start": 40}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inspect_query",
                    "--session-id",
                    "7",
                    "--action",
                    "source",
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
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inspect_query",
            {
                "session_id": 7,
                "action": "source",
                "query": {
                    "location": {"kind": "file_line", "file": "src/main.c", "line": 42},
                    "context_before": 2,
                    "context_after": 3,
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inspect_source_address_location_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "source", "result": {"line_start": 40}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inspect_query",
                    "--session-id",
                    "7",
                    "--action",
                    "source",
                    "--location-kind",
                    "address",
                    "--address",
                    "0x401000",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inspect_query",
            {
                "session_id": 7,
                "action": "source",
                "query": {
                    "location": {"kind": "address", "address": "0x401000"},
                    "context_before": 5,
                    "context_after": 5,
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inspect_disassembly_address_location_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "disassembly", "result": {"count": 4}},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inspect_query",
                    "--session-id",
                    "7",
                    "--action",
                    "disassembly",
                    "--location-kind",
                    "address",
                    "--address",
                    "0x401000",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inspect_query",
            {
                "session_id": 7,
                "action": "disassembly",
                "query": {
                    "location": {"kind": "address", "address": "0x401000"},
                    "instruction_count": 32,
                    "mode": "mixed",
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_workflow_batch_from_grouped_step_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "count": 2, "error_count": 0},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_workflow_batch",
                    "--session-id",
                    "7",
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
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_workflow_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_context_query",
                        "label": "stack",
                        "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
                    },
                    {
                        "tool": "gdb_inspect_query",
                        "arguments": {"action": "evaluate", "query": {"expression": "counter"}},
                    },
                ],
                "fail_fast": False,
                "capture_stop_events": True,
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_renders_workflow_batch_human_output(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "count": 1,
                "error_count": 0,
                "steps": [
                    {
                        "tool": "gdb_context_query",
                        "label": "stack",
                        "action": "backtrace",
                        "result": {"result": {"count": 1}},
                    }
                ],
            },
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_workflow_batch",
                    "--session-id",
                    "7",
                    "--step",
                    "gdb_context_query",
                    "--step-arg",
                    "action=backtrace",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "steps:" in rendered
        assert "tool: gdb_context_query" in rendered
        assert "label: stack" in rendered
        assert "{'tool':" not in rendered

    def test_build_workflow_batch_omits_default_flags_before_validation(self):
        import gdb_mcp.client.specs as client_specs

        args = parse_client_args(
            [
                "--server-url",
                "http://127.0.0.1:8000/mcp",
                "gdb_workflow_batch",
                "--session-id",
                "7",
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

        with patch(
            "gdb_mcp.client.specs.validate_model_payload",
            side_effect=lambda _model, payload: payload,
        ):
            payload = client_specs._build_workflow_batch(typed_input)

        assert payload == {
            "session_id": 7,
            "steps": [
                {
                    "tool": "gdb_context_query",
                    "label": "stack",
                    "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
                }
            ],
            "fail_fast": False,
        }

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_workflow_batch_preserves_string_step_arguments(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "count": 1, "error_count": 0},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_workflow_batch",
                    "--session-id",
                    "7",
                    "--step",
                    "gdb_inspect_query",
                    "--step-arg",
                    "action=evaluate",
                    "--step-arg",
                    "query.expression=123",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_workflow_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_inspect_query",
                        "arguments": {"action": "evaluate", "query": {"expression": "123"}},
                    }
                ],
                "fail_fast": True,
                "capture_stop_events": True,
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_workflow_batch_list_step_arguments(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "count": 1, "error_count": 0},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_workflow_batch",
                    "--session-id",
                    "7",
                    "--step",
                    "gdb_execution_manage",
                    "--step-arg",
                    "action=run",
                    "--step-arg",
                    "execution.args=one",
                    "--step-arg",
                    "execution.args=two",
                    "--step-arg",
                    "execution.wait.until=stop",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_workflow_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_execution_manage",
                        "arguments": {
                            "action": "run",
                            "execution": {
                                "args": ["one", "two"],
                                "wait": {"until": "stop"},
                            },
                        },
                    }
                ],
                "fail_fast": True,
                "capture_stop_events": True,
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_workflow_batch_single_list_step_argument(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "count": 1, "error_count": 0},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_workflow_batch",
                    "--session-id",
                    "7",
                    "--step",
                    "gdb_breakpoint_query",
                    "--step-arg",
                    "action=list",
                    "--step-arg",
                    "query.kinds=code",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_workflow_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_breakpoint_query",
                        "arguments": {
                            "action": "list",
                            "query": {"kinds": ["code"]},
                        },
                    }
                ],
                "fail_fast": True,
                "capture_stop_events": True,
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_workflow_batch_step_session_id_argument(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_workflow_batch",
                        "--session-id",
                        "7",
                        "--step",
                        "gdb_context_query",
                        "--step-arg",
                        "session_id=9",
                        "--step-arg",
                        "action=threads",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "must not include session_id" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_workflow_batch_session_query_list_step(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_workflow_batch",
                        "--session-id",
                        "7",
                        "--step",
                        "gdb_session_query",
                        "--step-arg",
                        "action=list",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "gdb_session_query(action=list) is not valid inside workflow steps" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_workflow_batch_conflicting_dotted_assignments(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_workflow_batch",
                        "--session-id",
                        "7",
                        "--step",
                        "gdb_context_query",
                        "--step-arg",
                        "query=raw",
                        "--step-arg",
                        "query.max_frames=1",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "Conflicting dotted assignment" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_workflow_batch_malformed_dotted_assignment(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_workflow_batch",
                        "--session-id",
                        "7",
                        "--step",
                        "gdb_context_query",
                        "--step-arg",
                        "query..max_frames=1",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "non-empty dotted segments" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_run_until_failure_with_prefixed_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "matched_failure": False, "iterations_completed": 1},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_run_until_failure",
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
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_run_until_failure",
            {
                "startup": {
                    "program": "/bin/true",
                    "init_commands": ["set pagination off"],
                },
                "setup_steps": [],
                "run_timeout_sec": 15,
                "max_iterations": 2,
                "failure": {
                    "failure_on_error": True,
                    "failure_on_timeout": True,
                    "stop_reasons": ["signal-received"],
                    "execution_states": [],
                    "exit_codes": [],
                },
                "capture": {
                    "enabled": True,
                    "expressions": ["errno"],
                    "memory_ranges": ["&errno:8"],
                    "max_frames": 100,
                    "include_threads": True,
                    "include_backtraces": True,
                    "include_frame": True,
                    "include_variables": True,
                    "include_registers": True,
                    "include_transcript": True,
                    "include_stop_history": True,
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_renders_run_until_failure_human_output(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "matched_failure": True,
                "iterations_completed": 1,
                "iterations": [
                    {
                        "iteration": 1,
                        "status": "success",
                        "trigger": "signal-received",
                    }
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
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_run_until_failure",
                    "--startup-program",
                    "/bin/true",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "iterations:" in rendered
        assert "iteration: 1" in rendered
        assert "artifacts:" in rendered
        assert "name: manifest.json" in rendered
        assert "{'iteration':" not in rendered

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_run_until_failure_setup_steps_from_prefixed_flags(
        self,
        mock_invoke_tool,
    ):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "matched_failure": False, "iterations_completed": 1},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_run_until_failure",
                    "--setup-step",
                    "gdb_capture_bundle",
                    "--setup-step-arg",
                    "expressions=errno",
                    "--setup-step-arg",
                    "expressions=counter",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_run_until_failure",
            {
                "startup": {},
                "setup_steps": [
                    {
                        "tool": "gdb_capture_bundle",
                        "arguments": {
                            "expressions": ["errno", "counter"],
                            "memory_ranges": [],
                            "max_frames": 100,
                            "include_threads": True,
                            "include_backtraces": True,
                            "include_frame": True,
                            "include_variables": True,
                            "include_registers": True,
                            "include_transcript": True,
                            "include_stop_history": True,
                        },
                    }
                ],
                "run_timeout_sec": 30,
                "max_iterations": 1,
                "failure": {
                    "failure_on_error": True,
                    "failure_on_timeout": True,
                    "stop_reasons": ["signal-received", "exited-signalled"],
                    "execution_states": [],
                    "exit_codes": [],
                },
                "capture": {
                    "enabled": True,
                    "expressions": [],
                    "memory_ranges": [],
                    "max_frames": 100,
                    "include_threads": True,
                    "include_backtraces": True,
                    "include_frame": True,
                    "include_variables": True,
                    "include_registers": True,
                    "include_transcript": True,
                    "include_stop_history": True,
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_run_until_failure_setup_step_single_list_argument(
        self,
        mock_invoke_tool,
    ):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "matched_failure": False, "iterations_completed": 1},
            is_error=False,
        )

        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_run_until_failure",
                    "--setup-step",
                    "gdb_capture_bundle",
                    "--setup-step-arg",
                    "expressions=errno",
                ]
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_run_until_failure",
            {
                "startup": {},
                "setup_steps": [
                    {
                        "tool": "gdb_capture_bundle",
                        "arguments": {
                            "expressions": ["errno"],
                            "memory_ranges": [],
                            "max_frames": 100,
                            "include_threads": True,
                            "include_backtraces": True,
                            "include_frame": True,
                            "include_variables": True,
                            "include_registers": True,
                            "include_transcript": True,
                            "include_stop_history": True,
                        },
                    }
                ],
                "run_timeout_sec": 30,
                "max_iterations": 1,
                "failure": {
                    "failure_on_error": True,
                    "failure_on_timeout": True,
                    "stop_reasons": ["signal-received", "exited-signalled"],
                    "execution_states": [],
                    "exit_codes": [],
                },
                "capture": {
                    "enabled": True,
                    "expressions": [],
                    "memory_ranges": [],
                    "max_frames": 100,
                    "include_threads": True,
                    "include_backtraces": True,
                    "include_frame": True,
                    "include_variables": True,
                    "include_registers": True,
                    "include_transcript": True,
                    "include_stop_history": True,
                },
            },
            http_client=None,
        )

    def test_build_run_until_failure_omits_default_sections_before_validation(self):
        import gdb_mcp.client.specs as client_specs

        args = parse_client_args(
            [
                "--server-url",
                "http://127.0.0.1:8000/mcp",
                "gdb_run_until_failure",
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

        with patch(
            "gdb_mcp.client.specs.validate_model_payload",
            side_effect=lambda _model, payload: payload,
        ):
            payload = client_specs._build_run_until_failure(typed_input)

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

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_run_until_failure_invalid_setup_step(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_run_until_failure",
                        "--setup-step",
                        "gdb_session_manage",
                        "--setup-step-arg",
                        "action=stop",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "gdb_session_manage is not valid inside workflow steps" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_inspect_source_with_disassembly_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_inspect_query",
                        "--session-id",
                        "7",
                        "--action",
                        "source",
                        "--location-kind",
                        "current",
                        "--instruction-count",
                        "16",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--instruction-count" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_inspect_variables_with_file_flag_only(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_inspect_query",
                        "--session-id",
                        "7",
                        "--action",
                        "variables",
                        "--file",
                        "src/main.c",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--file" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_inspect_variables_with_location_selector_flags(
        self, mock_invoke_tool
    ):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_inspect_query",
                        "--session-id",
                        "7",
                        "--action",
                        "variables",
                        "--location-kind",
                        "file-line",
                        "--file",
                        "src/main.c",
                        "--line",
                        "42",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "--location-kind" in stderr.getvalue()
        assert "--file" in stderr.getvalue()
        assert "--line" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_rejects_run_until_failure_conflicting_capture_names(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(payload={"status": "success"}, is_error=False)

        stderr = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(
                main(
                    [
                        "--server-url",
                        "http://127.0.0.1:8000/mcp",
                        "gdb_run_until_failure",
                        "--capture-bundle-name",
                        "exact-name",
                        "--capture-bundle-name-prefix",
                        "prefix",
                    ],
                    stderr=stderr,
                )
            )

        assert exc_info.value.code == 2
        assert "mutually exclusive" in stderr.getvalue()
        mock_invoke_tool.assert_not_awaited()

    def test_client_tool_specs_preserve_cli_help_order(self):
        from gdb_mcp.client.specs import CLIENT_TOOL_SPECS

        assert tuple(CLIENT_TOOL_SPECS) == (
            "gdb_session_start",
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
