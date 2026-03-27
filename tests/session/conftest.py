"""Shared fixtures for session-layer tests."""

from __future__ import annotations

import re
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

    session_service.runtime.controller = MagicMock()
    session_service.runtime.mark_ready()
    return session_service


class _FakeStdin:
    def __init__(self):
        self.writes: list[bytes] = []
        self.flush_count = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        self.flush_count += 1


class ScriptedController:
    """Controller double that returns scripted GDB/MI response batches."""

    def __init__(self, response_batches: list[list[dict[str, object]]]):
        self.io_manager = MagicMock()
        self.io_manager.stdin = _FakeStdin()
        self._response_batches = [list(batch) for batch in response_batches]
        self._emit_empty = False
        self.gdb_process = MagicMock()
        self.exit_called = False

    def _current_token(self) -> int | None:
        if not self.io_manager.stdin.writes:
            return None
        command = self.io_manager.stdin.writes[-1].decode()
        match = re.match(r"(\d+)", command)
        if match is None:
            return None
        return int(match.group(1))

    def get_gdb_response(self, *, timeout_sec: float, raise_error_on_timeout: bool):
        del timeout_sec, raise_error_on_timeout

        if self._emit_empty:
            self._emit_empty = False
            return []

        if not self._response_batches:
            return []

        token = self._current_token()
        batch = []
        for response in self._response_batches.pop(0):
            normalized = dict(response)
            if normalized.get("type") == "result" and "token" not in normalized and token is not None:
                normalized["token"] = token
            batch.append(normalized)

        self._emit_empty = True
        return batch

    def exit(self) -> None:
        self.exit_called = True


@pytest.fixture
def mi_result():
    """Build one MI result record."""

    def build(
        payload: dict[str, object] | None = None,
        *,
        message: str = "done",
    ) -> dict[str, object]:
        return {"type": "result", "message": message, "payload": payload}

    return build


@pytest.fixture
def mi_console():
    """Build one MI console stream record."""

    def build(payload: str) -> dict[str, object]:
        return {"type": "console", "payload": payload}

    return build


@pytest.fixture
def mi_notify():
    """Build one MI async notification record."""

    def build(message: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        return {"type": "notify", "message": message, "payload": payload or {}}

    return build


@pytest.fixture
def scripted_running_session(session_service):
    """Create a running session backed by a scripted controller."""

    def build(*response_batches: list[dict[str, object]]):
        controller = ScriptedController(list(response_batches))
        session_service.runtime.controller = controller
        session_service.runtime.mark_ready()
        return session_service, controller

    return build


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
