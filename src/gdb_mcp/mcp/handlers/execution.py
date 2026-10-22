"""Execution control handler."""

from __future__ import annotations

from ...domain import (
    CommandExecutionInfo,
    FinishInfo,
    MessageResult,
    OperationError,
    OperationResult,
    WaitForStopInfo,
)
from ...session.service import SessionService
from ..schemas import (
    ExecutionContinueAction,
    ExecutionFinishAction,
    ExecutionInterruptAction,
    ExecutionJumpAction,
    ExecutionManageArgs,
    ExecutionNextAction,
    ExecutionNextInstructionAction,
    ExecutionReturnAction,
    ExecutionRunAction,
    ExecutionStepAction,
    ExecutionStepInstructionAction,
    ExecutionUntilAction,
    ExecutionWaitForStopAction,
)
from .common import (
    _execution_wait_policy,
    _normalize_run_args,
)


def _handle_execution_manage(
    session: SessionService, args: ExecutionManageArgs
) -> OperationResult[CommandExecutionInfo | MessageResult | FinishInfo | WaitForStopInfo]:
    """Route v2 execution actions to the session execution API."""

    action_args = args.root

    match action_args:
        case ExecutionInterruptAction():
            return session.interrupt()
        case ExecutionReturnAction():
            return session.return_from_frame(value=action_args.execution.value)
        case ExecutionWaitForStopAction():
            return session.wait_for_stop(
                timeout_sec=action_args.execution.timeout_sec,
                stop_reasons=tuple(action_args.execution.stop_reasons),
            )
    options = _execution_wait_policy(
        action_args.execution.wait_until, action_args.execution.timeout_sec
    )
    match action_args:
        case ExecutionRunAction():
            run_args = _normalize_run_args(action_args.execution.args)
            if isinstance(run_args, OperationError):
                return run_args
            return session.run(
                options=options,
                args=run_args,
            )
        case ExecutionContinueAction():
            return session.continue_execution(
                options=options,
                reverse=action_args.execution.reverse,
            )
        case ExecutionStepAction():
            return session.step(
                options=options,
                reverse=action_args.execution.reverse,
            )
        case ExecutionNextAction():
            return session.next(
                options=options,
                reverse=action_args.execution.reverse,
            )
        case ExecutionStepInstructionAction():
            return session.step_instruction(
                options=options,
                reverse=action_args.execution.reverse,
            )
        case ExecutionNextInstructionAction():
            return session.next_instruction(
                options=options,
                reverse=action_args.execution.reverse,
            )
        case ExecutionJumpAction() | ExecutionUntilAction():
            operation = (
                session.jump if isinstance(action_args, ExecutionJumpAction) else session.until
            )
            return operation(
                options=options,
                location=action_args.execution.location,
            )
        case ExecutionFinishAction():
            return session.finish(
                options=options,
                reverse=action_args.execution.reverse,
            )
