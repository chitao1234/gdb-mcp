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
    _unwrap_action_args,
)


def _handle_inferior_query(session: SessionService, args: InferiorQueryArgs) -> ToolResult:
    """Route v2 inferior query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, InferiorQueryListAction):
        return session.list_inferiors()

    if isinstance(action_args, InferiorQueryCurrentAction):
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

    return OperationError(
        message=f"Unsupported inferior query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inferior_manage(session: SessionService, args: InferiorManageArgs) -> ToolResult:
    """Route v2 inferior mutation actions to the inspection/execution services."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, InferiorManageCreateAction):
        create_payload = action_args.inferior
        return session.add_inferior(
            executable=create_payload.executable,
            make_current=create_payload.make_current,
        )

    if isinstance(action_args, InferiorManageRemoveAction):
        remove_payload = action_args.inferior
        return session.remove_inferior(inferior_id=remove_payload.inferior_id)

    if isinstance(action_args, InferiorManageSelectAction):
        select_payload = action_args.inferior
        return session.select_inferior(inferior_id=select_payload.inferior_id)

    if isinstance(action_args, InferiorManageFollowForkAction):
        follow_payload = action_args.inferior
        return session.set_follow_fork_mode(mode=follow_payload.mode)

    if isinstance(action_args, InferiorManageDetachOnForkAction):
        detach_payload = action_args.inferior
        return session.set_detach_on_fork(enabled=detach_payload.enabled)

    return OperationError(
        message=f"Unsupported inferior manage action: {type(action_args).__name__}",
        code="validation_error",
    )
