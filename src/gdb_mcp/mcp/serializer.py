"""Serialization helpers for MCP tool results."""

from __future__ import annotations

import json

from mcp.types import CallToolResult, TextContent

from ..domain import OperationError, OperationResult, StructuredPayload, result_to_mapping


def result_to_payload(
    result: OperationResult[object],
) -> StructuredPayload:
    """Convert a typed internal result into the external JSON payload shape."""

    return result_to_mapping(result)


def serialize_result(
    result: OperationResult[object],
) -> CallToolResult:
    """Serialize a typed tool result with a truthful MCP ``isError`` flag."""

    payload = result_to_payload(result)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        isError=isinstance(result, OperationError),
    )


def serialize_exception(tool_name: str, exc: Exception) -> CallToolResult:
    """Serialize an unexpected exception into the standard MCP error shape."""

    error_result = OperationError(
        message=str(exc),
        code="internal_error",
        details={"tool": tool_name},
    )
    return serialize_result(error_result)
