"""Unit tests for the MCP CLI client runtime helper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from gdb_mcp.client.runtime import invoke_tool


class _AsyncContextManager:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestClientRuntime:
    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_initializes_session_and_returns_json_payload(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='{"status":"success","session_id":7,"message":"started"}',
                    )
                ]
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        result = asyncio.run(
            invoke_tool(
                "http://127.0.0.1:8000/mcp",
                "gdb_session_start",
                {"program": "/bin/true"},
            )
        )

        mock_streamable_http_client.assert_called_once_with(
            "http://127.0.0.1:8000/mcp",
            http_client=None,
        )
        mock_client_session_cls.assert_called_once_with(read_stream, write_stream)
        session.initialize.assert_awaited_once_with()
        session.call_tool.assert_awaited_once_with("gdb_session_start", {"program": "/bin/true"})
        assert result.is_error is False
        assert result.payload["status"] == "success"
        assert result.payload["session_id"] == 7

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_preserves_tool_error_flag(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='{"status":"error","code":"gdb_error","message":"boom"}',
                    )
                ],
                isError=True,
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        result = asyncio.run(
            invoke_tool(
                "http://127.0.0.1:8000/mcp",
                "gdb_execute_command",
                {"session_id": 7, "command": "explode"},
            )
        )

        assert result.is_error is True
        assert result.payload["status"] == "error"
        assert result.payload["code"] == "gdb_error"

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_forwards_custom_http_client(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        http_client = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text='{"status":"success"}')]
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        asyncio.run(
            invoke_tool(
                "http://127.0.0.1:8000/mcp",
                "gdb_session_query",
                {"action": "list", "query": {}},
                http_client=http_client,
            )
        )

        mock_streamable_http_client.assert_called_once_with(
            "http://127.0.0.1:8000/mcp",
            http_client=http_client,
        )

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_rejects_non_object_json_payload(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text='["not","an","object"]')]
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        with pytest.raises(ValueError, match="Expected tool payload JSON object"):
            asyncio.run(
                invoke_tool(
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_query",
                    {"action": "list", "query": {}},
                )
            )

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_rejects_malformed_json_payload(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text="{not-json}")]
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        with pytest.raises(ValueError, match="Expected valid JSON tool payload"):
            asyncio.run(
                invoke_tool(
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_query",
                    {"action": "list", "query": {}},
                )
            )
