"""Helpers for constructing and running the MCP app."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolResult, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from .stdio_input import DaemonStdinLines

if TYPE_CHECKING:
    from anyio import AsyncFile

logger = logging.getLogger(__name__)


def create_mcp_app(
    *,
    list_tools_handler: Callable[[], Awaitable[list[Tool]]],
    call_tool_handler: Callable[[str, object], Awaitable[CallToolResult]],
) -> Server:
    """Create an MCP app and register the provided handlers."""

    app = Server("gdb-mcp-server")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return await list_tools_handler()

    # The SDK's own inputSchema gate is disabled on purpose: the typed request
    # models are the single source of truth, while the published flattened
    # schema is a client-facing summary that drops union alternatives.
    @app.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: object) -> CallToolResult:
        return await call_tool_handler(name, arguments)

    return app


@dataclass
class ServerActivity:
    """Tracks the last HTTP request so the idle watchdog can shut down."""

    last_request: float

    @classmethod
    def now(cls) -> "ServerActivity":
        return cls(last_request=time.monotonic())

    def touch(self) -> None:
        self.last_request = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_request


def _authorized(scope: Scope, token: str | None) -> bool:
    """Return whether one HTTP scope carries the expected bearer token."""

    if not token:
        return True
    provided = dict(scope.get("headers") or ()).get(b"authorization")
    if not isinstance(provided, bytes):
        return False
    return provided.decode("latin-1") == f"Bearer {token}"


class StreamableHTTPASGIApp:
    """ASGI adapter with optional bearer auth and activity tracking."""

    def __init__(
        self,
        session_manager: StreamableHTTPSessionManager,
        *,
        auth_token: str | None = None,
        activity: ServerActivity | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._auth_token = auth_token
        self._activity = activity

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and self._activity is not None:
            self._activity.touch()
        if not _authorized(scope, self._auth_token):
            await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
            return
        await self._session_manager.handle_request(scope, receive, send)


async def _idle_watchdog(
    activity: ServerActivity,
    idle_timeout_sec: float,
    has_active_sessions: Callable[[], bool] | None,
    request_shutdown: Callable[[], None],
) -> None:
    """Request shutdown once the server is idle and no session is alive."""

    while True:
        await asyncio.sleep(max(1.0, min(idle_timeout_sec / 4, 30.0)))
        if activity.idle_seconds() < idle_timeout_sec:
            continue
        if has_active_sessions is not None and has_active_sessions():
            continue
        request_shutdown()
        return


def create_streamable_http_app(
    app: Server,
    *,
    path: str,
    on_shutdown: Callable[[], None] | None = None,
    auth_token: str | None = None,
    idle_timeout_sec: float = 0.0,
    has_active_sessions: Callable[[], bool] | None = None,
    request_shutdown: Callable[[], None] | None = None,
) -> Starlette:
    """Create a Starlette app that serves the MCP app over streamable HTTP."""

    session_manager = StreamableHTTPSessionManager(app=app)
    activity = ServerActivity.now()
    transport_app = StreamableHTTPASGIApp(
        session_manager,
        auth_token=auth_token,
        activity=activity,
    )

    async def shutdown_endpoint(request) -> JSONResponse | PlainTextResponse:
        if not _authorized(request.scope, auth_token):
            return PlainTextResponse("Unauthorized", status_code=401)
        force = request.query_params.get("force") in {"1", "true"}
        if not force and has_active_sessions is not None and has_active_sessions():
            return JSONResponse(
                {
                    "status": "error",
                    "message": "active debug sessions; pass force=1 to stop anyway",
                },
                status_code=409,
            )
        if request_shutdown is not None:
            request_shutdown()
        return JSONResponse({"status": "success"})

    @asynccontextmanager
    async def lifespan(_: Starlette):
        watchdog: asyncio.Task[None] | None = None
        if idle_timeout_sec > 0 and request_shutdown is not None:
            watchdog = asyncio.create_task(
                _idle_watchdog(activity, idle_timeout_sec, has_active_sessions, request_shutdown)
            )
        async with session_manager.run():
            try:
                yield
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if on_shutdown is not None:
                    on_shutdown()

    return Starlette(
        routes=[
            Route(path, endpoint=transport_app),
            Route("/shutdown", endpoint=shutdown_endpoint, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


class _ReadyReportingServer(uvicorn.Server):
    """uvicorn server that reports its bound address once it is serving."""

    def __init__(
        self,
        config: uvicorn.Config,
        *,
        ready_file: Path | None,
        path: str,
    ) -> None:
        super().__init__(config)
        self._ready_file = ready_file
        self._path = path

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        if self._ready_file is None:
            return
        address = self.servers[0].sockets[0].getsockname()
        host, port = address[0], address[1]
        logger.info("Listening on http://%s:%s%s", host, port, self._path)
        self._ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._ready_file.with_suffix(self._ready_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"host": host, "port": port, "path": self._path}),
            encoding="utf-8",
        )
        os.replace(temporary, self._ready_file)


def _daemon_stdin_lines() -> "AsyncFile[str] | None":
    """Return the daemon-backed stdin reader, or None when stdin is not a real fd.

    The SDK's default reader is used as a fallback so environments with a
    replaced stdin keep working; the daemon reader is what makes Ctrl-C exit
    reliably for the normal server entrypoint.
    """

    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    return cast("AsyncFile[str]", DaemonStdinLines(fd))


async def run_stdio_app(
    app: Server,
    *,
    startup_message: str | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> None:
    """Run the MCP app on stdio and invoke cleanup when it exits."""

    from mcp.server.stdio import stdio_server

    # The SDK reads stdin through a non-cancellable worker-thread read, so a
    # Ctrl-C during shutdown would wait on a thread blocked in read() forever.
    async with stdio_server(stdin=_daemon_stdin_lines()) as (read_stream, write_stream):
        if startup_message:
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
    auth_token: str | None = None,
    ready_file: Path | None = None,
    idle_timeout_sec: float = 0.0,
    has_active_sessions: Callable[[], bool] | None = None,
) -> None:
    """Run the MCP app on streamable HTTP via uvicorn."""

    if startup_message:
        logging.getLogger(__name__).info("%s starting", startup_message)

    shutdown_event = asyncio.Event()
    starlette_app = create_streamable_http_app(
        app,
        path=path,
        on_shutdown=on_shutdown,
        auth_token=auth_token,
        idle_timeout_sec=idle_timeout_sec,
        has_active_sessions=has_active_sessions,
        request_shutdown=shutdown_event.set,
    )
    config = uvicorn.Config(
        starlette_app,
        host=host,
        port=port,
        log_level=logging.getLevelName(logging.getLogger().getEffectiveLevel()).lower(),
    )
    server = _ReadyReportingServer(config, ready_file=ready_file, path=path)

    async def watch_shutdown() -> None:
        await shutdown_event.wait()
        server.should_exit = True

    watcher = asyncio.create_task(watch_shutdown())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)
