"""Session lifecycle and query handlers."""

from __future__ import annotations

from ...domain import (
    OperationError,
    OperationResult,
    SessionListInfo,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
)
from ...session.registry import SessionRegistry
from ...session.service import SessionService
from ..schemas import (
    ConnectSessionArgs,
    SessionManageArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    StartSessionArgs,
)
from .common import (
    _normalize_run_args,
)


def _handle_start_session(
    args: StartSessionArgs,
    session_manager: SessionRegistry,
) -> OperationResult[SessionStartInfo]:
    """Start a new debugger session."""

    normalized_args = _normalize_run_args(args.args)
    if isinstance(normalized_args, OperationError):
        return normalized_args

    return session_manager.start_session(
        program=args.program,
        debugger=args.debugger,
        args=normalized_args,
        init_commands=args.init_commands,
        env=args.env,
        debugger_path=args.debugger_path,
        debugger_args=args.debugger_args,
        working_dir=args.working_dir,
        core=args.core,
        rr_replay=args.rr_replay,
    )


def _handle_connect_session(
    args: ConnectSessionArgs,
    session_manager: SessionRegistry,
) -> OperationResult[SessionStartInfo]:
    """Connect to a target endpoint owned by another process and publish a session."""

    return session_manager.start_session(
        endpoint=args.endpoint,
        init_commands=args.init_commands,
        debugger=args.debugger,
        program=args.program,
        debugger_path=args.debugger_path,
    )


def _handle_session_list(
    args: SessionQueryListAction, session_manager: SessionRegistry
) -> OperationResult[SessionListInfo]:
    """List the sessions in the registry."""

    return session_manager.list_sessions()


def _handle_session_status(
    session: SessionService, args: SessionQueryStatusAction
) -> OperationResult[SessionStatusSnapshot]:
    """Read the status of a session resolved by dispatch."""

    return session.get_status()


def _handle_session_manage(
    args: SessionManageArgs,
    session_manager: SessionRegistry,
) -> OperationResult[SessionMessage]:
    """Route one v2 session lifecycle mutation."""

    return session_manager.close_session(args.root.session_id)
