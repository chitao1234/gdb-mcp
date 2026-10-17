"""Runtime helpers for the MCP CLI client."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import timedelta

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from .results import ToolEnvelope

logger = logging.getLogger(__name__)

# Tool results travel over long-lived SSE responses in the MCP streamable-HTTP
# transport. httpx's built-in default is a 5 second read timeout, which tears the
# stream down mid-call and drops the response, leaving the client waiting
# forever. Match the MCP defaults (30s request timeout, 300s SSE read timeout) so
# tool calls that take longer than 5 seconds can still complete.
_CLIENT_TIMEOUT = httpx.Timeout(30.0, read=300.0)

# Last-resort bound on waiting for one tool response. If a transport drops the
# response anyway (an SSE stream torn down with no id to resume from), the MCP
# SDK leaves the request pending and `send_request` waits on it without a
# deadline, so the CLI would hang forever. A session read timeout turns that into
# an explicit error. The default is far above any ordinary tool call and can be
# raised for very long campaigns such as gdb_run_until_failure.
_SESSION_READ_TIMEOUT_ENV = "GDB_MCP_CLIENT_READ_TIMEOUT_SEC"
_DEFAULT_SESSION_READ_TIMEOUT_SEC = 1800.0


def _session_read_timeout() -> timedelta:
    """Resolve the per-tool-call read timeout for one CLI invocation."""

    raw = os.environ.get(_SESSION_READ_TIMEOUT_ENV)
    if raw is not None:
        try:
            seconds = float(raw)
        except ValueError:
            logger.warning(
                "Ignoring invalid %s=%r; using %ss",
                _SESSION_READ_TIMEOUT_ENV,
                raw,
                _DEFAULT_SESSION_READ_TIMEOUT_SEC,
            )
        else:
            if seconds > 0:
                return timedelta(seconds=seconds)
            logger.warning(
                "Ignoring non-positive %s=%r; using %ss",
                _SESSION_READ_TIMEOUT_ENV,
                raw,
                _DEFAULT_SESSION_READ_TIMEOUT_SEC,
            )
    return timedelta(seconds=_DEFAULT_SESSION_READ_TIMEOUT_SEC)


@dataclass(frozen=True)
class ClientToolResponse:
    """Structured result for one CLI-driven MCP tool invocation."""

    payload: dict[str, object]
    is_error: bool

    def envelope(self) -> ToolEnvelope:
        """Return a typed view over this response's payload."""

        return ToolEnvelope.from_payload(self.payload)


def _parse_tool_payload(result: CallToolResult) -> dict[str, object]:
    """Extract the JSON object payload from one MCP tool result."""

    if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
        raise ValueError("Expected exactly one text content item from MCP tool result")

    try:
        payload = json.loads(result.content[0].text)
    except json.JSONDecodeError as exc:
        raise ValueError("Expected valid JSON tool payload") from exc

    if not isinstance(payload, dict):
        raise ValueError("Expected tool payload JSON object")

    return payload


async def invoke_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    http_client: httpx.AsyncClient | None = None,
    auth_token: str | None = None,
) -> ClientToolResponse:
    """Connect to one MCP HTTP endpoint, invoke a tool, and return its parsed payload."""

    owns_client = http_client is None and auth_token is not None
    client = http_client
    if owns_client:
        client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=_CLIENT_TIMEOUT,
        )
    try:
        async with streamable_http_client(server_url, http_client=client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=_session_read_timeout(),
            ) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    finally:
        if owns_client and client is not None:
            await client.aclose()

    payload = _parse_tool_payload(result)
    return ClientToolResponse(
        payload=payload,
        is_error=result.isError,
    )
