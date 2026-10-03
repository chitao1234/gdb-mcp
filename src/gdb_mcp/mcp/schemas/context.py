"""Thread and frame context request models."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    Field,
    RootModel,
)

from ... import contracts as shared_contracts

from .common import (
    SessionId,
    StrictArgsModel,
)
from .selectors import (
    EmptyQuery,
    FrameSelectorArgs,
    ThreadSelectorArgs,
)


class ContextBacktraceQueryArgs(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    max_frames: int = Field(100, gt=0, description="Maximum number of frames to return")


class ContextFrameQueryArgs(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    frame: int | None = Field(None, ge=0, description="Optional frame override")


class ContextQueryThreadsAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionThreadsName = Field(..., description="List threads")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class ContextQueryBacktraceAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionBacktraceName = Field(
        ...,
        description="Inspect a backtrace",
    )
    query: ContextBacktraceQueryArgs = Field(
        default_factory=lambda: ContextBacktraceQueryArgs.model_validate({})
    )


class ContextQueryFrameAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionFrameName = Field(
        ...,
        description="Inspect frame information",
    )
    query: ContextFrameQueryArgs = Field(
        default_factory=lambda: ContextFrameQueryArgs.model_validate({})
    )


class ContextQueryArgs(
    RootModel[
        Annotated[
            ContextQueryThreadsAction | ContextQueryBacktraceAction | ContextQueryFrameAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for thread/frame queries."""


class ContextManageSelectThreadAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionSelectThreadName = Field(
        ...,
        description="Select the current thread",
    )
    context: ThreadSelectorArgs


class ContextManageSelectFrameAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionSelectFrameName = Field(
        ...,
        description="Select the current frame",
    )
    context: FrameSelectorArgs


class ContextManageArgs(
    RootModel[
        Annotated[
            ContextManageSelectThreadAction | ContextManageSelectFrameAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for thread/frame selection."""
