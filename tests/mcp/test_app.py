"""Tests for MCP app helpers."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import ANY, AsyncMock, Mock, patch

import uvicorn

from starlette.responses import PlainTextResponse
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mcp.types import TextContent, Tool

from gdb_mcp.mcp.app import (
    _ReadyReportingServer,
    create_mcp_app,
    create_streamable_http_app,
    run_streamable_http_app,
)


class TestMcpAppToolRegistration:
    """Verify registered call tool handlers bypass the SDK schema gate."""

    def test_call_tool_skips_sdk_input_schema_validation(self):
        """Inputs valid for the typed models must reach the handler unchanged."""

        from mcp.shared.memory import create_connected_server_and_client_session

        seen: list[object] = []

        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="probe",
                    description="probe tool",
                    inputSchema={
                        "type": "object",
                        "properties": {"args": {"type": "array"}},
                        "required": ["args"],
                    },
                )
            ]

        async def call_tool(name: str, arguments: object) -> list[TextContent]:
            seen.append(arguments)
            return [TextContent(type="text", text='{"status": "success"}')]

        app = create_mcp_app(list_tools_handler=list_tools, call_tool_handler=call_tool)

        async def exercise():
            async with create_connected_server_and_client_session(app) as session:
                return await session.call_tool("probe", {"args": "not-an-array"})

        result = asyncio.run(exercise())

        assert result.isError is False
        assert seen == [{"args": "not-an-array"}]


class TestStreamableHttpApp:
    """Verify streamable HTTP app construction and serving."""

    @patch("gdb_mcp.mcp.app.StreamableHTTPSessionManager")
    def test_create_streamable_http_app_registers_route_and_shutdown(
        self,
        mock_session_manager_cls,
    ):
        """The helper should build the route and wire shutdown cleanup into lifespan."""

        mcp_app = Mock()
        shutdown = Mock()
        session_manager = Mock()

        @asynccontextmanager
        async def manager_lifespan():
            yield

        session_manager.run.return_value = manager_lifespan()
        mock_session_manager_cls.return_value = session_manager

        app = create_streamable_http_app(
            mcp_app,
            path="/mcp",
            on_shutdown=shutdown,
        )

        assert isinstance(app, Starlette)
        assert [route.path for route in app.routes] == ["/mcp", "/shutdown"]

        async def exercise_lifespan() -> None:
            async with app.router.lifespan_context(app):
                pass

        asyncio.run(exercise_lifespan())

        mock_session_manager_cls.assert_called_once_with(app=mcp_app)
        session_manager.run.assert_called_once_with()
        shutdown.assert_called_once_with()

    @patch("gdb_mcp.mcp.app.StreamableHTTPSessionManager")
    def test_create_streamable_http_app_forwards_requests_to_session_manager(
        self,
        mock_session_manager_cls,
    ):
        """The created Starlette app should route requests through the session manager."""

        mcp_app = Mock()
        shutdown = Mock()

        class FakeSessionManager:
            def __init__(self):
                self.scopes: list[dict[str, object]] = []

            @asynccontextmanager
            async def run(self):
                yield

            async def handle_request(self, scope, receive, send) -> None:
                self.scopes.append(scope)
                response = PlainTextResponse("ok")
                await response(scope, receive, send)

        session_manager = FakeSessionManager()
        mock_session_manager_cls.return_value = session_manager

        app = create_streamable_http_app(
            mcp_app,
            path="/mcp",
            on_shutdown=shutdown,
        )

        with TestClient(app) as client:
            response = client.post("/mcp", json={"jsonrpc": "2.0"})

        assert response.status_code == 200
        assert response.text == "ok"
        assert [scope["path"] for scope in session_manager.scopes] == ["/mcp"]
        shutdown.assert_called_once_with()

    @patch("gdb_mcp.mcp.app._ReadyReportingServer")
    @patch("gdb_mcp.mcp.app.uvicorn.Config")
    @patch("gdb_mcp.mcp.app.create_streamable_http_app")
    def test_run_streamable_http_app_uses_uvicorn(
        self,
        mock_create_streamable_http_app,
        mock_config_cls,
        mock_server_cls,
    ):
        """The helper should wrap the Starlette app in a uvicorn server."""

        mcp_app = Mock()
        starlette_app = Mock()
        shutdown = Mock()
        uvicorn_server = Mock()
        uvicorn_server.serve = AsyncMock(return_value=None)

        mock_create_streamable_http_app.return_value = starlette_app
        mock_server_cls.return_value = uvicorn_server

        asyncio.run(
            run_streamable_http_app(
                mcp_app,
                host="127.0.0.1",
                port=8000,
                path="/mcp",
                startup_message="GDB MCP Server starting...",
                on_shutdown=shutdown,
            )
        )

        mock_create_streamable_http_app.assert_called_once_with(
            mcp_app,
            path="/mcp",
            on_shutdown=shutdown,
            auth_token=None,
            idle_timeout_sec=0.0,
            has_active_sessions=None,
            request_shutdown=ANY,
        )
        mock_config_cls.assert_called_once()
        assert mock_config_cls.call_args.args[0] is starlette_app
        assert mock_config_cls.call_args.kwargs["host"] == "127.0.0.1"
        assert mock_config_cls.call_args.kwargs["port"] == 8000
        mock_server_cls.assert_called_once_with(
            mock_config_cls.return_value,
            ready_file=None,
            path="/mcp",
        )
        uvicorn_server.serve.assert_awaited_once_with()

    @patch("gdb_mcp.mcp.app.logging.getLogger")
    @patch("gdb_mcp.mcp.app._ReadyReportingServer")
    @patch("gdb_mcp.mcp.app.uvicorn.Config")
    @patch("gdb_mcp.mcp.app.create_streamable_http_app")
    def test_run_streamable_http_app_logs_the_startup_message(
        self,
        mock_create_streamable_http_app,
        mock_config_cls,
        mock_server_cls,
        mock_get_logger,
    ):
        """The startup message should be logged once with the effective log level."""

        mcp_app = Mock()
        app_logger = Mock()
        root_logger = Mock()
        root_logger.getEffectiveLevel.return_value = 20
        uvicorn_server = Mock()
        uvicorn_server.serve = AsyncMock(return_value=None)

        mock_create_streamable_http_app.return_value = Mock()
        mock_server_cls.return_value = uvicorn_server
        mock_get_logger.side_effect = [app_logger, root_logger]

        asyncio.run(
            run_streamable_http_app(
                mcp_app,
                host="127.0.0.1",
                port=8000,
                path="/mcp",
                startup_message="GDB MCP Server starting...",
            )
        )

        app_logger.info.assert_called_once_with(
            "%s starting",
            "GDB MCP Server starting...",
        )

    def test_ready_reporting_server_writes_the_bound_address(self, tmp_path):
        """The ready file must carry the address uvicorn actually bound."""

        config = uvicorn.Config(Mock(), host="127.0.0.1", port=0, log_level="warning")
        ready_file = tmp_path / "ready.json"
        server = _ReadyReportingServer(config, ready_file=ready_file, path="/mcp")

        class FakeSocket:
            def getsockname(self):
                return ("127.0.0.1", 42631)

        class FakeBoundServer:
            sockets = [FakeSocket()]

        server.servers = [FakeBoundServer()]

        with patch("gdb_mcp.mcp.app.uvicorn.Server.startup", new=AsyncMock()):
            asyncio.run(server.startup())

        assert json.loads(ready_file.read_text(encoding="utf-8")) == {
            "host": "127.0.0.1",
            "port": 42631,
            "path": "/mcp",
        }
