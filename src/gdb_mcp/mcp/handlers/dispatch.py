"""Top-level MCP tool dispatch."""

from __future__ import annotations

import logging
from typing import cast

from pydantic import ValidationError
from mcp.types import CallToolResult

from ...contracts import (
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
)
from ...domain import (
    OperationError,
)
from ...session.locking import session_workflow_context
from ...session.registry import SessionRegistry
from ..schemas import (
    SessionManageArgs,
    SessionQueryArgs,
    TOOL_MODELS,
)
from ..serializer import serialize_exception, serialize_result
from ..validation_errors import build_validation_error
from .common import (
    SessionArgsProtocol,
    SessionToolSpec,
    ToolArguments,
    ToolResult,
    _normalize_arguments,
    _unwrap_action_args,
    _wrap_action_result_for,
)
from .session import (
    _handle_session_manage,
    _handle_session_query,
    _handle_start_session,
)
from .workflow import _handle_run_until_failure
from .registry import SESSION_TOOL_SPECS


def _dispatch_session_tool(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
    tool_spec: SessionToolSpec,
) -> ToolResult:
    """Validate one session-scoped request and invoke its handler."""

    validated_args = tool_spec.model.model_validate(arguments)
    session_args = cast(SessionArgsProtocol, _unwrap_action_args(validated_args))
    session = session_manager.resolve_session(session_args.session_id)
    if isinstance(session, OperationError):
        return _wrap_action_result_for(validated_args, session)
    with session_workflow_context(session):
        result = tool_spec.handler(session, validated_args)
    return _wrap_action_result_for(validated_args, result)


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
        if name == TOOL_SESSION_START:
            return serialize_result(_handle_start_session(normalized_args, session_manager))
        if name == TOOL_SESSION_QUERY:
            validated_query_args = SessionQueryArgs.model_validate(normalized_args)
            return serialize_result(
                _wrap_action_result_for(
                    validated_query_args,
                    _handle_session_query(validated_query_args, session_manager),
                )
            )
        if name == TOOL_SESSION_MANAGE:
            validated_manage_args = SessionManageArgs.model_validate(normalized_args)
            return serialize_result(
                _wrap_action_result_for(
                    validated_manage_args,
                    _handle_session_manage(validated_manage_args, session_manager),
                )
            )
        if name == TOOL_RUN_UNTIL_FAILURE:
            return serialize_result(_handle_run_until_failure(normalized_args, session_manager))

        tool_spec = SESSION_TOOL_SPECS.get(name)
        if tool_spec is None:
            return serialize_result(
                OperationError(message=f"Unknown tool: {name}", code="unknown_tool")
            )

        return serialize_result(_dispatch_session_tool(normalized_args, session_manager, tool_spec))

    except ValidationError as exc:
        return serialize_result(
            build_validation_error(exc, tool_name=name, model=TOOL_MODELS.get(name))
        )
    except Exception as exc:
        logger.error("Error executing tool %s: %s", name, exc, exc_info=True)
        return serialize_exception(name, exc)
