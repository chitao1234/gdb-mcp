# MCP CLI Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. If the user has expressed a strong preference for one of these execution styles, keep using that style unless they explicitly ask to switch. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Codex subagent-driven execution:** Subagents cannot stream partial progress back to the controller while still running. The controller should assign each subagent a unique shared progress file and inspect that file during execution when visibility is needed.

**Goal:** Add a first-party `gdb-mcp-client` CLI that connects to the streamable HTTP MCP server, exposes the same 17 public tools as one subcommand per tool, accepts command-line flags instead of raw JSON for normal use, and supports both human-oriented and `--json` output.

**Architecture:** Build the client as a small package under `src/gdb_mcp/client/` with four layers: an MCP HTTP runtime helper, shared CLI parsing helpers, a static adapter registry that reuses the existing schema models in `src/gdb_mcp/mcp/schemas.py`, and human renderers. Keep the server unchanged; the client remains a one-shot MCP consumer that validates payloads locally with the same Pydantic models and then calls the mirrored tool over streamable HTTP.

**Tech Stack:** Python 3.10+, asyncio, argparse, MCP Python SDK client (`ClientSession`, `streamable_http_client`), httpx, Pydantic v2 models from `src/gdb_mcp/mcp/schemas.py`, pytest, ruff, mypy.

---

### File Structure

**Core files and responsibilities**

- Create: `src/gdb_mcp/client/__init__.py`
  Export `main()` and `run_client()` so `pyproject.toml` can expose a console script without pulling implementation details into the package root.
- Create: `src/gdb_mcp/client/runtime.py`
  Own the MCP streamable HTTP connection, session initialization, single-tool invocation, and JSON payload extraction from MCP text content.
- Create: `src/gdb_mcp/client/parsers.py`
  Hold shared argparse helpers for repeated string flags, `KEY=VALUE` maps, boolean optional flags, dotted-path assignments, and grouped workflow-step events.
- Create: `src/gdb_mcp/client/renderers.py`
  Convert structured response payloads into human-oriented text while keeping `--json` as a raw passthrough in the CLI layer.
- Create: `src/gdb_mcp/client/specs.py`
  Define the static `ToolCliSpec` registry that maps each public tool name to its schema model, parser configuration, payload builder, and human renderer.
- Create: `src/gdb_mcp/client/cli.py`
  Build the top-level parser, parse global flags, dispatch to the selected `ToolCliSpec`, call the runtime helper, print output, and return the process exit code.
- Create: `tests/mcp/test_client_runtime.py`
  Unit-test the MCP client runtime helper in isolation from argparse and renderers.
- Create: `tests/mcp/test_client_cli.py`
  Unit-test CLI parsing, payload construction, JSON mode, human rendering, grouped workflow flags, and registry coverage.
- Create: `tests/integration/test_client_streamable_http.py`
  Exercise the real streamable HTTP app and the new CLI entrypoint together without needing a live TCP listener.
- Modify: `pyproject.toml`
  Add the `gdb-mcp-client` console script.
- Modify: `README.md`
  Add client overview, launch examples, and clarify the client/server split.
- Modify: `TOOLS.md`
  Add CLI examples that show parity with the public tool inventory.
- Modify: `examples/README.md`
  Show how to use the new CLI against a local streamable HTTP server.
- Modify: `examples/USAGE_GUIDE.md`
  Add command-line examples that mirror a few existing JSON/MCP workflows.

### Task 1: Add The Streamable HTTP Client Runtime Helper

**Files:**
- Create: `src/gdb_mcp/client/runtime.py`
- Create: `tests/mcp/test_client_runtime.py`

**Testing approach:** `TDD`
Reason: The MCP SDK session boundary is a clean seam. Locking down initialization, tool invocation, and JSON payload extraction first gives the rest of the CLI a stable runtime surface.

- [ ] **Step 1: Write failing runtime tests for MCP HTTP invocation and payload parsing**

```python
# tests/mcp/test_client_runtime.py

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.types import CallToolResult, TextContent

from gdb_mcp.client.runtime import invoke_tool


class _AsyncContextManager:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestClientRuntime:
    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_initializes_session_and_returns_json_payload(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='{"status":"success","session_id":7,"message":"started"}',
                    )
                ]
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        result = asyncio.run(
            invoke_tool(
                "http://127.0.0.1:8000/mcp",
                "gdb_session_start",
                {"program": "/bin/true"},
            )
        )

        mock_streamable_http_client.assert_called_once_with(
            "http://127.0.0.1:8000/mcp",
            http_client=None,
        )
        mock_client_session_cls.assert_called_once_with(read_stream, write_stream)
        session.initialize.assert_awaited_once_with()
        session.call_tool.assert_awaited_once_with("gdb_session_start", {"program": "/bin/true"})
        assert result.is_error is False
        assert result.payload["status"] == "success"
        assert result.payload["session_id"] == 7

    @patch("gdb_mcp.client.runtime.ClientSession")
    @patch("gdb_mcp.client.runtime.streamable_http_client")
    def test_invoke_tool_rejects_non_object_json_payload(
        self,
        mock_streamable_http_client,
        mock_client_session_cls,
    ):
        read_stream = Mock()
        write_stream = Mock()
        mock_streamable_http_client.return_value = _AsyncContextManager(
            (read_stream, write_stream, lambda: "session-1")
        )

        session = AsyncMock()
        session.initialize = AsyncMock(return_value=Mock())
        session.call_tool = AsyncMock(
            return_value=CallToolResult(
                content=[TextContent(type="text", text='["not","an","object"]')]
            )
        )
        mock_client_session_cls.return_value = _AsyncContextManager(session)

        with pytest.raises(ValueError, match="Expected tool payload JSON object"):
            asyncio.run(
                invoke_tool(
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_query",
                    {"action": "list", "query": {}},
                )
            )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_runtime.py`
Expected: FAIL because `gdb_mcp.client.runtime` does not exist yet.

- [ ] **Step 3: Implement the HTTP runtime helper**

```python
# src/gdb_mcp/client/runtime.py

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent


@dataclass(frozen=True)
class ClientToolResponse:
    payload: dict[str, object]
    is_error: bool


def _parse_tool_payload(result: CallToolResult) -> dict[str, object]:
    if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
        raise ValueError("Expected exactly one text content item from MCP tool result")

    payload = json.loads(result.content[0].text)
    if not isinstance(payload, dict):
        raise ValueError("Expected tool payload JSON object")

    return cast(dict[str, object], payload)


async def invoke_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ClientToolResponse:
    async with streamable_http_client(server_url, http_client=http_client) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

    payload = _parse_tool_payload(result)
    return ClientToolResponse(
        payload=payload,
        is_error=result.isError or payload.get("status") == "error",
    )
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_runtime.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/runtime.py tests/mcp/test_client_runtime.py
git commit -m "feat: add MCP HTTP client runtime"
```

### Task 2: Add CLI Scaffolding, Shared Parsers, And Dedicated Tool Support

**Files:**
- Create: `src/gdb_mcp/client/__init__.py`
- Create: `src/gdb_mcp/client/cli.py`
- Create: `src/gdb_mcp/client/parsers.py`
- Create: `src/gdb_mcp/client/renderers.py`
- Create: `src/gdb_mcp/client/specs.py`
- Create: `tests/mcp/test_client_cli.py`

**Testing approach:** `TDD`
Reason: The global CLI surface and dedicated-tool payload mapping are clear user-facing behaviors. Driving them from tests keeps the first client commands stable before the action-family adapters are layered in.

- [ ] **Step 1: Write failing CLI tests for `--server-url`, dedicated-tool payload building, and `--json` mode**

```python
# tests/mcp/test_client_cli.py

from __future__ import annotations

import asyncio
import json
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import main, parse_client_args
from gdb_mcp.client.runtime import ClientToolResponse


class TestClientCli:
    def test_parse_client_args_requires_server_url(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_client_args(["gdb_session_start", "--program", "/bin/true"])

        assert exc_info.value.code == 2

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_invokes_session_start_with_flat_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={
                "status": "success",
                "session_id": 7,
                "message": "GDB session started successfully",
                "target_loaded": True,
                "execution_state": "not_started",
            },
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_start",
                    "--program",
                    "/bin/true",
                    "--arg=--mode",
                    "--arg",
                    "fast",
                    "--env",
                    "TERM=dumb",
                    "--init-command",
                    "set pagination off",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_session_start",
            {
                "program": "/bin/true",
                "args": ["--mode", "fast"],
                "init_commands": ["set pagination off"],
                "env": {"TERM": "dumb"},
            },
            http_client=None,
        )
        rendered = stdout.getvalue()
        assert "session_id: 7" in rendered
        assert "target_loaded: True" in rendered

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_json_mode_prints_raw_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "message": "attached"},
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "--json",
                    "gdb_attach_process",
                    "--session-id",
                    "7",
                    "--pid",
                    "1234",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        assert json.loads(stdout.getvalue()) == {"status": "success", "message": "attached"}
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_cli.py -k 'requires_server_url or invokes_session_start_with_flat_flags or json_mode_prints_raw_payload'`
Expected: FAIL because the CLI package, parser helpers, and registry do not exist yet.

- [ ] **Step 3: Implement the client package, shared parsers, dedicated-tool specs, and base renderers**

```python
# src/gdb_mcp/client/parsers.py

from __future__ import annotations

import argparse
from typing import cast

from pydantic import BaseModel


def key_value_entry(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("Expected KEY=VALUE")
    key, value = text.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("Expected non-empty KEY in KEY=VALUE")
    return key, value


def collapse_key_value_entries(entries: list[tuple[str, str]]) -> dict[str, str] | None:
    if not entries:
        return None
    return {key: value for key, value in entries}


def add_boolean_flag(
    parser: argparse.ArgumentParser,
    name: str,
    *,
    default: bool,
    help_text: str,
) -> None:
    parser.add_argument(
        f"--{name.replace('_', '-')}",
        dest=name,
        action=argparse.BooleanOptionalAction,
        default=default,
        help=help_text,
    )


def validate_model_payload(model: type[BaseModel], payload: dict[str, object]) -> dict[str, object]:
    validated = model.model_validate(payload)
    dumped = validated.model_dump(mode="python", exclude_none=True)
    return cast(dict[str, object], dumped)
```

```python
# src/gdb_mcp/client/renderers.py

from __future__ import annotations


def _render_mapping_lines(payload: dict[str, object], *, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(_render_mapping_lines(value, indent=indent + 1))
        else:
            lines.append(f"{prefix}{key}: {value}")
    return lines


def render_mapping(payload: dict[str, object]) -> str:
    return "\n".join(_render_mapping_lines(payload))


def render_session_start(payload: dict[str, object]) -> str:
    summary = {
        "status": payload.get("status"),
        "session_id": payload.get("session_id"),
        "target_loaded": payload.get("target_loaded"),
        "execution_state": payload.get("execution_state"),
        "message": payload.get("message"),
    }
    return render_mapping(summary)
```

```python
# src/gdb_mcp/client/specs.py

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ExecuteCommandArgs,
    StartSessionArgs,
    build_tool_definitions,
)

from .parsers import add_boolean_flag, collapse_key_value_entries, key_value_entry, validate_model_payload
from .renderers import render_mapping, render_session_start


@dataclass(frozen=True)
class ToolCliSpec:
    name: str
    model: type[BaseModel]
    configure_parser: Callable[[argparse.ArgumentParser], None]
    build_arguments: Callable[[argparse.Namespace], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


TOOL_DESCRIPTIONS = {tool.name: tool.description or "" for tool in build_tool_definitions()}


def _configure_session_start(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--program")
    parser.add_argument("--arg", dest="args", action="append", default=[])
    parser.add_argument("--init-command", dest="init_commands", action="append", default=[])
    parser.add_argument("--env", dest="env", action="append", type=key_value_entry, default=[])
    parser.add_argument("--gdb-path")
    parser.add_argument("--working-dir")
    parser.add_argument("--core")


def _build_session_start(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {
        "program": namespace.program,
        "args": namespace.args or None,
        "init_commands": namespace.init_commands or None,
        "env": collapse_key_value_entries(namespace.env),
        "gdb_path": namespace.gdb_path,
        "working_dir": namespace.working_dir,
        "core": namespace.core,
    }
    return validate_model_payload(StartSessionArgs, payload)


def _configure_session_id_and_timeout(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--timeout-sec", type=int, default=30)


def _configure_execute_command(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--command", required=True)


def _build_execute_command(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        ExecuteCommandArgs,
        {
            "session_id": namespace.session_id,
            "command": namespace.command,
            "timeout_sec": namespace.timeout_sec,
        },
    )


def _configure_attach_process(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--pid", type=int, required=True)


def _build_attach_process(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        AttachProcessArgs,
        {
            "session_id": namespace.session_id,
            "pid": namespace.pid,
            "timeout_sec": namespace.timeout_sec,
        },
    )


def _configure_call_function(parser: argparse.ArgumentParser) -> None:
    _configure_session_id_and_timeout(parser)
    parser.add_argument("--expression", required=True)


def _build_call_function(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        CallFunctionArgs,
        {
            "session_id": namespace.session_id,
            "expression": namespace.expression,
            "timeout_sec": namespace.timeout_sec,
        },
    )


def _configure_capture_bundle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--bundle-name")
    parser.add_argument("--expression", dest="expressions", action="append", default=[])
    parser.add_argument("--memory-range", dest="memory_ranges", action="append", default=[])
    parser.add_argument("--max-frames", type=int, default=100)
    add_boolean_flag(parser, "include_threads", default=True, help_text="Capture thread inventory")
    add_boolean_flag(parser, "include_backtraces", default=True, help_text="Capture thread backtraces")
    add_boolean_flag(parser, "include_frame", default=True, help_text="Capture current frame")
    add_boolean_flag(parser, "include_variables", default=True, help_text="Capture variables")
    add_boolean_flag(parser, "include_registers", default=True, help_text="Capture registers")
    add_boolean_flag(parser, "include_transcript", default=True, help_text="Capture transcript")
    add_boolean_flag(parser, "include_stop_history", default=True, help_text="Capture stop history")


def _build_capture_bundle(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        CaptureBundleArgs,
        {
            "session_id": namespace.session_id,
            "output_dir": namespace.output_dir,
            "bundle_name": namespace.bundle_name,
            "expressions": namespace.expressions,
            "memory_ranges": namespace.memory_ranges,
            "max_frames": namespace.max_frames,
            "include_threads": namespace.include_threads,
            "include_backtraces": namespace.include_backtraces,
            "include_frame": namespace.include_frame,
            "include_variables": namespace.include_variables,
            "include_registers": namespace.include_registers,
            "include_transcript": namespace.include_transcript,
            "include_stop_history": namespace.include_stop_history,
        },
    )


CLIENT_TOOL_SPECS: dict[str, ToolCliSpec] = {
    "gdb_session_start": ToolCliSpec(
        name="gdb_session_start",
        model=StartSessionArgs,
        configure_parser=_configure_session_start,
        build_arguments=_build_session_start,
        render_human=render_session_start,
    ),
    "gdb_execute_command": ToolCliSpec(
        name="gdb_execute_command",
        model=ExecuteCommandArgs,
        configure_parser=_configure_execute_command,
        build_arguments=_build_execute_command,
        render_human=render_mapping,
    ),
    "gdb_attach_process": ToolCliSpec(
        name="gdb_attach_process",
        model=AttachProcessArgs,
        configure_parser=_configure_attach_process,
        build_arguments=_build_attach_process,
        render_human=render_mapping,
    ),
    "gdb_call_function": ToolCliSpec(
        name="gdb_call_function",
        model=CallFunctionArgs,
        configure_parser=_configure_call_function,
        build_arguments=_build_call_function,
        render_human=render_mapping,
    ),
    "gdb_capture_bundle": ToolCliSpec(
        name="gdb_capture_bundle",
        model=CaptureBundleArgs,
        configure_parser=_configure_capture_bundle,
        build_arguments=_build_capture_bundle,
        render_human=render_mapping,
    ),
}
```

```python
# src/gdb_mcp/client/cli.py

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import TextIO

import httpx

from .runtime import invoke_tool
from .specs import CLIENT_TOOL_SPECS, TOOL_DESCRIPTIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gdb-mcp-client")
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--json", action="store_true")

    subparsers = parser.add_subparsers(dest="tool_name", required=True)
    for tool_name, spec in CLIENT_TOOL_SPECS.items():
        subparser = subparsers.add_parser(
            tool_name,
            help=TOOL_DESCRIPTIONS.get(tool_name, ""),
            description=TOOL_DESCRIPTIONS.get(tool_name, ""),
        )
        spec.configure_parser(subparser)

    return parser


def parse_client_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


async def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    args = parse_client_args(argv)
    spec = CLIENT_TOOL_SPECS[args.tool_name]
    payload = spec.build_arguments(args)
    response = await invoke_tool(
        args.server_url,
        args.tool_name,
        payload,
        http_client=http_client,
    )

    if args.json:
        output.write(json.dumps(response.payload, indent=2) + "\n")
    else:
        output.write(spec.render_human(response.payload) + "\n")

    return 1 if response.is_error else 0


def run_client(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(asyncio.run(main(argv)))
```

```python
# src/gdb_mcp/client/__init__.py

from .cli import main, run_client

__all__ = ["main", "run_client"]
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_runtime.py tests/mcp/test_client_cli.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/__init__.py src/gdb_mcp/client/cli.py src/gdb_mcp/client/parsers.py src/gdb_mcp/client/renderers.py src/gdb_mcp/client/specs.py tests/mcp/test_client_cli.py
git commit -m "feat: add CLI client scaffolding for dedicated tools"
```

### Task 3: Add Static Adapters For Session, Inferior, Execution, And Context Action Tools

**Files:**
- Modify: `src/gdb_mcp/client/parsers.py`
- Modify: `src/gdb_mcp/client/renderers.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `tests/mcp/test_client_cli.py`

**Testing approach:** `TDD`
Reason: These action families share the same nested-envelope pattern and are good candidates for one generic helper. Driving them from tests prevents the static adapter layer from drifting away from the server schemas.

- [ ] **Step 1: Write failing CLI tests for action-based envelopes and action rendering**

```python
# Add these tests to tests/mcp/test_client_cli.py

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_execution_run_action_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "run", "result": {"execution_state": "paused"}},
            is_error=False,
        )

        stdout = StringIO()
        exit_code = asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_execution_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "run",
                    "--arg=--mode",
                    "--arg",
                    "fast",
                    "--wait-until",
                    "stop",
                    "--wait-timeout-sec",
                    "30",
                ],
                stdout=stdout,
            )
        )

        assert exit_code == 0
        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_execution_manage",
            {
                "session_id": 7,
                "action": "run",
                "execution": {
                    "args": ["--mode", "fast"],
                    "wait": {"until": "stop", "timeout_sec": 30},
                },
            },
            http_client=None,
        )
        assert "action: run" in stdout.getvalue()
        assert "execution_state: paused" in stdout.getvalue()

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_session_query_status_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "status", "result": {"is_running": False}},
            is_error=False,
        )

        asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_session_query",
                    "--session-id",
                    "7",
                    "--action",
                    "status",
                ]
            )
        )

        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_session_query",
            {"session_id": 7, "action": "status", "query": {}},
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_context_query_backtrace_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "backtrace", "result": {"count": 3}},
            is_error=False,
        )

        asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_context_query",
                    "--session-id",
                    "7",
                    "--action",
                    "backtrace",
                    "--thread-id",
                    "2",
                    "--max-frames",
                    "10",
                ]
            )
        )

        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_context_query",
            {
                "session_id": 7,
                "action": "backtrace",
                "query": {"thread_id": 2, "max_frames": 10},
            },
            http_client=None,
        )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_cli.py -k 'execution_run_action_payload or session_query_status_payload or context_query_backtrace_payload'`
Expected: FAIL because the action-family tool specs and nested payload builders do not exist yet.

- [ ] **Step 3: Implement reusable action helpers and add the session, inferior, execution, and context specs**

```python
# src/gdb_mcp/client/renderers.py

def render_action_payload(payload: dict[str, object]) -> str:
    result = payload.get("result")
    if isinstance(result, dict):
        merged = {
            "status": payload.get("status"),
            "action": payload.get("action"),
            **result,
        }
        return render_mapping(merged)
    return render_mapping(payload)
```

```python
# src/gdb_mcp/client/specs.py

from dataclasses import dataclass

from gdb_mcp.mcp.schemas import (
    ContextManageArgs,
    ContextQueryArgs,
    EmptyQuery,
    ExecutionManageArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    SessionManageArgs,
    SessionQueryArgs,
)

from .renderers import render_action_payload


@dataclass(frozen=True)
class ActionVariant:
    payload_field: str
    build_payload: Callable[[argparse.Namespace], dict[str, object]]


def _add_session_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)


def _add_action(parser: argparse.ArgumentParser, *, choices: list[str]) -> None:
    parser.add_argument("--action", required=True, choices=choices)


def _build_action_arguments(
    namespace: argparse.Namespace,
    *,
    model: type[BaseModel],
    variants: dict[str, ActionVariant],
) -> dict[str, object]:
    variant = variants[namespace.action]
    payload: dict[str, object] = {
        "session_id": namespace.session_id,
        "action": namespace.action,
        variant.payload_field: variant.build_payload(namespace),
    }
    return validate_model_payload(model, payload)


def _empty_payload(_: argparse.Namespace) -> dict[str, object]:
    return {}


def _execution_wait(namespace: argparse.Namespace) -> dict[str, object] | None:
    if namespace.wait_until is None and namespace.wait_timeout_sec is None:
        return None

    payload: dict[str, object] = {}
    if namespace.wait_until is not None:
        payload["until"] = namespace.wait_until
    if namespace.wait_timeout_sec is not None:
        payload["timeout_sec"] = namespace.wait_timeout_sec
    return payload


def _execution_run_payload(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {}
    if namespace.args:
        payload["args"] = namespace.args
    wait_payload = _execution_wait(namespace)
    if wait_payload is not None:
        payload["wait"] = wait_payload
    return payload


def _execution_control_payload(namespace: argparse.Namespace) -> dict[str, object]:
    wait_payload = _execution_wait(namespace)
    return {} if wait_payload is None else {"wait": wait_payload}


def _execution_wait_for_stop_payload(namespace: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {"timeout_sec": namespace.timeout_sec}
    if namespace.stop_reasons:
        payload["stop_reasons"] = namespace.stop_reasons
    return payload


def _configure_session_query(parser: argparse.ArgumentParser) -> None:
    _add_action(parser, choices=["list", "status"])
    parser.add_argument("--session-id", type=int)


def _build_session_query(namespace: argparse.Namespace) -> dict[str, object]:
    if namespace.action == "list":
        return validate_model_payload(SessionQueryArgs, {"action": "list", "query": {}})
    return validate_model_payload(
        SessionQueryArgs,
        {"session_id": namespace.session_id, "action": "status", "query": {}},
    )


def _configure_session_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["stop"])


def _build_session_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        SessionManageArgs,
        {"session_id": namespace.session_id, "action": "stop", "session": {}},
    )


def _configure_inferior_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["list", "current"])


def _build_inferior_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=InferiorQueryArgs,
        variants={
            "list": ActionVariant(payload_field="query", build_payload=_empty_payload),
            "current": ActionVariant(payload_field="query", build_payload=_empty_payload),
        },
    )


def _configure_inferior_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(
        parser,
        choices=["create", "remove", "select", "set_follow_fork_mode", "set_detach_on_fork"],
    )
    parser.add_argument("--inferior-id", type=int)
    parser.add_argument("--executable")
    add_boolean_flag(parser, "make_current", default=False, help_text="Select the inferior after create")
    parser.add_argument("--mode", choices=["parent", "child"])
    add_boolean_flag(parser, "enabled", default=True, help_text="Detach from the non-followed fork")


def _build_inferior_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=InferiorManageArgs,
        variants={
            "create": ActionVariant(
                payload_field="inferior",
                build_payload=lambda ns: {
                    "executable": ns.executable,
                    "make_current": ns.make_current,
                },
            ),
            "remove": ActionVariant(
                payload_field="inferior",
                build_payload=lambda ns: {"inferior_id": ns.inferior_id},
            ),
            "select": ActionVariant(
                payload_field="inferior",
                build_payload=lambda ns: {"inferior_id": ns.inferior_id},
            ),
            "set_follow_fork_mode": ActionVariant(
                payload_field="inferior",
                build_payload=lambda ns: {"mode": ns.mode},
            ),
            "set_detach_on_fork": ActionVariant(
                payload_field="inferior",
                build_payload=lambda ns: {"enabled": ns.enabled},
            ),
        },
    )


def _configure_execution_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(
        parser,
        choices=["run", "continue", "interrupt", "step", "next", "finish", "wait_for_stop"],
    )
    parser.add_argument("--arg", dest="args", action="append", default=[])
    parser.add_argument("--wait-until", choices=["acknowledged", "stop"])
    parser.add_argument("--wait-timeout-sec", type=int)
    parser.add_argument("--timeout-sec", type=int, default=30)
    parser.add_argument("--stop-reason", dest="stop_reasons", action="append", default=[])


def _build_execution_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=ExecutionManageArgs,
        variants={
            "run": ActionVariant(payload_field="execution", build_payload=_execution_run_payload),
            "continue": ActionVariant(payload_field="execution", build_payload=_execution_control_payload),
            "interrupt": ActionVariant(payload_field="execution", build_payload=_empty_payload),
            "step": ActionVariant(payload_field="execution", build_payload=_execution_control_payload),
            "next": ActionVariant(payload_field="execution", build_payload=_execution_control_payload),
            "finish": ActionVariant(payload_field="execution", build_payload=_execution_control_payload),
            "wait_for_stop": ActionVariant(
                payload_field="execution",
                build_payload=_execution_wait_for_stop_payload,
            ),
        },
    )


def _configure_context_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["threads", "backtrace", "frame"])
    parser.add_argument("--thread-id", type=int)
    parser.add_argument("--frame", type=int)
    parser.add_argument("--max-frames", type=int, default=100)


def _build_context_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=ContextQueryArgs,
        variants={
            "threads": ActionVariant(payload_field="query", build_payload=_empty_payload),
            "backtrace": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "thread_id": ns.thread_id,
                    "max_frames": ns.max_frames,
                },
            ),
            "frame": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "thread_id": ns.thread_id,
                    "frame": ns.frame,
                },
            ),
        },
    )


def _configure_context_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["select_thread", "select_frame"])
    parser.add_argument("--thread-id", type=int)
    parser.add_argument("--frame", type=int)


def _build_context_manage(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=ContextManageArgs,
        variants={
            "select_thread": ActionVariant(
                payload_field="context",
                build_payload=lambda ns: {"thread_id": ns.thread_id},
            ),
            "select_frame": ActionVariant(
                payload_field="context",
                build_payload=lambda ns: {"frame": ns.frame},
            ),
        },
    )


CLIENT_TOOL_SPECS.update(
    {
        "gdb_session_query": ToolCliSpec(
            name="gdb_session_query",
            model=SessionQueryArgs,
            configure_parser=_configure_session_query,
            build_arguments=_build_session_query,
            render_human=render_action_payload,
        ),
        "gdb_session_manage": ToolCliSpec(
            name="gdb_session_manage",
            model=SessionManageArgs,
            configure_parser=_configure_session_manage,
            build_arguments=_build_session_manage,
            render_human=render_action_payload,
        ),
        "gdb_inferior_query": ToolCliSpec(
            name="gdb_inferior_query",
            model=InferiorQueryArgs,
            configure_parser=_configure_inferior_query,
            build_arguments=_build_inferior_query,
            render_human=render_action_payload,
        ),
        "gdb_inferior_manage": ToolCliSpec(
            name="gdb_inferior_manage",
            model=InferiorManageArgs,
            configure_parser=_configure_inferior_manage,
            build_arguments=_build_inferior_manage,
            render_human=render_action_payload,
        ),
        "gdb_execution_manage": ToolCliSpec(
            name="gdb_execution_manage",
            model=ExecutionManageArgs,
            configure_parser=_configure_execution_manage,
            build_arguments=_build_execution_manage,
            render_human=render_action_payload,
        ),
        "gdb_context_query": ToolCliSpec(
            name="gdb_context_query",
            model=ContextQueryArgs,
            configure_parser=_configure_context_query,
            build_arguments=_build_context_query,
            render_human=render_action_payload,
        ),
        "gdb_context_manage": ToolCliSpec(
            name="gdb_context_manage",
            model=ContextManageArgs,
            configure_parser=_configure_context_manage,
            build_arguments=_build_context_manage,
            render_human=render_action_payload,
        ),
    }
)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_runtime.py tests/mcp/test_client_cli.py -k 'execution_run_action_payload or session_query_status_payload or context_query_backtrace_payload or invokes_session_start_with_flat_flags or json_mode_prints_raw_payload'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/parsers.py src/gdb_mcp/client/renderers.py src/gdb_mcp/client/specs.py tests/mcp/test_client_cli.py
git commit -m "feat: add action tool support to CLI client"
```

### Task 4: Add Breakpoint, Inspect, Workflow, And Campaign Tool Coverage Plus HTTP Integration

**Files:**
- Modify: `src/gdb_mcp/client/parsers.py`
- Modify: `src/gdb_mcp/client/renderers.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `tests/mcp/test_client_cli.py`
- Create: `tests/integration/test_client_streamable_http.py`

**Testing approach:** `TDD`
Reason: These are the highest-complexity adapters because they include discriminated unions, grouped workflow steps, and nested prefixed sections. The integration test then proves the completed CLI can invoke the real streamable HTTP app without inventing a second transport layer.

- [ ] **Step 1: Write failing unit and integration tests for the remaining tool families and full inventory coverage**

```python
# Add these tests to tests/mcp/test_client_cli.py

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_breakpoint_create_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "create", "result": {"breakpoint": {"number": "1"}}},
            is_error=False,
        )

        asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_breakpoint_manage",
                    "--session-id",
                    "7",
                    "--action",
                    "create",
                    "--breakpoint-kind",
                    "code",
                    "--location",
                    "main",
                    "--temporary",
                ]
            )
        )

        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_breakpoint_manage",
            {
                "session_id": 7,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main", "temporary": True},
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_inspect_source_location_payload(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "action": "source", "result": {"line_start": 40}},
            is_error=False,
        )

        asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_inspect_query",
                    "--session-id",
                    "7",
                    "--action",
                    "source",
                    "--location-kind",
                    "file-line",
                    "--file",
                    "src/main.c",
                    "--line",
                    "42",
                    "--context-before",
                    "2",
                    "--context-after",
                    "3",
                ]
            )
        )

        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_inspect_query",
            {
                "session_id": 7,
                "action": "source",
                "query": {
                    "location": {"kind": "file_line", "file": "src/main.c", "line": 42},
                    "context_before": 2,
                    "context_after": 3,
                },
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_workflow_batch_from_grouped_step_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "count": 2, "error_count": 0},
            is_error=False,
        )

        asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_workflow_batch",
                    "--session-id",
                    "7",
                    "--step",
                    "gdb_context_query",
                    "--step-label",
                    "stack",
                    "--step-arg",
                    "action=backtrace",
                    "--step-arg",
                    "query.max_frames=20",
                    "--step",
                    "gdb_inspect_query",
                    "--step-arg",
                    "action=evaluate",
                    "--step-arg",
                    "query.expression=counter",
                    "--no-fail-fast",
                ]
            )
        )

        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_workflow_batch",
            {
                "session_id": 7,
                "steps": [
                    {
                        "tool": "gdb_context_query",
                        "label": "stack",
                        "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
                    },
                    {
                        "tool": "gdb_inspect_query",
                        "arguments": {"action": "evaluate", "query": {"expression": "counter"}},
                    },
                ],
                "fail_fast": False,
                "capture_stop_events": True,
            },
            http_client=None,
        )

    @patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
    def test_main_builds_run_until_failure_with_prefixed_flags(self, mock_invoke_tool):
        mock_invoke_tool.return_value = ClientToolResponse(
            payload={"status": "success", "matched_failure": False, "iterations_completed": 1},
            is_error=False,
        )

        asyncio.run(
            main(
                [
                    "--server-url",
                    "http://127.0.0.1:8000/mcp",
                    "gdb_run_until_failure",
                    "--startup-program",
                    "/bin/true",
                    "--startup-init-command",
                    "set pagination off",
                    "--max-iterations",
                    "2",
                    "--run-timeout-sec",
                    "15",
                    "--failure-stop-reason",
                    "signal-received",
                    "--capture-expression",
                    "errno",
                    "--capture-memory-range",
                    "stack=0x7fffffffe000:64",
                ]
            )
        )

        mock_invoke_tool.assert_awaited_once_with(
            "http://127.0.0.1:8000/mcp",
            "gdb_run_until_failure",
            {
                "startup": {
                    "program": "/bin/true",
                    "init_commands": ["set pagination off"],
                },
                "setup_steps": [],
                "run_timeout_sec": 15,
                "max_iterations": 2,
                "failure": {"stop_reasons": ["signal-received"]},
                "capture": {
                    "expressions": ["errno"],
                    "memory_ranges": ["stack=0x7fffffffe000:64"],
                },
            },
            http_client=None,
        )

    def test_client_tool_specs_cover_public_tool_inventory(self):
        from gdb_mcp.client.specs import CLIENT_TOOL_SPECS
        from gdb_mcp.mcp.schemas import build_tool_definitions

        assert set(CLIENT_TOOL_SPECS) == {tool.name for tool in build_tool_definitions()}
```

```python
# tests/integration/test_client_streamable_http.py

from __future__ import annotations

import asyncio
import json
from io import StringIO

import httpx
import pytest

from gdb_mcp.client.cli import main
from gdb_mcp.mcp.app import create_streamable_http_app


@pytest.mark.integration
def test_cli_calls_real_streamable_http_app(integration_runtime):
    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    stdout = StringIO()

    async def exercise() -> tuple[int, dict[str, object]]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                exit_code = await main(
                    [
                        "--server-url",
                        "http://testserver/mcp",
                        "--json",
                        "gdb_session_query",
                        "--action",
                        "list",
                    ],
                    stdout=stdout,
                    http_client=http_client,
                )
        return exit_code, json.loads(stdout.getvalue())

    exit_code, payload = asyncio.run(exercise())

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["action"] == "list"
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_cli.py -k 'breakpoint_create_payload or inspect_source_location_payload or workflow_batch_from_grouped_step_flags or run_until_failure_with_prefixed_flags or tool_specs_cover_public_tool_inventory' tests/integration/test_client_streamable_http.py`
Expected: FAIL because the breakpoint, inspect, workflow, and campaign adapters do not exist yet and the registry does not cover all 17 public tools.

- [ ] **Step 3: Implement the remaining adapters, grouped workflow parsing, and integration seam**

```python
# src/gdb_mcp/client/parsers.py

class AppendTaggedValue(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        events = getattr(namespace, self.dest, None)
        if events is None:
            events = []
            setattr(namespace, self.dest, events)
        events.append((option_string or self.option_strings[0], values))


def coerce_scalar(text: str) -> object:
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(text)
    except ValueError:
        return text


def dotted_assignment(text: str) -> tuple[str, object]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("Expected PATH=VALUE")
    path, raw_value = text.split("=", 1)
    if not path:
        raise argparse.ArgumentTypeError("Expected non-empty PATH in PATH=VALUE")
    return path, coerce_scalar(raw_value)


def assign_dotted_value(target: dict[str, object], path: str, value: object) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value
```

```python
# src/gdb_mcp/client/specs.py

from gdb_mcp.mcp.schemas import (
    BatchArgs,
    BatchStepArgs,
    BreakpointManageArgs,
    BreakpointQueryArgs,
    InspectQueryArgs,
    RunUntilFailureArgs,
)
from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS

from .parsers import AppendTaggedValue, assign_dotted_value, dotted_assignment


def _build_location(namespace: argparse.Namespace) -> dict[str, object]:
    kind = namespace.location_kind
    if kind == "current":
        return {"kind": "current"}
    if kind == "function":
        return {"kind": "function", "function": namespace.function}
    if kind == "address":
        return {"kind": "address", "address": namespace.address}
    if kind == "address-range":
        return {
            "kind": "address_range",
            "start_address": namespace.start_address,
            "end_address": namespace.end_address,
        }
    if kind == "file-line":
        return {"kind": "file_line", "file": namespace.file, "line": namespace.line}
    return {
        "kind": "file_range",
        "file": namespace.file,
        "start_line": namespace.start_line,
        "end_line": namespace.end_line,
    }


def _build_context_override(namespace: argparse.Namespace) -> dict[str, object] | None:
    payload: dict[str, object] = {}
    if getattr(namespace, "thread_id", None) is not None:
        payload["thread_id"] = namespace.thread_id
    if getattr(namespace, "frame", None) is not None:
        payload["frame"] = namespace.frame
    return payload or None


def _configure_breakpoint_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["create", "update", "delete", "enable", "disable"])
    parser.add_argument("--breakpoint-kind", choices=["code", "watch", "catch"])
    parser.add_argument("--location")
    parser.add_argument("--expression")
    parser.add_argument("--access", choices=["write", "read", "access"], default="write")
    parser.add_argument("--event", choices=["throw", "rethrow", "catch", "exec", "fork", "vfork", "load", "unload", "signal", "syscall"])
    parser.add_argument("--argument")
    add_boolean_flag(parser, "temporary", default=False, help_text="Create a temporary breakpoint")
    parser.add_argument("--number", type=int)
    parser.add_argument("--condition")
    add_boolean_flag(parser, "clear_condition", default=False, help_text="Clear breakpoint condition")


def _build_breakpoint_manage(namespace: argparse.Namespace) -> dict[str, object]:
    def create_payload(ns: argparse.Namespace) -> dict[str, object]:
        if ns.breakpoint_kind == "code":
            payload: dict[str, object] = {"kind": "code", "location": ns.location}
            if ns.condition is not None:
                payload["condition"] = ns.condition
            if ns.temporary:
                payload["temporary"] = True
            return payload
        if ns.breakpoint_kind == "watch":
            return {"kind": "watch", "expression": ns.expression, "access": ns.access}
        payload = {"kind": "catch", "event": ns.event}
        if ns.argument is not None:
            payload["argument"] = ns.argument
        if ns.temporary:
            payload["temporary"] = True
        return payload

    if namespace.action == "create":
        return validate_model_payload(
            BreakpointManageArgs,
            {
                "session_id": namespace.session_id,
                "action": "create",
                "breakpoint": create_payload(namespace),
            },
        )
    if namespace.action == "update":
        return validate_model_payload(
            BreakpointManageArgs,
            {
                "session_id": namespace.session_id,
                "action": "update",
                "breakpoint": {"number": namespace.number},
                "changes": {
                    "condition": namespace.condition,
                    "clear_condition": namespace.clear_condition,
                },
            },
        )
    return validate_model_payload(
        BreakpointManageArgs,
        {
            "session_id": namespace.session_id,
            "action": namespace.action,
            "breakpoint": {"number": namespace.number},
        },
    )


def _configure_breakpoint_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["list", "get"])
    parser.add_argument("--number", type=int)
    parser.add_argument("--kind", dest="kinds", action="append", choices=["code", "watch", "catch"], default=[])
    parser.add_argument("--enabled", choices=["true", "false"])


def _build_breakpoint_query(namespace: argparse.Namespace) -> dict[str, object]:
    enabled = None
    if namespace.enabled is not None:
        enabled = namespace.enabled == "true"
    return _build_action_arguments(
        namespace,
        model=BreakpointQueryArgs,
        variants={
            "list": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {"kinds": ns.kinds, "enabled": enabled},
            ),
            "get": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {"number": ns.number},
            ),
        },
    )


def _configure_inspect_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=["evaluate", "variables", "registers", "memory", "disassembly", "source"])
    parser.add_argument("--thread-id", type=int)
    parser.add_argument("--frame", type=int)
    parser.add_argument("--expression")
    parser.add_argument("--register-number", dest="register_numbers", action="append", default=[])
    parser.add_argument("--register-name", dest="register_names", action="append", default=[])
    add_boolean_flag(parser, "include_vector_registers", default=True, help_text="Include vector registers")
    parser.add_argument("--max-registers", type=int)
    parser.add_argument("--value-format", choices=["hex", "natural"], default="hex")
    parser.add_argument("--address")
    parser.add_argument("--count", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--location-kind", choices=["current", "function", "address", "address-range", "file-line", "file-range"])
    parser.add_argument("--function")
    parser.add_argument("--start-address")
    parser.add_argument("--end-address")
    parser.add_argument("--file")
    parser.add_argument("--line", type=int)
    parser.add_argument("--start-line", type=int)
    parser.add_argument("--end-line", type=int)
    parser.add_argument("--instruction-count", type=int, default=32)
    parser.add_argument("--mode", choices=["assembly", "mixed"], default="mixed")
    parser.add_argument("--context-before", type=int, default=5)
    parser.add_argument("--context-after", type=int, default=5)


def _build_inspect_query(namespace: argparse.Namespace) -> dict[str, object]:
    return _build_action_arguments(
        namespace,
        model=InspectQueryArgs,
        variants={
            "evaluate": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "context": _build_context_override(ns),
                    "expression": ns.expression,
                },
            ),
            "variables": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {"context": _build_context_override(ns)},
            ),
            "registers": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "context": _build_context_override(ns),
                    "register_numbers": ns.register_numbers,
                    "register_names": ns.register_names,
                    "include_vector_registers": ns.include_vector_registers,
                    "max_registers": ns.max_registers,
                    "value_format": ns.value_format,
                },
            ),
            "memory": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "address": ns.address,
                    "count": ns.count,
                    "offset": ns.offset,
                },
            ),
            "disassembly": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "context": _build_context_override(ns),
                    "location": _build_location(ns),
                    "instruction_count": ns.instruction_count,
                    "mode": ns.mode,
                },
            ),
            "source": ActionVariant(
                payload_field="query",
                build_payload=lambda ns: {
                    "context": _build_context_override(ns),
                    "location": _build_location(ns),
                    "context_before": ns.context_before,
                    "context_after": ns.context_after,
                },
            ),
        },
    )


def _configure_workflow_batch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--step", dest="step_events", action=AppendTaggedValue)
    parser.add_argument("--step-label", dest="step_events", action=AppendTaggedValue)
    parser.add_argument("--step-arg", dest="step_events", action=AppendTaggedValue, type=dotted_assignment)
    add_boolean_flag(parser, "fail_fast", default=True, help_text="Stop on first error")
    add_boolean_flag(parser, "capture_stop_events", default=True, help_text="Capture stop events")


def _build_step_list(step_events: list[tuple[str, object]] | None) -> list[dict[str, object]]:
    def validate_step(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool_name == "gdb_session_query" and arguments.get("action") == "list":
            raise ValueError("gdb_session_query(action=list) is not valid inside workflow steps")
        if tool_name in {"gdb_session_manage", "gdb_workflow_batch", "gdb_run_until_failure"}:
            raise ValueError(f"{tool_name} is not valid inside workflow steps")

        tool_spec = SESSION_TOOL_SPECS.get(tool_name)
        if tool_spec is None:
            raise ValueError(f"Unsupported workflow step tool: {tool_name}")

        step_payload = {"session_id": 1, **arguments}
        validated = validate_model_payload(tool_spec.model, step_payload)
        validated.pop("session_id", None)
        return validated

    steps: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for option, value in step_events or []:
        if option == "--step":
            current = {"tool": value, "arguments": {}}
            steps.append(current)
            continue
        if current is None:
            raise ValueError("Step metadata requires a preceding --step")
        if option == "--step-label":
            current["label"] = value
            continue
        if option == "--step-arg":
            path, scalar = value
            assign_dotted_value(current["arguments"], path, scalar)
    for step in steps:
        step["arguments"] = validate_step(str(step["tool"]), step["arguments"])
    return steps


def _build_workflow_batch(namespace: argparse.Namespace) -> dict[str, object]:
    payload = {
        "session_id": namespace.session_id,
        "steps": _build_step_list(namespace.step_events),
        "fail_fast": namespace.fail_fast,
        "capture_stop_events": namespace.capture_stop_events,
    }
    return validate_model_payload(BatchArgs, payload)


def _configure_run_until_failure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--startup-program")
    parser.add_argument("--startup-arg", dest="startup_args", action="append", default=[])
    parser.add_argument("--startup-init-command", dest="startup_init_commands", action="append", default=[])
    parser.add_argument("--startup-env", dest="startup_env", action="append", type=key_value_entry, default=[])
    parser.add_argument("--setup-step", dest="step_events", action=AppendTaggedValue)
    parser.add_argument("--setup-step-label", dest="step_events", action=AppendTaggedValue)
    parser.add_argument("--setup-step-arg", dest="step_events", action=AppendTaggedValue, type=dotted_assignment)
    parser.add_argument("--run-arg", dest="run_args", action="append", default=[])
    parser.add_argument("--run-timeout-sec", type=int, default=30)
    parser.add_argument("--max-iterations", type=int, default=1)
    add_boolean_flag(parser, "failure_on_error", default=True, help_text="Match startup or run errors")
    add_boolean_flag(parser, "failure_on_timeout", default=True, help_text="Match run timeouts")
    parser.add_argument("--failure-stop-reason", dest="failure_stop_reasons", action="append", default=[])
    parser.add_argument("--failure-execution-state", dest="failure_execution_states", action="append", default=[])
    parser.add_argument("--failure-exit-code", dest="failure_exit_codes", action="append", type=int, default=[])
    parser.add_argument("--failure-result-text-regex")
    add_boolean_flag(parser, "capture_enabled", default=True, help_text="Capture bundle on match")
    parser.add_argument("--capture-output-dir")
    parser.add_argument("--capture-bundle-name-prefix")
    parser.add_argument("--capture-bundle-name")
    parser.add_argument("--capture-expression", dest="capture_expressions", action="append", default=[])
    parser.add_argument("--capture-memory-range", dest="capture_memory_ranges", action="append", default=[])
    parser.add_argument("--capture-max-frames", type=int, default=100)
    add_boolean_flag(parser, "capture_include_threads", default=True, help_text="Capture threads")
    add_boolean_flag(parser, "capture_include_backtraces", default=True, help_text="Capture backtraces")
    add_boolean_flag(parser, "capture_include_frame", default=True, help_text="Capture frame")
    add_boolean_flag(parser, "capture_include_variables", default=True, help_text="Capture variables")
    add_boolean_flag(parser, "capture_include_registers", default=True, help_text="Capture registers")
    add_boolean_flag(parser, "capture_include_transcript", default=True, help_text="Capture transcript")
    add_boolean_flag(parser, "capture_include_stop_history", default=True, help_text="Capture stop history")


def _build_run_until_failure(namespace: argparse.Namespace) -> dict[str, object]:
    payload = {
        "startup": {
            "program": namespace.startup_program,
            "args": namespace.startup_args or None,
            "init_commands": namespace.startup_init_commands or None,
            "env": collapse_key_value_entries(namespace.startup_env),
        },
        "setup_steps": _build_step_list(
            [
                ("--step" if option == "--setup-step" else "--step-label" if option == "--setup-step-label" else "--step-arg", value)
                for option, value in namespace.step_events or []
            ]
        ),
        "run_args": namespace.run_args or None,
        "run_timeout_sec": namespace.run_timeout_sec,
        "max_iterations": namespace.max_iterations,
        "failure": {
            "failure_on_error": namespace.failure_on_error,
            "failure_on_timeout": namespace.failure_on_timeout,
            "stop_reasons": namespace.failure_stop_reasons,
            "execution_states": namespace.failure_execution_states,
            "exit_codes": namespace.failure_exit_codes,
            "result_text_regex": namespace.failure_result_text_regex,
        },
        "capture": {
            "enabled": namespace.capture_enabled,
            "output_dir": namespace.capture_output_dir,
            "bundle_name_prefix": namespace.capture_bundle_name_prefix,
            "bundle_name": namespace.capture_bundle_name,
            "expressions": namespace.capture_expressions,
            "memory_ranges": namespace.capture_memory_ranges,
            "max_frames": namespace.capture_max_frames,
            "include_threads": namespace.capture_include_threads,
            "include_backtraces": namespace.capture_include_backtraces,
            "include_frame": namespace.capture_include_frame,
            "include_variables": namespace.capture_include_variables,
            "include_registers": namespace.capture_include_registers,
            "include_transcript": namespace.capture_include_transcript,
            "include_stop_history": namespace.capture_include_stop_history,
        },
    }
    return validate_model_payload(RunUntilFailureArgs, payload)


CLIENT_TOOL_SPECS.update(
    {
        "gdb_breakpoint_query": ToolCliSpec(
            name="gdb_breakpoint_query",
            model=BreakpointQueryArgs,
            configure_parser=_configure_breakpoint_query,
            build_arguments=_build_breakpoint_query,
            render_human=render_action_payload,
        ),
        "gdb_breakpoint_manage": ToolCliSpec(
            name="gdb_breakpoint_manage",
            model=BreakpointManageArgs,
            configure_parser=_configure_breakpoint_manage,
            build_arguments=_build_breakpoint_manage,
            render_human=render_action_payload,
        ),
        "gdb_inspect_query": ToolCliSpec(
            name="gdb_inspect_query",
            model=InspectQueryArgs,
            configure_parser=_configure_inspect_query,
            build_arguments=_build_inspect_query,
            render_human=render_action_payload,
        ),
        "gdb_workflow_batch": ToolCliSpec(
            name="gdb_workflow_batch",
            model=BatchArgs,
            configure_parser=_configure_workflow_batch,
            build_arguments=_build_workflow_batch,
            render_human=render_mapping,
        ),
        "gdb_run_until_failure": ToolCliSpec(
            name="gdb_run_until_failure",
            model=RunUntilFailureArgs,
            configure_parser=_configure_run_until_failure,
            build_arguments=_build_run_until_failure,
            render_human=render_mapping,
        ),
    }
)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_runtime.py tests/mcp/test_client_cli.py tests/integration/test_client_streamable_http.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/parsers.py src/gdb_mcp/client/renderers.py src/gdb_mcp/client/specs.py tests/mcp/test_client_cli.py tests/integration/test_client_streamable_http.py
git commit -m "feat: complete CLI client tool coverage"
```

### Task 5: Expose The Console Script And Update Public Docs

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `TOOLS.md`
- Modify: `examples/README.md`
- Modify: `examples/USAGE_GUIDE.md`

**Testing approach:** `existing tests + targeted verification`
Reason: This task is packaging and docs work on top of already-tested client code. The right verification is script exposure, full lint/type/test coverage, and a clean diff check.

- [ ] **Step 1: Add the console script and update the public docs**

```toml
# pyproject.toml

[project.scripts]
gdb-mcp-server = "gdb_mcp.server:run_server"
gdb-mcp-client = "gdb_mcp.client:run_client"
```

````md
# README.md

## CLI Client

`gdb-mcp-server` exposes the MCP endpoint. `gdb-mcp-client` calls that endpoint over streamable HTTP.

Start the server:

```bash
gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Call a tool:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_session_query \
  --action list
```

Request raw JSON instead of human-oriented output:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  --json \
  gdb_execution_manage \
  --session-id 7 \
  --action continue
```
````

````md
# TOOLS.md

## CLI Client Note

The same public tool inventory is also available through the first-party `gdb-mcp-client` command when the server is running in streamable HTTP mode:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_breakpoint_manage \
  --session-id 7 \
  --action create \
  --breakpoint-kind code \
  --location main
```

Add `--json` when you want the structured payload instead of human-oriented text.
````

````md
# examples/README.md

If you want to exercise the examples without a separate MCP host application, start the server in HTTP mode:

```bash
gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

Then call tools directly with the CLI client, for example:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_session_start \
  --program examples/sample_program
```
````

````md
# examples/USAGE_GUIDE.md

Representative CLI equivalent for the first workflow:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_session_start \
  --program examples/sample_program

gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_breakpoint_manage \
  --session-id 1 \
  --action create \
  --breakpoint-kind code \
  --location main

gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_execution_manage \
  --session-id 1 \
  --action run
```
````

- [ ] **Step 2: Run the targeted script verification**

Run: `uv run gdb-mcp-client --help`
Expected: PASS and shows the global `--server-url` / `--json` flags plus the 17 same-named tool subcommands.

- [ ] **Step 3: Run the full repository verification**

Run: `uv run ruff check src tests`
Expected: PASS

Run: `uv run mypy src`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

Run: `git diff --check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml README.md TOOLS.md examples/README.md examples/USAGE_GUIDE.md
git commit -m "docs: expose and document MCP CLI client"
```
