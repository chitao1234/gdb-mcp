"""Top-level MCP tool dispatch."""

from __future__ import annotations

import logging

from mcp.types import CallToolResult
from pydantic import BaseModel, ValidationError

from ...domain import (
    OperationError,
)
from ...session.locking import session_workflow_context
from ...session.registry import SessionRegistry
from ..schemas import (
    TOOL_MODELS,
)
from ..serializer import serialize_exception, serialize_result
from ..validation_errors import build_validation_error
from .common import (
    SessionToolInvocation,
    ToolArguments,
    ToolResult,
    _annotate_session_failure,
    _normalize_arguments,
    _wrap_action_result,
)
from .registry import TOOL_SPECS


def _validation_model(name: str) -> type[BaseModel] | None:
    """Resolve one tool's request model from the authoritative registry."""

    spec = TOOL_SPECS.get(name)
    if spec is not None:
        return spec.model
    return TOOL_MODELS.get(name)


def _dispatch_tool(
    name: str,
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Validate one request against its tool spec and invoke the handler."""

    spec = TOOL_SPECS.get(name)
    if spec is None:
        return OperationError(message=f"Unknown tool: {name}", code="unknown_tool")

    invocation = spec.prepare(arguments)

    if isinstance(invocation, SessionToolInvocation):
        session = session_manager.resolve_session(invocation.session_id)
        if isinstance(session, OperationError):
            return _wrap_action_result(invocation.action, session)
        with session_workflow_context(session):
            result = invocation.execute(session)
        result = _annotate_session_failure(result, session, session_id=invocation.session_id)
    else:
        result = invocation.execute(session_manager)

    return _wrap_action_result(invocation.action, result)


async def dispatch_tool_call(
    name: str,
    arguments: object,
    session_manager: SessionRegistry,
    *,
    logger: logging.Logger,
) -> CallToolResult:
    """Dispatch one MCP tool call using structured validation and handlers."""

    try:
        normalized_args = _normalize_arguments(arguments)
    except TypeError as exc:
        return serialize_result(
            OperationError(
                message=str(exc),
                code="validation_error",
                details={
                    "tool": name,
                    "field_errors": [
                        {"field": "(arguments)", "issue": "invalid", "message": str(exc)}
                    ],
                },
            )
        )

    try:
        return serialize_result(_dispatch_tool(name, normalized_args, session_manager))
    except ValidationError as exc:
        return serialize_result(
            build_validation_error(exc, tool_name=name, model=_validation_model(name))
        )
    except Exception as exc:
        logger.exception("Error executing tool %s", name)
        return serialize_exception(name, exc)
