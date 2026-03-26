"""Serialization helpers for MCP tool results."""

from __future__ import annotations

import json

from mcp.types import TextContent


def serialize_result(result: dict) -> list[TextContent]:
    """Serialize a tool result dictionary into MCP text content."""

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


def serialize_exception(tool_name: str, exc: Exception) -> list[TextContent]:
    """Serialize an unexpected exception into the standard MCP error shape."""

    error_result = {"status": "error", "message": str(exc), "tool": tool_name}
    return serialize_result(error_result)
