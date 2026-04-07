"""Unit tests for the MCP CLI client command surface."""

from __future__ import annotations

import argparse
import asyncio
import builtins
import json
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import main, parse_client_args
from gdb_mcp.client.parsers import CliUsageError
from gdb_mcp.client.renderers import render_action_payload
from gdb_mcp.client.specs import ActionVariant, _build_action_arguments
from gdb_mcp.client.runtime import ClientToolResponse
from gdb_mcp.mcp.schemas import SessionQueryArgs


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

    def test_parse_client_args_requires_server_url(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_client_args(["gdb_session_start", "--program", "/bin/true"])

        assert exc_info.value.code == 2

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
