"""Unit tests for the MCP CLI client runtime helper."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import TracebackType
from typing import Generic, TypeVar
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from gdb_mcp.client.runtime import _session_read_timeout, invoke_tool

ValueT = TypeVar("ValueT")


class _AsyncContextManager(Generic[ValueT]):
    def __init__(self, value: ValueT) -> None:
        self._value = value

    async def __aenter__(self) -> ValueT:
        return self._value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class TestClientRuntime:
    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_initializes_session_and_returns_json_payload(
        self,
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
    ) -> None:
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
        mock_client_session_cls.assert_called_once_with(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=1800.0),
        )
        session.initialize.assert_awaited_once_with()
        session.call_tool.assert_awaited_once_with("gdb_session_start", {"program": "/bin/true"})
        assert result.is_error is False
        assert result.payload["status"] == "success"
        assert result.payload["session_id"] == 7

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_preserves_tool_error_flag(
        self,
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
    ) -> None:
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
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
    ) -> None:
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

    @patch("gdb_mcp.client.runtime.httpx.AsyncClient")
    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_owned_http_client_uses_mcp_sse_timeouts(
        self,
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
        mock_async_client_cls: MagicMock,
    ) -> None:
        """A CLI-owned client must survive tool calls that outlive httpx defaults.

        httpx's built-in 5s read timeout tears down the streamed tool response for
        any call that runs longer than 5 seconds, so the client has to use the MCP
        SSE read timeout instead.
        """

        read_stream = Mock()
        write_stream = Mock()
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
        owned_client = AsyncMock()
        mock_async_client_cls.return_value = owned_client

        asyncio.run(
            invoke_tool(
                "http://127.0.0.1:8000/mcp",
                "gdb_session_query",
                {"action": "list", "query": {}},
                auth_token="secret",
            )
        )

        _, kwargs = mock_async_client_cls.call_args
        timeout = kwargs["timeout"]
        assert timeout.read is not None
        assert timeout.read > 5.0
        assert kwargs["headers"] == {"Authorization": "Bearer secret"}
        mock_streamable_http_client.assert_called_once_with(
            "http://127.0.0.1:8000/mcp",
            http_client=owned_client,
        )

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_prefers_structured_content(
        self,
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
    ) -> None:
        """Structured output should be used directly instead of re-parsing text."""

        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[],
                structuredContent={"status": "success", "value": 42},
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        result = asyncio.run(
            invoke_tool(
                "http://127.0.0.1:8000/mcp",
                "gdb_session_query",
                {"action": "list", "query": {}},
            )
        )

        assert result.payload == {"status": "success", "value": 42}
        assert result.is_error is False

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_rejects_non_object_json_payload(
        self,
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
    ) -> None:
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
        mock_streamable_http_client: MagicMock,
        mock_client_session_cls: MagicMock,
    ) -> None:
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(content=[TextContent(type="text", text="{not-json}")])
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


class TestSessionReadTimeout:
    """The per-call read timeout bounds silent hangs without capping normal work."""

    def test_defaults_to_thirty_minutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GDB_MCP_CLIENT_READ_TIMEOUT_SEC", raising=False)

        assert _session_read_timeout() == timedelta(seconds=1800.0)

    def test_env_override_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GDB_MCP_CLIENT_READ_TIMEOUT_SEC", "45.5")

        assert _session_read_timeout() == timedelta(seconds=45.5)

    @pytest.mark.parametrize("value", ["", "abc", "0", "-5"])
    def test_invalid_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("GDB_MCP_CLIENT_READ_TIMEOUT_SEC", value)

        assert _session_read_timeout() == timedelta(seconds=1800.0)
