"""Compatibility entrypoint for the GDB MCP server."""

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
    create_server_runtime,
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
runtime = create_server_runtime(session_manager_provider=lambda: session_manager, logger=logger)


async def list_tools():
    """List all available GDB debugging tools."""

    return await runtime.list_tools()


async def call_tool(name: str, arguments: Any):
    """Handle tool calls from the MCP client."""

    return await runtime.call_tool(name, arguments)


app = runtime.app


async def main():
    """Main async entry point for the MCP server."""

    await runtime.main()


def run_server():
    """Synchronous entry point for the MCP server (for script entry point)."""

    runtime.run_server()


if __name__ == "__main__":
    run_server()
