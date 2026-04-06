# Selectable MCP Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. If the user has expressed a strong preference for one of these execution styles, keep using that style unless they explicitly ask to switch. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Codex subagent-driven execution:** Subagents cannot stream partial progress back to the controller while still running. The controller should assign each subagent a unique shared progress file and inspect that file during execution when visibility is needed.

**Goal:** Add streamable HTTP MCP serving as an explicit CLI-selectable transport while preserving stdio as the zero-argument default.

**Architecture:** Build the HTTP transport as a thin MCP app wrapper in `src/gdb_mcp/mcp/app.py`, then expose explicit runtime methods for stdio and streamable HTTP in `src/gdb_mcp/mcp/runtime.py`. Keep transport selection and validation in `src/gdb_mcp/server.py`, so the same `ServerRuntime`, tool handlers, and `SessionRegistry` back both transports while docs and tests stay aligned with the new CLI.

**Tech Stack:** Python 3.10+, asyncio, argparse, MCP Python SDK low-level server, `StreamableHTTPSessionManager`, Starlette, uvicorn, pytest, ruff, mypy.

---

### File Structure

**Core files and responsibilities**

- Modify: `src/gdb_mcp/mcp/app.py`
  Add the streamable HTTP Starlette app factory, the ASGI adapter that delegates to `StreamableHTTPSessionManager`, and the uvicorn runner helper.
- Modify: `src/gdb_mcp/mcp/runtime.py`
  Add explicit `run_stdio()` and `run_streamable_http()` methods that both reuse the same low-level MCP app and shutdown hook.
- Modify: `src/gdb_mcp/server.py`
  Add CLI parsing and validation for `--transport`, `--host`, `--port`, and `--path`, then route the selected transport through the new runtime methods.
- Create: `tests/mcp/test_app.py`
  Lock in the Starlette route, lifespan cleanup, and uvicorn wiring for the new streamable HTTP helper.
- Modify: `tests/mcp/test_runtime.py`
  Verify runtime delegation to stdio and streamable HTTP helpers without needing live network traffic.
- Modify: `tests/mcp/test_server_entrypoint.py`
  Verify default stdio behavior, explicit streamable HTTP selection, parser validation, and fresh runtime creation per invocation.
- Modify: `README.md`
  Document transport selection and preserve stdio as the primary Claude/Desktop configuration path.
- Modify: `INSTALL.md`
  Add HTTP startup examples and explain when to use stdio versus streamable HTTP.
- Modify: `TOOLS.md`
  Clarify that tool semantics are transport-independent and add the HTTP startup command where transport is discussed.
- Modify: `examples/README.md`
  Explain how to launch the example workflows in HTTP mode.
- Modify: `examples/USAGE_GUIDE.md`
  Clarify that the same tool workflows work over either stdio or streamable HTTP.
- Modify: `skills/debug-with-gdb-mcp/SKILL.md`
  Keep the debugging workflow guidance aligned with the new server startup options.

### Task 1: Add The Streamable HTTP App Helper

**Files:**
- Modify: `src/gdb_mcp/mcp/app.py`
- Create: `tests/mcp/test_app.py`

**Testing approach:** `TDD`
Reason: The HTTP transport wrapper is a clean seam around the MCP SDK. Locking down the route, lifespan cleanup, and uvicorn handoff first prevents runtime and CLI work from baking in the wrong helper shape.

- [ ] **Step 1: Write failing app-layer tests for the new HTTP helper**

```python
# tests/mcp/test_app.py

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

from starlette.applications import Starlette

from gdb_mcp.mcp.app import create_streamable_http_app, run_streamable_http_app


class TestStreamableHttpApp:
    @patch("gdb_mcp.mcp.app.StreamableHTTPSessionManager")
    def test_create_streamable_http_app_registers_route_and_shutdown(
        self,
        mock_session_manager_cls,
    ):
        mcp_app = Mock()
        shutdown = Mock()
        session_manager = Mock()

        @asynccontextmanager
        async def manager_lifespan():
            yield

        session_manager.run.return_value = manager_lifespan()
        mock_session_manager_cls.return_value = session_manager

        app = create_streamable_http_app(
            mcp_app,
            path="/mcp",
            on_shutdown=shutdown,
        )

        assert isinstance(app, Starlette)
        assert [route.path for route in app.routes] == ["/mcp"]

        async def exercise_lifespan() -> None:
            async with app.router.lifespan_context(app):
                pass

        asyncio.run(exercise_lifespan())

        mock_session_manager_cls.assert_called_once_with(app=mcp_app)
        session_manager.run.assert_called_once_with()
        shutdown.assert_called_once_with()

    @patch("gdb_mcp.mcp.app.uvicorn.Server")
    @patch("gdb_mcp.mcp.app.uvicorn.Config")
    @patch("gdb_mcp.mcp.app.create_streamable_http_app")
    def test_run_streamable_http_app_uses_uvicorn(
        self,
        mock_create_streamable_http_app,
        mock_config_cls,
        mock_server_cls,
    ):
        mcp_app = Mock()
        starlette_app = Mock()
        shutdown = Mock()
        uvicorn_server = Mock()
        uvicorn_server.serve = AsyncMock(return_value=None)

        mock_create_streamable_http_app.return_value = starlette_app
        mock_server_cls.return_value = uvicorn_server

        asyncio.run(
            run_streamable_http_app(
                mcp_app,
                host="127.0.0.1",
                port=8000,
                path="/mcp",
                startup_message="GDB MCP Server starting...",
                on_shutdown=shutdown,
            )
        )

        mock_create_streamable_http_app.assert_called_once_with(
            mcp_app,
            path="/mcp",
            on_shutdown=shutdown,
        )
        mock_config_cls.assert_called_once()
        assert mock_config_cls.call_args.args[0] is starlette_app
        assert mock_config_cls.call_args.kwargs["host"] == "127.0.0.1"
        assert mock_config_cls.call_args.kwargs["port"] == 8000
        mock_server_cls.assert_called_once_with(mock_config_cls.return_value)
        uvicorn_server.serve.assert_awaited_once_with()
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_app.py`
Expected: FAIL because `create_streamable_http_app()` and `run_streamable_http_app()` do not exist yet in `src/gdb_mcp/mcp/app.py`.

- [ ] **Step 3: Implement the streamable HTTP app and runner**

```python
# src/gdb_mcp/mcp/app.py

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


class StreamableHTTPASGIApp:
    """Thin ASGI adapter for the MCP streamable HTTP session manager."""

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


async def run_streamable_http_app(
    app: Server,
    *,
    host: str,
    port: int,
    path: str,
    startup_message: str | None = None,
    on_shutdown: Callable[[], None] | None = None,
) -> None:
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
    server = uvicorn.Server(
        uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level=logging.getLevelName(
                logging.getLogger().getEffectiveLevel()
            ).lower(),
        )
    )
    await server.serve()
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_app.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/mcp/app.py tests/mcp/test_app.py
git commit -m "feat: add streamable HTTP MCP app helper"
```

### Task 2: Add Explicit Runtime Transport Runners

**Files:**
- Modify: `src/gdb_mcp/mcp/runtime.py`
- Modify: `tests/mcp/test_runtime.py`

**Testing approach:** `TDD`
Reason: The runtime is the composition root. Adding explicit `run_stdio()` and `run_streamable_http()` methods before touching the CLI keeps transport behavior centralized and independently testable.

- [ ] **Step 1: Write failing runtime tests for stdio and HTTP delegation**

```python
# tests/mcp/test_runtime.py

from unittest.mock import AsyncMock, MagicMock, Mock, patch


class TestServerRuntime:
    @patch("gdb_mcp.mcp.runtime.run_stdio_app", new_callable=AsyncMock)
    def test_run_stdio_delegates_to_stdio_helper(self, mock_run_stdio_app):
        mock_manager = Mock()
        runtime = create_server_runtime(
            session_manager_provider=lambda: mock_manager,
            logger=logging.getLogger("test-runtime"),
        )

        asyncio.run(runtime.run_stdio())

        mock_run_stdio_app.assert_awaited_once_with(
            runtime.app,
            startup_message="GDB MCP Server starting...",
            on_shutdown=runtime.shutdown_sessions,
        )

    @patch("gdb_mcp.mcp.runtime.run_streamable_http_app", new_callable=AsyncMock)
    def test_run_streamable_http_delegates_to_http_helper(
        self,
        mock_run_streamable_http_app,
    ):
        mock_manager = Mock()
        runtime = create_server_runtime(
            session_manager_provider=lambda: mock_manager,
            logger=logging.getLogger("test-runtime"),
        )

        asyncio.run(
            runtime.run_streamable_http(
                host="127.0.0.1",
                port=8000,
                path="/mcp",
            )
        )

        mock_run_streamable_http_app.assert_awaited_once_with(
            runtime.app,
            host="127.0.0.1",
            port=8000,
            path="/mcp",
            startup_message="GDB MCP Server starting...",
            on_shutdown=runtime.shutdown_sessions,
        )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_runtime.py -k 'run_stdio_delegates_to_stdio_helper or run_streamable_http_delegates_to_http_helper'`
Expected: FAIL because `ServerRuntime` only exposes `main()` and `run_server()` today.

- [ ] **Step 3: Implement the explicit runtime methods**

```python
# src/gdb_mcp/mcp/runtime.py

from .app import create_mcp_app, run_stdio_app, run_streamable_http_app

# Add these methods inside the existing ServerRuntime class.

    async def run_stdio(self) -> None:
        await run_stdio_app(
            self.app,
            startup_message=self.startup_message,
            on_shutdown=self.shutdown_sessions,
        )

    async def run_streamable_http(
        self,
        *,
        host: str,
        port: int,
        path: str,
    ) -> None:
        await run_streamable_http_app(
            self.app,
            host=host,
            port=port,
            path=path,
            startup_message=self.startup_message,
            on_shutdown=self.shutdown_sessions,
        )

    async def main(self) -> None:
        await self.run_stdio()

    def run_server(self) -> None:
        asyncio.run(self.run_stdio())
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_runtime.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/mcp/runtime.py tests/mcp/test_runtime.py
git commit -m "refactor: add runtime transport runners"
```

### Task 3: Add CLI Transport Selection And Validation

**Files:**
- Modify: `src/gdb_mcp/server.py`
- Modify: `tests/mcp/test_server_entrypoint.py`

**Testing approach:** `TDD`
Reason: The CLI is the user-visible behavior change. Driving it from parser and dispatch tests ensures the new flags stay backward-compatible with the current stdio default.

- [ ] **Step 1: Write failing entrypoint tests for CLI transport selection**

```python
# tests/mcp/test_server_entrypoint.py

from __future__ import annotations

import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestServerEntrypoint:
    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_defaults_to_stdio_transport(self, mock_create_default_runtime):
        from gdb_mcp.server import main

        runtime = Mock()
        runtime.run_stdio = AsyncMock(return_value=None)
        runtime.run_streamable_http = AsyncMock(return_value=None)
        mock_create_default_runtime.return_value = runtime

        asyncio.run(main([]))

        mock_create_default_runtime.assert_called_once_with()
        runtime.run_stdio.assert_awaited_once_with()
        runtime.run_streamable_http.assert_not_called()

    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_selects_streamable_http_transport(self, mock_create_default_runtime):
        from gdb_mcp.server import main

        runtime = Mock()
        runtime.run_stdio = AsyncMock(return_value=None)
        runtime.run_streamable_http = AsyncMock(return_value=None)
        mock_create_default_runtime.return_value = runtime

        asyncio.run(
            main(
                [
                    "--transport",
                    "streamable-http",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9000",
                    "--path",
                    "/debug",
                ]
            )
        )

        runtime.run_streamable_http.assert_awaited_once_with(
            host="0.0.0.0",
            port=9000,
            path="/debug",
        )
        runtime.run_stdio.assert_not_called()

    def test_parse_server_config_rejects_http_flags_for_stdio(self):
        from gdb_mcp.server import parse_server_config

        with pytest.raises(SystemExit) as exc_info:
            parse_server_config(["--port", "9000"])

        assert exc_info.value.code == 2

    def test_parse_server_config_rejects_non_rooted_http_path(self):
        from gdb_mcp.server import parse_server_config

        with pytest.raises(SystemExit) as exc_info:
            parse_server_config(["--transport", "streamable-http", "--path", "mcp"])

        assert exc_info.value.code == 2

    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_builds_a_fresh_runtime_for_each_invocation(
        self,
        mock_create_default_runtime,
    ):
        from gdb_mcp.server import main

        runtime_one = Mock()
        runtime_one.run_stdio = AsyncMock(return_value=None)
        runtime_one.run_streamable_http = AsyncMock(return_value=None)

        runtime_two = Mock()
        runtime_two.run_stdio = AsyncMock(return_value=None)
        runtime_two.run_streamable_http = AsyncMock(return_value=None)

        mock_create_default_runtime.side_effect = [runtime_one, runtime_two]

        asyncio.run(main([]))
        asyncio.run(main([]))

        assert mock_create_default_runtime.call_count == 2
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_server_entrypoint.py -k 'defaults_to_stdio_transport or selects_streamable_http_transport or rejects_http_flags_for_stdio or rejects_non_rooted_http_path'`
Expected: FAIL because `main()` does not accept argv yet and there is no parser or transport validation in `src/gdb_mcp/server.py`.

- [ ] **Step 3: Implement the CLI parser and transport dispatch**

```python
# src/gdb_mcp/server.py

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TransportKind = Literal["stdio", "streamable-http"]


@dataclass(frozen=True)
class ServerCliConfig:
    transport: TransportKind
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"


def _parse_http_path(value: str) -> str:
    if not value.startswith("/") or "?" in value or "#" in value:
        raise argparse.ArgumentTypeError(
            "--path must start with '/' and cannot contain query strings or fragments"
        )
    return value


def parse_server_config(argv: Sequence[str] | None = None) -> ServerCliConfig:
    parser = argparse.ArgumentParser(prog="gdb-mcp-server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--path", type=_parse_http_path)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        invalid_flags = [
            flag
            for flag, value in (
                ("--host", args.host),
                ("--port", args.port),
                ("--path", args.path),
            )
            if value is not None
        ]
        if invalid_flags:
            parser.error(
                f"{', '.join(invalid_flags)} require --transport streamable-http"
            )
        return ServerCliConfig(transport="stdio")

    return ServerCliConfig(
        transport="streamable-http",
        host=args.host or "127.0.0.1",
        port=args.port or 8000,
        path=args.path or "/mcp",
    )


async def main(argv: Sequence[str] | None = None) -> None:
    config = parse_server_config(argv)
    runtime = create_default_runtime()

    if config.transport == "stdio":
        await runtime.run_stdio()
        return

    await runtime.run_streamable_http(
        host=config.host,
        port=config.port,
        path=config.path,
    )


def run_server(argv: Sequence[str] | None = None) -> None:
    configure_logging()
    _warn_if_shadowed_by_build_lib()
    asyncio.run(main(argv))
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_server_entrypoint.py tests/mcp/test_runtime.py tests/mcp/test_app.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/server.py tests/mcp/test_server_entrypoint.py
git commit -m "feat: add selectable MCP transport CLI"
```

### Task 4: Sync Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `INSTALL.md`
- Modify: `TOOLS.md`
- Modify: `examples/README.md`
- Modify: `examples/USAGE_GUIDE.md`
- Modify: `skills/debug-with-gdb-mcp/SKILL.md`

**Testing approach:** `existing tests + targeted verification`
Reason: The code behavior is already locked in by the MCP server tests. This task keeps all user-facing guidance aligned with the implemented CLI and transport behavior, then finishes with the repo’s required validation commands.

- [ ] **Step 1: Update the primary install and configuration docs**

````markdown
# README.md / INSTALL.md

## Transport Selection

`gdb-mcp-server` still defaults to stdio:

```bash
gdb-mcp-server
```

Use streamable HTTP only when the MCP client expects an HTTP endpoint:

```bash
gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Point HTTP-capable clients at:

```text
http://127.0.0.1:8000/mcp
```

Keep the Claude Desktop config examples on stdio:

```json
{
  "mcpServers": {
    "gdb": {
      "command": "gdb-mcp-server"
    }
  }
}
```
````

- [ ] **Step 2: Update the tool reference, examples, and debugging skill docs**

````markdown
# TOOLS.md / examples/README.md / examples/USAGE_GUIDE.md / skills/debug-with-gdb-mcp/SKILL.md

Add one short transport note near the setup sections:

> The tool payloads and responses in this guide are transport-independent. You can expose the same MCP surface over stdio (the default `gdb-mcp-server` behavior) or over streamable HTTP by starting:
>
> `gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp`

For the skill guide, add a startup reminder such as:

- stdio clients usually launch `gdb-mcp-server` directly
- streamable HTTP clients should connect to a running endpoint such as `http://127.0.0.1:8000/mcp`
````

- [ ] **Step 3: Run a targeted CLI sanity check**

Run: `uv run gdb-mcp-server --help`
Expected: PASS and the help output includes `--transport {stdio,streamable-http}`, `--host`, `--port`, and `--path`.

- [ ] **Step 4: Run the final verification suite**

Run: `uv run ruff check src tests`
Expected: PASS

Run: `uv run mypy src`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

Run: `git diff --check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md INSTALL.md TOOLS.md examples/README.md examples/USAGE_GUIDE.md skills/debug-with-gdb-mcp/SKILL.md
git commit -m "docs: document selectable MCP transports"
```
