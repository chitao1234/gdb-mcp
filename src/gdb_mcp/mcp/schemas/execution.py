"""Execution control request models."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    Field,
    RootModel,
)

from ... import contracts as shared_contracts
from ...contracts import (
    ExecutionWaitUntil,
)

from .common import (
    SessionId,
    StrictArgsModel,
)
from .selectors import EmptyQuery


class ExecutionWaitPolicy(StrictArgsModel):
    wait_until: ExecutionWaitUntil = Field(
        "stop",
        description="Whether to return when GDB acknowledges running or when a stop is observed.",
    )
    timeout_sec: int | None = Field(
        None,
        gt=0,
        description="Optional timeout override for the execution command.",
    )


class ExecutionRunPayload(ExecutionWaitPolicy):
    args: list[str] | str | None = Field(
        None,
        description="Optional inferior argv override for this run.",
    )


class ExecutionControlPayload(ExecutionWaitPolicy):
    pass


class ExecutionWaitForStopPayload(StrictArgsModel):
    timeout_sec: int = Field(30, gt=0, description="Maximum time to wait for a stop event")
    stop_reasons: list[str] = Field(
        default_factory=list,
        description="Optional stop reasons that should count as a match",
    )


class ExecutionRunAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionRunName = Field(..., description="Start the inferior")
    execution: ExecutionRunPayload = Field(
        default_factory=lambda: ExecutionRunPayload.model_validate({})
    )


class ExecutionContinueAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionContinueName = Field(..., description="Continue execution")
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionInterruptAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionInterruptName = Field(
        ...,
        description="Interrupt the running inferior",
    )
    execution: EmptyQuery = Field(default_factory=EmptyQuery)


class ExecutionStepAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionStepName = Field(
        ...,
        description="Step into the next line or instruction",
    )
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionNextAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionNextName = Field(
        ...,
        description="Step over the next line or instruction",
    )
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionFinishAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionFinishName = Field(..., description="Finish the current frame")
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionWaitForStopAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionWaitForStopName = Field(
        ...,
        description="Wait for the next stop event",
    )
    execution: ExecutionWaitForStopPayload = Field(
        default_factory=lambda: ExecutionWaitForStopPayload.model_validate({})
    )


class ExecutionManageArgs(
    RootModel[
        Annotated[
            ExecutionRunAction
            | ExecutionContinueAction
            | ExecutionInterruptAction
            | ExecutionStepAction
            | ExecutionNextAction
            | ExecutionFinishAction
            | ExecutionWaitForStopAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for execution control."""
