"""Inferior query and mutation request models."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    Field,
    RootModel,
    field_validator,
)

from ... import contracts as shared_contracts
from ...contracts import (
    InferiorFollowForkMode,
)

from .common import (
    SessionId,
    StrictArgsModel,
    _normalize_optional_text,
)
from .selectors import EmptyQuery


class InferiorQueryListAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionListName = Field(
        ..., description="List inferiors in one live session"
    )
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class InferiorQueryCurrentAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionCurrentName = Field(
        ...,
        description="Inspect the selected inferior",
    )
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class InferiorQueryArgs(
    RootModel[
        Annotated[
            InferiorQueryListAction | InferiorQueryCurrentAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for inferior queries."""


class InferiorCreatePayload(StrictArgsModel):
    executable: str | None = Field(
        None,
        description="Optional executable to associate with the new inferior.",
    )
    make_current: bool = Field(
        False,
        description="Whether to leave the new inferior selected after creation.",
    )

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="executable")


class InferiorIdPayload(StrictArgsModel):
    inferior_id: int = Field(..., gt=0, description="Inferior ID")


class InferiorFollowForkPayload(StrictArgsModel):
    mode: InferiorFollowForkMode = Field(
        ...,
        description="Whether GDB should follow the parent or child after fork/vfork.",
    )


class InferiorDetachOnForkPayload(StrictArgsModel):
    enabled: bool = Field(..., description="Whether GDB should detach from the non-followed fork.")


class InferiorManageCreateAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionCreateName = Field(..., description="Create a new inferior")
    inferior: InferiorCreatePayload = Field(
        default_factory=lambda: InferiorCreatePayload.model_validate({})
    )


class InferiorManageRemoveAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionRemoveName = Field(..., description="Remove one inferior")
    inferior: InferiorIdPayload


class InferiorManageSelectAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionSelectName = Field(..., description="Select the active inferior")
    inferior: InferiorIdPayload


class InferiorManageFollowForkAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionSetFollowForkModeName = Field(
        ...,
        description="Change follow-fork-mode",
    )
    inferior: InferiorFollowForkPayload


class InferiorManageDetachOnForkAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionSetDetachOnForkName = Field(
        ...,
        description="Change detach-on-fork",
    )
    inferior: InferiorDetachOnForkPayload


class InferiorManageArgs(
    RootModel[
        Annotated[
            InferiorManageCreateAction
            | InferiorManageRemoveAction
            | InferiorManageSelectAction
            | InferiorManageFollowForkAction
            | InferiorManageDetachOnForkAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for inferior mutations."""
