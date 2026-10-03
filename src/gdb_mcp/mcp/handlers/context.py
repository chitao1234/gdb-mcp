"""Thread and frame context handlers."""

from __future__ import annotations


from ...domain import (
    OperationError,
)
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
    _unwrap_action_args,
)


def _handle_context_query(session: SessionService, args: ContextQueryArgs) -> ToolResult:
    """Route v2 context query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, ContextQueryThreadsAction):
        return session.get_threads()

    if isinstance(action_args, ContextQueryBacktraceAction):
        backtrace_query = action_args.query
        return session.get_backtrace(
            thread_id=backtrace_query.thread_id,
            max_frames=backtrace_query.max_frames,
        )

    if isinstance(action_args, ContextQueryFrameAction):
        frame_query = action_args.query
        return session.get_frame_info(
            thread_id=frame_query.thread_id,
            frame=frame_query.frame,
        )

    return OperationError(
        message=f"Unsupported context query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_context_manage(session: SessionService, args: ContextManageArgs) -> ToolResult:
    """Route v2 context mutation actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, ContextManageSelectThreadAction):
        return session.select_thread(thread_id=action_args.context.thread_id)

    if isinstance(action_args, ContextManageSelectFrameAction):
        return session.select_frame(frame_number=action_args.context.frame)

    return OperationError(
        message=f"Unsupported context manage action: {type(action_args).__name__}",
        code="validation_error",
    )
