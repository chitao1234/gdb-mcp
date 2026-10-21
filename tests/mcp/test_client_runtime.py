"""Unit tests for the MCP CLI client runtime helper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from types import TracebackType
from typing import Generic, TypeVar
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from mcp.types import CallToolResult, TextContent

from gdb_mcp.client import runtime
from gdb_mcp.client.runtime import _session_read_timeout, invoke_tool

from .cli_support import SERVER_URL

ValueT = TypeVar("ValueT")

DEFAULT_READ_TIMEOUT = timedelta(seconds=1800.0)


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


@dataclass(frozen=True)
class _RuntimeHarness:
    """Patched collaborators that one ``invoke_tool`` call would otherwise build."""

    session: AsyncMock
    streamable_http_client: MagicMock
    client_session_cls: MagicMock
    read_stream: Mock
    write_stream: Mock


@pytest.fixture
def runtime_harness(monkeypatch: pytest.MonkeyPatch) -> _RuntimeHarness:
    """Wire the transport and session doubles for one runtime invocation."""

    read_stream = Mock()
    write_stream = Mock()
    streamable_http_client = MagicMock()
    streamable_http_client.return_value = _AsyncContextManager(
        (read_stream, write_stream, lambda: "session-1")
    )
    monkeypatch.setattr(runtime, "streamable_http_client", streamable_http_client)

    session = AsyncMock()
    client_session_cls = MagicMock()
    client_session_cls.return_value = _AsyncContextManager(session)
    monkeypatch.setattr(runtime, "ClientSession", client_session_cls)

    return _RuntimeHarness(
        session=session,
        streamable_http_client=streamable_http_client,
        client_session_cls=client_session_cls,
        read_stream=read_stream,
        write_stream=write_stream,
    )


class TestClientRuntime:
    def test_invoke_tool_initializes_session_and_returns_json_payload(
        self, runtime_harness: _RuntimeHarness
    ) -> None:
        """The runtime should initialize the session and decode one JSON payload."""

        session = runtime_harness.session
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='{"status":"success","result":{"session_id":7,"message":"started"}}',
                    )
                ]
            )
        )

        result = asyncio.run(
            invoke_tool(
                SERVER_URL,
                "gdb_session_start",
                {"program": "/bin/true"},
            )
        )

        runtime_harness.streamable_http_client.assert_called_once_with(
            SERVER_URL,
            http_client=None,
        )
        runtime_harness.client_session_cls.assert_called_once_with(
            runtime_harness.read_stream,
            runtime_harness.write_stream,
            read_timeout_seconds=DEFAULT_READ_TIMEOUT,
        )
        session.initialize.assert_awaited_once_with()
        session.call_tool.assert_awaited_once_with("gdb_session_start", {"program": "/bin/true"})
        assert result.is_error is False
        assert result.payload["status"] == "success"
        assert result.envelope().session_id == 7

    def test_invoke_tool_preserves_tool_error_flag(self, runtime_harness: _RuntimeHarness) -> None:
        """A tool-level error should survive parsing as ``is_error``."""

        runtime_harness.session.call_tool = AsyncMock(
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

        result = asyncio.run(
            invoke_tool(
                SERVER_URL,
                "gdb_execute_command",
                {"session_id": 7, "command": "explode"},
            )
        )

        assert result.is_error is True
        assert result.payload["status"] == "error"
        assert result.payload["code"] == "gdb_error"

    def test_invoke_tool_forwards_custom_http_client(
        self, runtime_harness: _RuntimeHarness
    ) -> None:
        """A caller-supplied HTTP client should reach the streamable transport."""

        http_client = Mock()
        runtime_harness.session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text='{"status":"success"}')]
            )
        )

        asyncio.run(
            invoke_tool(
                SERVER_URL,
                "gdb_session_query",
                {"action": "list", "query": {}},
                http_client=http_client,
            )
        )

        runtime_harness.streamable_http_client.assert_called_once_with(
            SERVER_URL,
            http_client=http_client,
        )

    def test_owned_http_client_uses_mcp_sse_timeouts(
        self, runtime_harness: _RuntimeHarness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A CLI-owned client must survive tool calls that outlive httpx defaults.

        httpx's built-in 5s read timeout tears down the streamed tool response for
        any call that runs longer than 5 seconds, so the client has to use the MCP
        SSE read timeout instead.
        """

        mock_async_client_cls = MagicMock()
        owned_client = AsyncMock()
        mock_async_client_cls.return_value = owned_client
        monkeypatch.setattr(runtime.httpx, "AsyncClient", mock_async_client_cls)
        runtime_harness.session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text='{"status":"success"}')]
            )
        )

        asyncio.run(
            invoke_tool(
                SERVER_URL,
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
        runtime_harness.streamable_http_client.assert_called_once_with(
            SERVER_URL,
            http_client=owned_client,
        )

    def test_invoke_tool_prefers_structured_content(self, runtime_harness: _RuntimeHarness) -> None:
        """Structured output should be used directly instead of re-parsing text."""

        runtime_harness.session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[],
                structuredContent={"status": "success", "result": {"value": 42}},
            )
        )

        result = asyncio.run(
            invoke_tool(
                SERVER_URL,
                "gdb_session_query",
                {"action": "list", "query": {}},
            )
        )

        assert result.payload == {"status": "success", "result": {"value": 42}}
        assert result.is_error is False

    def test_invoke_tool_rejects_non_object_json_payload(
        self, runtime_harness: _RuntimeHarness
    ) -> None:
        """A JSON array is valid JSON but an invalid tool payload shape."""

        runtime_harness.session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text='["not","an","object"]')]
            )
        )

        with pytest.raises(ValueError, match="Expected tool payload JSON object"):
            asyncio.run(
                invoke_tool(
                    SERVER_URL,
                    "gdb_session_query",
                    {"action": "list", "query": {}},
                )
            )

    def test_invoke_tool_rejects_malformed_json_payload(
        self, runtime_harness: _RuntimeHarness
    ) -> None:
        """Malformed text content should surface as a clear parse error."""

        runtime_harness.session.call_tool = AsyncMock(
            return_value=CallToolResult(content=[TextContent(type="text", text="{not-json}")])
        )

        with pytest.raises(ValueError, match="Expected valid JSON tool payload"):
            asyncio.run(
                invoke_tool(
                    SERVER_URL,
                    "gdb_session_query",
                    {"action": "list", "query": {}},
                )
            )


class TestSessionReadTimeout:
    """The per-call read timeout bounds silent hangs without capping normal work."""

    def test_defaults_to_thirty_minutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unset environment variable falls back to the 30-minute default."""

        monkeypatch.delenv("GDB_MCP_CLIENT_READ_TIMEOUT_SEC", raising=False)

        assert _session_read_timeout() == DEFAULT_READ_TIMEOUT

    def test_env_override_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A positive override is interpreted as seconds."""

        monkeypatch.setenv("GDB_MCP_CLIENT_READ_TIMEOUT_SEC", "45.5")

        assert _session_read_timeout() == timedelta(seconds=45.5)

    @pytest.mark.parametrize("value", ["", "abc", "0", "-5"])
    def test_invalid_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Empty, non-numeric, and non-positive values keep the default."""

        monkeypatch.setenv("GDB_MCP_CLIENT_READ_TIMEOUT_SEC", value)

        assert _session_read_timeout() == DEFAULT_READ_TIMEOUT
