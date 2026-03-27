"""Serialization helpers for MCP tool results."""

from __future__ import annotations

import json

from mcp.types import TextContent

from ..domain import OperationError, OperationResult, StructuredPayload, result_to_mapping


def result_to_payload(
    result: OperationResult[object],
) -> StructuredPayload:
    """Convert a typed internal result into the external JSON payload shape."""

    return result_to_mapping(result)


def serialize_result(
    result: OperationResult[object],
) -> list[TextContent]:
    """Serialize a typed tool result into MCP text content."""

    return [TextContent(type="text", text=json.dumps(result_to_payload(result), indent=2))]


def serialize_exception(tool_name: str, exc: Exception) -> list[TextContent]:
    """Serialize an unexpected exception into the standard MCP error shape."""

    error_result = OperationError(message=str(exc), details={"tool": tool_name})
    return serialize_result(error_result)
