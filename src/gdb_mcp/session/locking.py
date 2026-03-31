"""Shared workflow-lock accessors for session-scoped orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .protocols import LockProtocol

if TYPE_CHECKING:
    from .service import SessionService


def session_workflow_context(session: "SessionService") -> LockProtocol:
    """Return the required workflow lock for one live session."""

    return session.runtime.workflow_lock
