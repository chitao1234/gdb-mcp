"""Compatibility entrypoint for the GDB MCP server."""

import asyncio
import logging
import os
from typing import Any

from .mcp import (
    BreakpointNumberArgs,
    CallFunctionArgs,
    EvaluateExpressionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetVariablesArgs,
    SessionIdArgs,
    SetBreakpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
    build_tool_definitions,
    create_mcp_app,
    dispatch_tool_call,
    run_stdio_app,
)
from .session.registry import SessionRegistry

# Set up logging - use GDB_MCP_LOG_LEVEL environment variable
log_level = os.environ.get("GDB_MCP_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SessionManager = SessionRegistry

# Global session manager instance
session_manager = SessionRegistry()


async def list_tools():
    """List all available GDB debugging tools."""

    return build_tool_definitions()


async def call_tool(name: str, arguments: Any):
    """Handle tool calls from the MCP client."""

    return await dispatch_tool_call(name, arguments, session_manager, logger=logger)


def _shutdown_sessions() -> None:
    """Stop all active sessions during server shutdown."""

    cleanup_results = session_manager.shutdown_all()
    if cleanup_results:
        logger.info("Stopped %s session(s) during shutdown", len(cleanup_results))


app = create_mcp_app(list_tools_handler=list_tools, call_tool_handler=call_tool)


async def main():
    """Main async entry point for the MCP server."""

    await run_stdio_app(
        app,
        startup_message="GDB MCP Server starting...",
        on_shutdown=_shutdown_sessions,
    )


def run_server():
    """Synchronous entry point for the MCP server (for script entry point)."""

    asyncio.run(main())


if __name__ == "__main__":
    run_server()
