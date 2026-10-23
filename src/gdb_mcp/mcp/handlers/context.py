"""Thread and frame context handlers."""

from __future__ import annotations

from ...domain import (
    BacktraceInfo,
    FrameInfo,
    FrameSelectionInfo,
    OperationResult,
    ThreadListInfo,
    ThreadSelectionInfo,
)
from ...session.requests import ThreadQuery
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


def _handle_context_query(
    session: SessionService, args: ContextQueryArgs
) -> OperationResult[ThreadListInfo | BacktraceInfo | FrameInfo]:
    """Route v2 context query actions to the inspection service."""

    action_args = args.root
    match action_args:
        case ContextQueryThreadsAction():
            query = action_args.query
            return session.get_threads(
                ThreadQuery(
                    waiting_on_channel=query.waiting_on_channel,
                    thread_id=query.context.thread_id if query.context else None,
                    frame=query.context.frame if query.context else None,
                    max_results=query.max_results,
                )
            )
        case ContextQueryBacktraceAction():
            return session.get_backtrace(
                thread_id=action_args.query.thread_id,
                max_frames=action_args.query.max_frames,
                include_defers=action_args.query.include_defers,
            )
        case ContextQueryFrameAction():
            return session.get_frame_info(
                thread_id=action_args.query.thread_id,
                frame=action_args.query.frame,
            )


def _handle_context_manage(
    session: SessionService, args: ContextManageArgs
) -> OperationResult[ThreadSelectionInfo | FrameSelectionInfo]:
    """Route v2 context mutation actions to the inspection service."""

    action_args = args.root
    match action_args:
        case ContextManageSelectThreadAction():
            return session.select_thread(thread_id=action_args.context.thread_id)
        case ContextManageSelectFrameAction():
            return session.select_frame(frame_number=action_args.context.frame)
