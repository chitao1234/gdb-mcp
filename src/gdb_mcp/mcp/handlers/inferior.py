"""Inferior query and mutation handlers."""

from __future__ import annotations

from ...domain import (
    OperationError,
    OperationSuccess,
)
from ...session.service import SessionService
from ..schemas import (
    InferiorManageArgs,
    InferiorManageCreateAction,
    InferiorManageDetachOnForkAction,
    InferiorManageFollowForkAction,
    InferiorManageRemoveAction,
    InferiorManageSelectAction,
    InferiorQueryArgs,
    InferiorQueryCurrentAction,
    InferiorQueryListAction,
)
from .common import (
    ToolResult,
    _unsupported_action,
    _unwrap_action_args,
)


def _handle_inferior_query(session: SessionService, args: InferiorQueryArgs) -> ToolResult:
    """Route v2 inferior query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    match action_args:
        case InferiorQueryListAction():
            return session.list_inferiors()
        case InferiorQueryCurrentAction():
            result = session.list_inferiors()
            if isinstance(result, OperationError):
                return result

            current_inferior_id = result.value.current_inferior_id
            current_inferior = next(
                (
                    inferior
                    for inferior in result.value.inferiors
                    if inferior.get("inferior_id") == current_inferior_id
                ),
                None,
            )
            if current_inferior is None:
                return OperationError(
                    message="Current inferior could not be determined",
                    code="not_found",
                    details={"current_inferior_id": current_inferior_id},
                )
            return OperationSuccess({"inferior": current_inferior})
    return _unsupported_action("inferior query action", action_args)


def _handle_inferior_manage(session: SessionService, args: InferiorManageArgs) -> ToolResult:
    """Route v2 inferior mutation actions to the inspection/execution services."""

    action_args = _unwrap_action_args(args)
    match action_args:
        case InferiorManageCreateAction():
            create_payload = action_args.inferior
            return session.add_inferior(
                executable=create_payload.executable,
                make_current=create_payload.make_current,
            )
        case InferiorManageRemoveAction():
            return session.remove_inferior(inferior_id=action_args.inferior.inferior_id)
        case InferiorManageSelectAction():
            return session.select_inferior(inferior_id=action_args.inferior.inferior_id)
        case InferiorManageFollowForkAction():
            return session.set_follow_fork_mode(mode=action_args.inferior.mode)
        case InferiorManageDetachOnForkAction():
            return session.set_detach_on_fork(enabled=action_args.inferior.enabled)
    return _unsupported_action("inferior manage action", action_args)
