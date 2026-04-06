"""Helpers for constructing and running the MCP app."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send


def create_mcp_app(
    *,
    list_tools_handler: Callable[[], Awaitable[list[Tool]]],
    call_tool_handler: Callable[[str, object], Awaitable[list[TextContent]]],
) -> Server:
    """Create an MCP app and register the provided handlers."""

    app = Server("gdb-mcp-server")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return await list_tools_handler()

    @app.call_tool()
    async def call_tool(name: str, arguments: object) -> list[TextContent]:
        return await call_tool_handler(name, arguments)

    return app


class StreamableHTTPASGIApp:
    """Thin ASGI adapter around the MCP SDK streamable HTTP session manager."""

    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._session_manager.handle_request(scope, receive, send)


def create_streamable_http_app(
    app: Server,
    *,
    path: str,
    on_shutdown: Callable[[], None] | None = None,
) -> Starlette:
    """Create a Starlette app that serves the MCP app over streamable HTTP."""

    session_manager = StreamableHTTPSessionManager(app=app)
    transport_app = StreamableHTTPASGIApp(session_manager)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with session_manager.run():
            try:
                yield
            finally:
                if on_shutdown is not None:
                    on_shutdown()

    return Starlette(
        routes=[Route(path, endpoint=transport_app)],
        lifespan=lifespan,
    )


async def run_stdio_app(
    app: Server,
    *,
    startup_message: str | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> None:
    """Run the MCP app on stdio and invoke cleanup when it exits."""

    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        if startup_message:
            import logging

            logging.getLogger(__name__).info(startup_message)

        try:
            await app.run(read_stream, write_stream, app.create_initialization_options())
        finally:
            if on_shutdown is not None:
                on_shutdown()


async def run_streamable_http_app(
    app: Server,
    *,
    host: str,
    port: int,
    path: str,
    startup_message: str | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> None:
    """Run the MCP app on streamable HTTP via uvicorn."""

    if startup_message:
        logging.getLogger(__name__).info(
            "%s Listening on http://%s:%s%s",
            startup_message,
            host,
            port,
            path,
        )

    starlette_app = create_streamable_http_app(
        app,
        path=path,
        on_shutdown=on_shutdown,
    )
    config = uvicorn.Config(
        starlette_app,
        host=host,
        port=port,
        log_level=logging.getLevelName(logging.getLogger().getEffectiveLevel()).lower(),
    )
    await uvicorn.Server(config).serve()
