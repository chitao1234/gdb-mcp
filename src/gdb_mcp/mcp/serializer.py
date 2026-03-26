"""Serialization helpers for MCP tool results."""

from __future__ import annotations

from collections.abc import Mapping
import json

from mcp.types import TextContent

from ..domain import OperationError, OperationResult, OperationSuccess


def result_to_payload(
    result: OperationResult[Mapping[str, object]] | Mapping[str, object],
) -> dict[str, object]:
    """Convert a typed internal result into the external JSON payload shape."""

    if isinstance(result, OperationSuccess):
        payload = dict(result.value)
        if result.warnings and "warnings" not in payload:
            payload["warnings"] = list(result.warnings)
        return payload

    if isinstance(result, OperationError):
        payload: dict[str, object] = {"status": "error", "message": result.message}
        if result.fatal:
            payload["fatal"] = True
        payload.update(result.details)
        return payload

    return dict(result)


def serialize_result(result: OperationResult[Mapping[str, object]] | Mapping[str, object]) -> list[TextContent]:
    """Serialize a typed or legacy tool result into MCP text content."""

    return [TextContent(type="text", text=json.dumps(result_to_payload(result), indent=2))]


def serialize_exception(tool_name: str, exc: Exception) -> list[TextContent]:
    """Serialize an unexpected exception into the standard MCP error shape."""

    error_result = OperationError(message=str(exc), details={"tool": tool_name})
    return serialize_result(error_result)
