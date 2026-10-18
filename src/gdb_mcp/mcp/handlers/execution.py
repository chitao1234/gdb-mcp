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
    _unsupported_action,
    _unwrap_action_args,
)


def _handle_execution_manage(session: SessionService, args: ExecutionManageArgs) -> ToolResult:
    """Route v2 execution actions to the session execution API."""

    action_args = _unwrap_action_args(args)

    match action_args:
        case ExecutionRunAction():
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
        case ExecutionContinueAction():
            timeout_sec, wait_for_stop = _execution_wait_policy(
                action_args.execution.wait_until, action_args.execution.timeout_sec
            )
            return session.continue_execution(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
                reverse=action_args.execution.reverse,
            )
        case ExecutionInterruptAction():
            return session.interrupt()
        case ExecutionStepAction():
            timeout_sec, wait_for_stop = _execution_wait_policy(
                action_args.execution.wait_until, action_args.execution.timeout_sec
            )
            return session.step(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
                reverse=action_args.execution.reverse,
            )
        case ExecutionNextAction():
            timeout_sec, wait_for_stop = _execution_wait_policy(
                action_args.execution.wait_until, action_args.execution.timeout_sec
            )
            return session.next(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
                reverse=action_args.execution.reverse,
            )
        case ExecutionFinishAction():
            timeout_sec, wait_for_stop = _execution_wait_policy(
                action_args.execution.wait_until, action_args.execution.timeout_sec
            )
            return session.finish(
                timeout_sec=timeout_sec,
                wait_for_stop=wait_for_stop,
                reverse=action_args.execution.reverse,
            )
        case ExecutionWaitForStopAction():
            return session.wait_for_stop(
                timeout_sec=action_args.execution.timeout_sec,
                stop_reasons=tuple(action_args.execution.stop_reasons),
            )
    return _unsupported_action("execution action", action_args)
