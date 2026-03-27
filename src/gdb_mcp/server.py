"""Entrypoint for the GDB MCP server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading

from mcp.types import TextContent, Tool

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
    ListSessionsArgs,
    RunArgs,
    ServerRuntime,
    SessionIdArgs,
    SetBreakpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
    create_server_runtime,
)
from .session.registry import SessionRegistry

logger = logging.getLogger(__name__)

_runtime: "ServerRuntime | None" = None
_runtime_lock = threading.Lock()


def create_default_runtime() -> "ServerRuntime":
    """Create the default runtime used by the CLI compatibility entrypoint."""

    session_manager = SessionRegistry()
    return create_server_runtime(session_manager_provider=lambda: session_manager, logger=logger)


def get_runtime() -> "ServerRuntime":
    """Lazily create and cache the default runtime."""

    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = create_default_runtime()
    return _runtime


class _LazyAppProxy:
    """Defer access to the MCP Server object until it is actually needed."""

    def __getattr__(self, name: str) -> object:
        return getattr(get_runtime().app, name)


async def list_tools() -> list[Tool]:
    """List all available GDB debugging tools."""

    return await get_runtime().list_tools()


async def call_tool(name: str, arguments: object) -> list[TextContent]:
    """Handle tool calls from the MCP client."""

    return await get_runtime().call_tool(name, arguments)


app = _LazyAppProxy()


async def main() -> None:
    """Main async entry point for the MCP server."""

    await get_runtime().main()


def run_server() -> None:
    """Synchronous entry point for the MCP server (for script entry point)."""

    configure_logging()
    _warn_if_shadowed_by_build_lib()
    get_runtime().run_server()


def configure_logging() -> None:
    """Configure process logging for the standalone server entrypoint."""

    log_level = os.environ.get("GDB_MCP_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _warn_if_shadowed_by_build_lib() -> None:
    """Warn when this module is loaded from a local build/lib tree."""

    module_path = Path(__file__).resolve()
    normalized_parts = tuple(part.lower() for part in module_path.parts)
    if "build" not in normalized_parts or "lib" not in normalized_parts:
        return

    logger.warning(
        "Detected gdb_mcp imported from a build/lib path (%s). "
        "This can be stale and diverge from src/. "
        "Prefer an editable install or remove the local build/ tree.",
        module_path,
    )


if __name__ == "__main__":
    run_server()
