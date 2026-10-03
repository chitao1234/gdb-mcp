"""Session lifecycle and query handlers."""

from __future__ import annotations


from ... import contracts as shared_contracts
from ...contracts import (
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
)
from ...domain import (
    OperationError,
    OperationSuccess,
    payload_to_mapping,
)
from ...session.locking import session_workflow_context
from ...session.registry import SessionRegistry
from ...session.service import SessionService
from ..schemas import (
    SessionManageArgs,
    SessionManageStopAction,
    SessionQueryArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    StartSessionArgs,
)
from .common import (
    ToolArguments,
    ToolResult,
    _normalize_run_args,
    _unwrap_action_args,
)


def _handle_start_session(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Validate and start a new debugger session."""

    args = StartSessionArgs.model_validate(arguments)
    normalized_args = _normalize_run_args(args.args)
    if isinstance(normalized_args, OperationError):
        return normalized_args

    session_id, result = session_manager.start_session(
        program=args.program,
        args=normalized_args,
        init_commands=args.init_commands,
        env=args.env,
        gdb_path=args.gdb_path,
        working_dir=args.working_dir,
        core=args.core,
    )
    if session_id is not None and isinstance(result, OperationSuccess):
        payload = payload_to_mapping(result.value)
        if not isinstance(payload, dict):
            return OperationError(message="Internal error: session start payload must be an object")
        payload = dict(payload)
        payload["session_id"] = session_id
        return OperationSuccess(payload)
    return result


def _handle_session_query(
    args: SessionQueryArgs,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Route one v2 session query action."""

    action_args = _unwrap_action_args(args)

    if isinstance(action_args, SessionQueryListAction):
        return session_manager.list_sessions()

    if isinstance(action_args, SessionQueryStatusAction):
        session = session_manager.resolve_session(action_args.session_id)
        if isinstance(session, OperationError):
            return session
        with session_workflow_context(session):
            return session.get_status()

    return OperationError(
        message=f"Unsupported session query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_session_manage(
    args: SessionManageArgs,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Route one v2 session lifecycle mutation."""

    action_args = _unwrap_action_args(args)

    if isinstance(action_args, SessionManageStopAction):
        return session_manager.close_session(action_args.session_id)

    return OperationError(
        message=f"Unsupported session manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_session_query_for_session(
    session: SessionService, args: SessionQueryArgs
) -> ToolResult:
    """Route batch-safe session query actions against an already resolved session."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, SessionQueryStatusAction):
        return session.get_status()
    return OperationError(
        message=(
            f"{TOOL_SESSION_QUERY}(action={shared_contracts.ACTION_LIST}) "
            f"is not valid inside {TOOL_WORKFLOW_BATCH}"
        ),
        code="unsupported_combination",
    )
