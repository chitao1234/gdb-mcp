"""Entrypoint for the GDB MCP server."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .mcp import (
    ServerRuntime,
    create_server_runtime,
)
from .session.registry import SessionRegistry

logger = logging.getLogger(__name__)


def create_default_runtime() -> ServerRuntime:
    """Create the default runtime used by the CLI entrypoint."""

    session_manager = SessionRegistry()
    return create_server_runtime(session_manager_provider=lambda: session_manager, logger=logger)


async def main() -> None:
    """Main async entry point for the MCP server."""

    await create_default_runtime().main()


def run_server() -> None:
    """Synchronous entry point for the MCP server."""

    configure_logging()
    _warn_if_shadowed_by_build_lib()
    create_default_runtime().run_server()


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
