"""Session query and lifecycle request models."""

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
from .selectors import EmptyQuery


class SessionQueryListAction(StrictArgsModel):
    action: shared_contracts.ActionListName = Field(..., description="List all active sessions")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionQueryStatusAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionStatusName = Field(..., description="Query one live session")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionQueryArgs(
    RootModel[
        Annotated[
            SessionQueryListAction | SessionQueryStatusAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for session queries."""


class SessionManageStopAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionStopName = Field(..., description="Stop one live session")
    session: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionManageArgs(
    RootModel[
        Annotated[
            SessionManageStopAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for session lifecycle mutations."""
