"""Runtime helpers for the MCP CLI client."""

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
    """Structured result for one CLI-driven MCP tool invocation."""

    payload: dict[str, object]
    is_error: bool


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

    return cast(dict[str, object], payload)


async def invoke_tool(
    server_url: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ClientToolResponse:
    """Connect to one MCP HTTP endpoint, invoke a tool, and return its parsed payload."""

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
        is_error=result.isError,
    )
