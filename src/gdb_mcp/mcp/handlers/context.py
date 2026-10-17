"""Thread and frame context handlers."""

from __future__ import annotations

from ...session.service import SessionService
from ..schemas import (
    ContextManageArgs,
    ContextManageSelectFrameAction,
    ContextManageSelectThreadAction,
    ContextQueryArgs,
    ContextQueryBacktraceAction,
    ContextQueryFrameAction,
    ContextQueryThreadsAction,
)
from .common import (
    ToolResult,
    _unsupported_action,
    _unwrap_action_args,
)


def _handle_context_query(session: SessionService, args: ContextQueryArgs) -> ToolResult:
    """Route v2 context query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    match action_args:
        case ContextQueryThreadsAction():
            return session.get_threads()
        case ContextQueryBacktraceAction():
            return session.get_backtrace(
                thread_id=action_args.query.thread_id,
                max_frames=action_args.query.max_frames,
            )
        case ContextQueryFrameAction():
            return session.get_frame_info(
                thread_id=action_args.query.thread_id,
                frame=action_args.query.frame,
            )
    return _unsupported_action("context query action", action_args)


def _handle_context_manage(session: SessionService, args: ContextManageArgs) -> ToolResult:
    """Route v2 context mutation actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    match action_args:
        case ContextManageSelectThreadAction():
            return session.select_thread(thread_id=action_args.context.thread_id)
        case ContextManageSelectFrameAction():
            return session.select_frame(frame_number=action_args.context.frame)
    return _unsupported_action("context manage action", action_args)
