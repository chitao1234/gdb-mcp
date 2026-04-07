"""Unit tests for the MCP CLI client command surface."""

from __future__ import annotations

import asyncio
import builtins
import json
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import main, parse_client_args
from gdb_mcp.client.runtime import ClientToolResponse


class TestClientCli:
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
