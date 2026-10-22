"""Tests for connecting a session to an externally started GDB/MI endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gdb_mcp.domain import result_to_mapping
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.transport import EndpointError
from gdb_mcp.transport.protocols import ControllerIoManagerProtocol, ControllerStdinProtocol
from tests.support import gdb_backend, text


class _ScriptedExternalController:
    """Controller double that answers the commands the lifecycle sends."""

    def __init__(self, *, info_files_output: str = 'Symbols from "/bin/true".\n') -> None:
        self.io_manager = MagicMock(spec=ControllerIoManagerProtocol)
        self.io_manager.stdin = MagicMock(spec=ControllerStdinProtocol)
        self.gdb_process = None
        self.exited = False
        self.answered: set[str] = set()
        self._info_files_output = info_files_output

    def _last_command(self) -> str | None:
        call = self.io_manager.stdin.write.call_args
        if call is None:
            return None
        written: bytes = call.args[0]
        return written.decode()

    def get_gdb_response(
        self, *, timeout_sec: float, raise_error_on_timeout: bool
    ) -> list[dict[str, object]]:
        del timeout_sec, raise_error_on_timeout
        command = self._last_command()
        if command is None:
            return []
        token_text, _, body = command.partition("-")
        if not token_text.isdigit() or command in self.answered:
            return []
        self.answered.add(command)

        responses: list[dict[str, object]] = []
        if "info files" in body:
            responses.append({"type": "console", "payload": self._info_files_output})
        responses.append(
            {"type": "result", "message": "done", "payload": {}, "token": int(token_text)}
        )
        return responses

    def exit(self) -> None:
        self.exited = True


def test_start_with_endpoint_adopts_external_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    """An endpoint session must adopt a controller instead of spawning gdb."""

    session = create_default_session_service()
    controller = _ScriptedExternalController()
    created: dict[str, str] = {}

    def fake_create(
        endpoint: str, *, time_to_check_for_additional_output_sec: float = 0.2
    ) -> _ScriptedExternalController:
        created["endpoint"] = endpoint
        return controller

    monkeypatch.setattr("gdb_mcp.session.lifecycle.create_external_controller", fake_create)

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("transport.start (spawn) must not run for endpoint sessions")

    monkeypatch.setattr(gdb_backend(session).transport, "start", boom)

    payload = result_to_mapping(session.start(endpoint="tcp://127.0.0.1:4399"))

    assert payload["status"] == "success"
    assert payload["endpoint"] == "tcp://127.0.0.1:4399"
    assert payload["target_loaded"] is True
    assert created["endpoint"] == "tcp://127.0.0.1:4399"
    assert gdb_backend(session).transport.controller is controller


def test_start_rejects_endpoint_with_program() -> None:
    """Connecting to an endpoint must not also request a target."""

    session = create_default_session_service()

    payload = result_to_mapping(session.start(program="/bin/true", endpoint="tcp://127.0.0.1:1"))

    assert payload["status"] == "error"
    assert "do not also pass" in text(payload["message"])
    assert session.has_controller is False


def test_start_reports_endpoint_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed endpoint connection surfaces as an actionable error."""

    session = create_default_session_service()

    def boom(endpoint: str, *, time_to_check_for_additional_output_sec: float = 0.2) -> None:
        raise EndpointError(f"Could not reach MI endpoint {endpoint}")

    monkeypatch.setattr("gdb_mcp.session.lifecycle.create_external_controller", boom)

    payload = result_to_mapping(session.start(endpoint="unix:///tmp/nope.sock"))

    assert payload["status"] == "error"
    assert "Could not reach MI endpoint unix:///tmp/nope.sock" in text(payload["message"])
    assert session.has_controller is False
