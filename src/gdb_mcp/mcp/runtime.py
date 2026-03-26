"""Runtime composition for the GDB MCP server."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from mcp.server import Server
from mcp.types import TextContent, Tool

from ..session.registry import SessionRegistry
from .app import create_mcp_app, run_stdio_app
from .handlers import dispatch_tool_call
from .schemas import build_tool_definitions


@dataclass
class ServerRuntime:
    """Owns the MCP app runtime and resolves injected dependencies on demand."""

    session_manager_provider: Callable[[], SessionRegistry]
    logger: logging.Logger
    startup_message: str = "GDB MCP Server starting..."
    app: Server = field(init=False)

    def __post_init__(self) -> None:
        self.app = create_mcp_app(
            list_tools_handler=self.list_tools,
            call_tool_handler=self.call_tool,
        )

    @property
    def session_manager(self) -> SessionRegistry:
        """Resolve the current session manager."""

        return self.session_manager_provider()

    async def list_tools(self) -> list[Tool]:
        """List all available tools."""

        return build_tool_definitions()

    async def call_tool(self, name: str, arguments: Any) -> list[TextContent]:
        """Dispatch one tool call through the injected session manager."""

        return await dispatch_tool_call(name, arguments, self.session_manager, logger=self.logger)

    def shutdown_sessions(self) -> None:
        """Stop all active sessions during shutdown."""

        cleanup_results = self.session_manager.shutdown_all()
        if cleanup_results:
            self.logger.info("Stopped %s session(s) during shutdown", len(cleanup_results))

    async def main(self) -> None:
        """Run the MCP server over stdio."""

        await run_stdio_app(
            self.app,
            startup_message=self.startup_message,
            on_shutdown=self.shutdown_sessions,
        )

    def run_server(self) -> None:
        """Run the MCP server synchronously."""

        asyncio.run(self.main())


def create_server_runtime(
    *,
    session_manager_provider: Callable[[], SessionRegistry],
    logger: logging.Logger,
    startup_message: str = "GDB MCP Server starting...",
) -> ServerRuntime:
    """Create a configured MCP server runtime."""

    return ServerRuntime(
        session_manager_provider=session_manager_provider,
        logger=logger,
        startup_message=startup_message,
    )
