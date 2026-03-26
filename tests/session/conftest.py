"""Shared fixtures for session-layer tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gdb_mcp.domain import CommandExecutionInfo, OperationSuccess
from gdb_mcp.session.factory import create_default_session_service


@pytest.fixture
def session_service():
    """Create a fresh default session service for one test."""

    return create_default_session_service()


@pytest.fixture
def running_session(session_service):
    """Create a session with a controller attached and marked running."""

    session_service.controller = MagicMock()
    session_service.is_running = True
    return session_service


@pytest.fixture
def command_result():
    """Build a typed command-execution result for session-layer mocks."""

    def build(
        command: str,
        *,
        result: dict[str, object] | None = None,
        output: str | None = None,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return OperationSuccess(CommandExecutionInfo(command=command, result=result, output=output))

    return build


@pytest.fixture
def prompt_response():
    """Build a normalized transport response payload for prompt waits."""

    def build(
        *,
        command_responses: list[dict[str, object]] | None = None,
        async_notifications: list[dict[str, object]] | None = None,
        timed_out: bool = False,
        error: str | None = None,
        fatal: bool = False,
    ) -> dict[str, object]:
        response: dict[str, object] = {
            "command_responses": command_responses or [],
            "async_notifications": async_notifications or [],
            "timed_out": timed_out,
        }
        if error is not None:
            response["error"] = error
        if fatal:
            response["fatal"] = True
        return response

    return build
