"""Unit tests for shared session workflow locking helpers."""

from __future__ import annotations

from gdb_mcp.session.locking import session_workflow_context


def test_session_workflow_context_returns_runtime_workflow_lock(session_service):
    """The shared helper should expose the session runtime workflow lock directly."""

    assert session_workflow_context(session_service) is session_service.runtime.workflow_lock
