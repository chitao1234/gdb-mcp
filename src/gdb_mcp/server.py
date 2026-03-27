"""Entrypoint for the GDB MCP server."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .mcp import (
    AttachProcessArgs,
    BreakpointNumberArgs,
    CallFunctionArgs,
    EvaluateExpressionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetRegistersArgs,
    GetVariablesArgs,
    RunArgs,
    SessionIdArgs,
    SetBreakpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
    create_server_runtime,
)
from .session.registry import SessionRegistry

logger = logging.getLogger(__name__)

_runtime = None
_runtime_lock = threading.Lock()


def create_default_runtime():
    """Create the default runtime used by the CLI compatibility entrypoint."""

    session_manager = SessionRegistry()
    return create_server_runtime(session_manager_provider=lambda: session_manager, logger=logger)


def get_runtime():
    """Lazily create and cache the default runtime."""

    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = create_default_runtime()
    return _runtime


class _LazyAppProxy:
    """Defer access to the MCP Server object until it is actually needed."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_runtime().app, name)


async def list_tools():
    """List all available GDB debugging tools."""

    return await get_runtime().list_tools()


async def call_tool(name: str, arguments: Any):
    """Handle tool calls from the MCP client."""

    return await get_runtime().call_tool(name, arguments)


app = _LazyAppProxy()


async def main():
    """Main async entry point for the MCP server."""

    await get_runtime().main()


def run_server():
    """Synchronous entry point for the MCP server (for script entry point)."""

    configure_logging()
    get_runtime().run_server()


def configure_logging() -> None:
    """Configure process logging for the standalone server entrypoint."""

    log_level = os.environ.get("GDB_MCP_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


if __name__ == "__main__":
    run_server()
