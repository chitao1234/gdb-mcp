"""Execution control handler."""

from __future__ import annotations


from ...domain import (
    OperationError,
)
from ...session.service import SessionService
from ..schemas import (
    ExecutionContinueAction,
    ExecutionFinishAction,
    ExecutionInterruptAction,
    ExecutionManageArgs,
    ExecutionNextAction,
    ExecutionRunAction,
    ExecutionStepAction,
    ExecutionWaitForStopAction,
)
from .common import (
    ToolResult,
    _execution_wait_policy,
    _normalize_run_args,
    _unwrap_action_args,
)


def _handle_execution_manage(session: SessionService, args: ExecutionManageArgs) -> ToolResult:
    """Route v2 execution actions to the session execution API."""

    action_args = _unwrap_action_args(args)

    if isinstance(action_args, ExecutionRunAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(
            action_args.execution.wait_until, action_args.execution.timeout_sec
        )
        run_args = _normalize_run_args(action_args.execution.args)
        if isinstance(run_args, OperationError):
            return run_args
        return session.run(
            args=run_args,
            timeout_sec=timeout_sec,
            wait_for_stop=wait_for_stop,
        )

    if isinstance(action_args, ExecutionContinueAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(
            action_args.execution.wait_until, action_args.execution.timeout_sec
        )
        return session.continue_execution(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
        )

    if isinstance(action_args, ExecutionInterruptAction):
        return session.interrupt()

    if isinstance(action_args, ExecutionStepAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(
            action_args.execution.wait_until, action_args.execution.timeout_sec
        )
        return session.step(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
        )

    if isinstance(action_args, ExecutionNextAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(
            action_args.execution.wait_until, action_args.execution.timeout_sec
        )
        return session.next(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
        )

    if isinstance(action_args, ExecutionFinishAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(
            action_args.execution.wait_until, action_args.execution.timeout_sec
        )
        return session.finish(
            timeout_sec=timeout_sec,
            wait_for_stop=wait_for_stop,
        )

    if isinstance(action_args, ExecutionWaitForStopAction):
        return session.wait_for_stop(
            timeout_sec=action_args.execution.timeout_sec,
            stop_reasons=tuple(action_args.execution.stop_reasons),
        )

    return OperationError(
        message=f"Unsupported execution action: {type(action_args).__name__}",
        code="validation_error",
    )
