"""Helpers for constructing and running the MCP app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool


def create_mcp_app(
    *,
    list_tools_handler: Callable[[], Awaitable[list[Tool]]],
    call_tool_handler: Callable[[str, Any], Awaitable[list[TextContent]]],
) -> Server:
    """Create an MCP app and register the provided handlers."""

    app = Server("gdb-mcp-server")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return await list_tools_handler()

    @app.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[TextContent]:
        return await call_tool_handler(name, arguments)

    return app


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
