"""Entrypoint for the GDB MCP server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .mcp import (
    ServerRuntime,
    create_server_runtime,
)
from .session.registry import SessionRegistry

logger = logging.getLogger(__name__)
TransportKind = Literal["stdio", "streamable-http"]
_AUTH_TOKEN_ENV = "GDB_MCP_AUTH_TOKEN"


@dataclass(frozen=True)
class ServerCliConfig:
    """Normalized CLI configuration for server startup."""

    transport: TransportKind
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    auth_token: str | None = None
    ready_file: Path | None = None
    idle_timeout_sec: float = 0.0


def create_default_runtime() -> ServerRuntime:
    """Create the default runtime used by the CLI entrypoint."""

    session_manager = SessionRegistry()
    return create_server_runtime(session_manager_provider=lambda: session_manager, logger=logger)


def _parse_http_path(value: str) -> str:
    """Validate and normalize the configured HTTP route path."""

    if not value.startswith("/") or "?" in value or "#" in value:
        raise argparse.ArgumentTypeError(
            "--path must start with '/' and cannot contain query strings or fragments"
        )
    return value


def _parse_http_port(value: str) -> int:
    """Validate the configured HTTP port."""

    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--port must be an integer") from exc

    if 0 <= port <= 65535:
        return port

    raise argparse.ArgumentTypeError("--port must be between 0 and 65535")


def parse_server_config(argv: Sequence[str] | None = None) -> ServerCliConfig:
    """Parse and validate transport-selection CLI flags."""

    parser = argparse.ArgumentParser(prog="gdb-mcp-server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host")
    parser.add_argument("--port", type=_parse_http_port)
    parser.add_argument("--path", type=_parse_http_path)
    parser.add_argument("--auth-token")
    parser.add_argument("--ready-file")
    parser.add_argument("--idle-timeout-sec", type=float)
    args = parser.parse_args(argv)

    if args.idle_timeout_sec is not None and args.idle_timeout_sec < 0:
        parser.error("--idle-timeout-sec must be >= 0")

    if args.transport == "stdio":
        invalid_flags = [
            flag
            for flag, value in (
                ("--host", args.host),
                ("--port", args.port),
                ("--path", args.path),
                ("--auth-token", args.auth_token),
                ("--ready-file", args.ready_file),
                ("--idle-timeout-sec", args.idle_timeout_sec),
            )
            if value is not None
        ]
        if invalid_flags:
            parser.error(f"{', '.join(invalid_flags)} require --transport streamable-http")
        return ServerCliConfig(transport="stdio")

    return ServerCliConfig(
        transport="streamable-http",
        host="127.0.0.1" if args.host is None else args.host,
        port=8000 if args.port is None else args.port,
        path="/mcp" if args.path is None else args.path,
        auth_token=(
            args.auth_token if args.auth_token is not None else os.environ.get(_AUTH_TOKEN_ENV)
        ),
        ready_file=Path(args.ready_file) if args.ready_file is not None else None,
        idle_timeout_sec=0.0 if args.idle_timeout_sec is None else args.idle_timeout_sec,
    )


async def main(argv: Sequence[str] | None = None) -> None:
    """Main async entry point for the MCP server."""

    config = parse_server_config(argv)
    runtime = create_default_runtime()

    if config.transport == "stdio":
        await runtime.run_stdio()
        return

    await runtime.run_streamable_http(
        host=config.host,
        port=config.port,
        path=config.path,
        auth_token=config.auth_token,
        ready_file=config.ready_file,
        idle_timeout_sec=config.idle_timeout_sec,
    )


def run_server(argv: Sequence[str] | None = None) -> None:
    """Synchronous entry point for the MCP server."""

    configure_logging()
    _warn_if_shadowed_by_build_lib()
    try:
        asyncio.run(main(argv))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


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
