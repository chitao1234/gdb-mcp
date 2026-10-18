"""Session lifecycle and query handlers."""

from __future__ import annotations

from ...contracts import (
    TOOL_SESSION_QUERY,
)
from ...domain import (
    OperationError,
    OperationSuccess,
    payload_to_mapping,
)
from ...session.locking import session_workflow_context
from ...session.registry import SessionRegistry
from ..schemas import (
    ConnectSessionArgs,
    SessionManageArgs,
    SessionManageStopAction,
    SessionQueryArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    StartSessionArgs,
)
from .common import (
    ToolContext,
    ToolResult,
    _normalize_run_args,
    _unsupported_action,
    _unwrap_action_args,
)


def _publish_session_result(session_id: int | None, result: ToolResult) -> ToolResult:
    """Inject the allocated session id into a successful session-start payload."""

    if session_id is None or not isinstance(result, OperationSuccess):
        return result

    payload = payload_to_mapping(result.value)
    if not isinstance(payload, dict):
        return OperationError(message="Internal error: session start payload must be an object")
    payload = dict(payload)
    payload["session_id"] = session_id
    return OperationSuccess(payload)


def _handle_start_session(
    args: StartSessionArgs,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Start a new debugger session."""

    normalized_args = _normalize_run_args(args.args)
    if isinstance(normalized_args, OperationError):
        return normalized_args

    session_id, result = session_manager.start_session(
        program=args.program,
        args=normalized_args,
        init_commands=args.init_commands,
        env=args.env,
        gdb_path=args.gdb_path,
        gdb_args=args.gdb_args,
        working_dir=args.working_dir,
        core=args.core,
        rr_replay=args.rr_replay,
    )
    return _publish_session_result(session_id, result)


def _handle_connect_session(
    args: ConnectSessionArgs,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Connect to an externally started GDB/MI endpoint and publish a session."""

    session_id, result = session_manager.start_session(
        endpoint=args.endpoint,
        init_commands=args.init_commands,
    )
    return _publish_session_result(session_id, result)


def _handle_session_query(context: ToolContext, args: SessionQueryArgs) -> ToolResult:
    """Route one v2 session query action.

    Direct calls arrive without a session and resolve it for the status action;
    batch execution supplies the session and only permits status (the batch
    contract rejects ``list`` before dispatch).
    """

    action_args = _unwrap_action_args(args)

    match action_args:
        case SessionQueryListAction():
            if context.registry is None:  # pragma: no cover - batch rejects list upstream
                return OperationError(
                    message=(
                        f"{TOOL_SESSION_QUERY}(action=list) is not valid inside a workflow batch"
                    ),
                    code="unsupported_combination",
                )
            return context.registry.list_sessions()
        case SessionQueryStatusAction():
            session = context.session
            if session is None:
                if context.registry is None:  # pragma: no cover - always present for direct calls
                    return OperationError(message="No session registry available")
                resolved = context.registry.resolve_session(action_args.session_id)
                if isinstance(resolved, OperationError):
                    return resolved
                session = resolved
            with session_workflow_context(session):
                return session.get_status()
    return _unsupported_action("session query action", action_args)


def _handle_session_manage(
    args: SessionManageArgs,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Route one v2 session lifecycle mutation."""

    action_args = _unwrap_action_args(args)

    match action_args:
        case SessionManageStopAction():
            return session_manager.close_session(action_args.session_id)
    return _unsupported_action("session manage action", action_args)
