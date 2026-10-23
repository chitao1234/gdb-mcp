"""Thread and frame context request models."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from ... import contracts as shared_contracts
from .common import (
    SessionId,
    StrictArgsModel,
    _normalize_optional_text,
)
from .selectors import (
    FrameSelectorArgs,
    ThreadFrameContextArgs,
    ThreadSelectorArgs,
)


class ContextThreadsQueryArgs(StrictArgsModel):
    waiting_on_channel: str | None = Field(
        None, description="Channel expression whose waiting goroutines to list (Delve 1.21.1+)"
    )
    context: ThreadFrameContextArgs | None = Field(
        None, description="Scope in which to evaluate the channel expression"
    )
    max_results: int | None = Field(None, gt=0, description="Maximum number of threads to return")

    @field_validator("waiting_on_channel")
    @classmethod
    def validate_channel(cls, value: str | None) -> str | None:
        normalized = _normalize_optional_text(value, field_name="waiting_on_channel")
        if value is not None and normalized is None:
            raise ValueError("waiting_on_channel must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_context(self) -> ContextThreadsQueryArgs:
        if self.context is not None and self.waiting_on_channel is None:
            raise ValueError("context requires waiting_on_channel")
        return self


class ContextBacktraceQueryArgs(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    max_frames: int = Field(100, gt=0, description="Maximum number of frames to return")
    include_defers: bool = Field(
        False, description="Include pending deferred calls per frame (Delve)"
    )


class ContextFrameQueryArgs(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    frame: int | None = Field(None, ge=0, description="Optional frame override")


class ContextQueryThreadsAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionThreadsName = Field(..., description="List threads")
    query: ContextThreadsQueryArgs = Field(default_factory=ContextThreadsQueryArgs)


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
