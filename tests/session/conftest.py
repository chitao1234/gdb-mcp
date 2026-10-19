"""Shared fixtures for session-layer tests."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from gdb_mcp.domain import (
    CommandExecutionInfo,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    result_to_mapping,
)
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.service import SessionService
from gdb_mcp.transport.mi_models import MiRecord
from gdb_mcp.transport.protocols import (
    ControllerIoManagerProtocol,
    GdbProcessProtocol,
)

MiRecordBuilder = Callable[..., dict[str, object]]
MiConsoleBuilder = Callable[[str], dict[str, object]]
MiNotifyBuilder = Callable[..., dict[str, object]]
PromptResponseBuilder = Callable[..., dict[str, object]]
CommandResultBuilder = Callable[..., OperationSuccess[CommandExecutionInfo]]
ScriptedSessionBuilder = Callable[..., tuple[SessionService, "ScriptedController"]]


@dataclass(frozen=True)
class _RecordBuilders:
    """The record builders a :class:`StartHarness` needs, passed as one bundle."""

    prompt_response: PromptResponseBuilder
    command_result: CommandResultBuilder
    mi_result: MiRecordBuilder
    mi_console: MiConsoleBuilder


@pytest.fixture
def session_service() -> SessionService:
    """Create a fresh default session service for one test."""

    return create_default_session_service()


@pytest.fixture
def running_session(session_service: SessionService) -> SessionService:
    """Create a session with a controller attached and marked running."""

    session_service.runtime.controller = MagicMock()
    session_service.runtime.mark_ready()
    return session_service


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.flush_count = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        self.flush_count += 1


class ScriptedController:
    """Controller double that returns scripted GDB/MI response batches."""

    def __init__(self, response_batches: list[list[dict[str, object]]]) -> None:
        self._stdin = _FakeStdin()
        self.io_manager: ControllerIoManagerProtocol = MagicMock()
        self.io_manager.stdin = self._stdin
        self._response_batches = [list(batch) for batch in response_batches]
        self._emit_empty = False
        self.gdb_process: GdbProcessProtocol | None = MagicMock()
        self.exit_called = False

    @property
    def decoded_writes(self) -> list[str]:
        """The commands written to the fake stdin, decoded for readable assertions."""

        return [data.decode() for data in self._stdin.writes]

    def _current_token(self) -> int | None:
        if not self._stdin.writes:
            return None
        command = self._stdin.writes[-1].decode()
        match = re.match(r"(\d+)", command)
        if match is None:
            return None
        return int(match.group(1))

    def get_gdb_response(
        self, *, timeout_sec: float, raise_error_on_timeout: bool
    ) -> list[MiRecord]:
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
            if (
                normalized.get("type") == "result"
                and "token" not in normalized
                and token is not None
            ):
                normalized["token"] = token
            batch.append(normalized)

        self._emit_empty = True
        return batch

    def exit(self) -> None:
        self.exit_called = True


@pytest.fixture
def mi_result() -> MiRecordBuilder:
    """Build one MI result record."""

    def build(
        payload: dict[str, object] | None = None,
        *,
        message: str = "done",
    ) -> dict[str, object]:
        return {"type": "result", "message": message, "payload": payload}

    return build


@pytest.fixture
def mi_console() -> MiConsoleBuilder:
    """Build one MI console stream record."""

    def build(payload: str) -> dict[str, object]:
        return {"type": "console", "payload": payload}

    return build


@pytest.fixture
def mi_notify() -> MiNotifyBuilder:
    """Build one MI async notification record."""

    def build(message: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        return {"type": "notify", "message": message, "payload": payload or {}}

    return build


@pytest.fixture
def scripted_running_session(
    session_service: SessionService,
) -> ScriptedSessionBuilder:
    """Create a running session backed by a scripted controller."""

    def build(
        *response_batches: list[dict[str, object]]
    ) -> tuple[SessionService, ScriptedController]:
        controller = ScriptedController(list(response_batches))
        session_service.runtime.controller = controller
        session_service.runtime.mark_ready()
        return session_service, controller

    return build


@pytest.fixture
def command_result() -> CommandResultBuilder:
    """Build a typed command-execution result for session-layer mocks."""

    def build(
        command: str,
        *,
        result: StructuredPayload | None = None,
        output: str | None = None,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return OperationSuccess(CommandExecutionInfo(command=command, result=result, output=output))

    return build


@pytest.fixture
def prompt_response() -> PromptResponseBuilder:
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


@pytest.fixture
def inferiors_console(
    mi_console: MiConsoleBuilder, mi_result: MiRecordBuilder
) -> Callable[..., list[dict[str, object]]]:
    """Build the ``info inferiors`` console batch from one string per row.

    Each row must be the raw fixed-width line GDB prints, for example
    ``"* 2    <null>                                 /tmp/app "``.
    """

    header = "  Num  Description       Connection           Executable        \n"

    def build(*rows: str) -> list[dict[str, object]]:
        return [mi_console(header), *(mi_console(f"{row}\n") for row in rows), mi_result()]

    return build


@dataclass
class StartHarness:
    """Drive ``SessionService.start()`` with its collaborators stubbed out.

    Calling the harness runs one ``start()`` with GDB controller creation, the
    command/prompt round trip, the CLI-command path, and the target-loaded probe
    replaced by doubles. ``executed`` records the CLI commands start() ran and
    ``controller_factory`` exposes the patched ``GdbController`` for command
    assertions.
    """

    service: SessionService
    controller_factory: MagicMock
    maker: _RecordBuilders
    executed: list[str] = field(default_factory=list)

    def __call__(
        self,
        *,
        console: Sequence[str] = (),
        prompt: dict[str, object] | None = None,
        initial_output: list[dict[str, object]] | None = None,
        probe_target_loaded: bool | None = True,
        execute: (
            Callable[..., OperationResult[CommandExecutionInfo]]
            | list[OperationResult[CommandExecutionInfo]]
            | None
        ) = None,
        controller_side_effect: BaseException | None = None,
        program: str | None = None,
        debugger: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        gdb_path: str | None = None,
        gdb_args: list[str] | None = None,
        working_dir: str | None = None,
        core: str | None = None,
        endpoint: str | None = None,
        rr_replay: str | None = None,
    ) -> StructuredPayload:
        """Run one ``start()`` and return its serialized result payload."""

        if controller_side_effect is not None:
            self.controller_factory.side_effect = controller_side_effect

        response = prompt if prompt is not None else self._prompt_response(console)
        side_effect = execute if execute is not None else self._record_execute

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    self.service._command_runner,
                    "send_command_and_wait_for_prompt",
                    return_value=response,
                )
            )
            stack.enter_context(
                patch.object(
                    self.service._command_runner,
                    "execute_command_result",
                    side_effect=side_effect,
                )
            )
            if probe_target_loaded is not None:
                stack.enter_context(
                    patch.object(
                        self.service._lifecycle,
                        "_probe_target_loaded",
                        return_value=probe_target_loaded,
                    )
                )
            if initial_output is not None:
                stack.enter_context(
                    patch.object(
                        self.service.runtime.transport,
                        "read_initial_output",
                        return_value=initial_output,
                    )
                )
            return result_to_mapping(
                self.service.start(
                    program=program,
                    debugger=debugger,
                    args=args,
                    init_commands=init_commands,
                    env=env,
                    gdb_path=gdb_path,
                    gdb_args=gdb_args,
                    working_dir=working_dir,
                    core=core,
                    endpoint=endpoint,
                    rr_replay=rr_replay,
                )
            )

    def _prompt_response(self, console: Sequence[str]) -> dict[str, object]:
        return self.maker.prompt_response(
            command_responses=[
                *(self.maker.mi_console(payload) for payload in console),
                self.maker.mi_result(),
            ]
        )

    def _record_execute(
        self, command: str, **kwargs: object
    ) -> OperationSuccess[CommandExecutionInfo]:
        del kwargs
        self.executed.append(command)
        return self.maker.command_result(command, output="")

    def command_of(self) -> list[str]:
        """The GDB command line the controller factory was called with."""

        call = self.controller_factory.call_args
        assert call is not None, "the factory was never called"
        return cast(list[str], call.kwargs["command"])


@pytest.fixture
def start_session(
    monkeypatch: pytest.MonkeyPatch,
    session_service: SessionService,
    prompt_response: PromptResponseBuilder,
    command_result: CommandResultBuilder,
    mi_result: MiRecordBuilder,
    mi_console: MiConsoleBuilder,
) -> StartHarness:
    """Patch GDB controller creation and expose a :class:`StartHarness`."""

    harness = StartHarness(
        service=session_service,
        controller_factory=MagicMock(),
        maker=_RecordBuilders(
            prompt_response=prompt_response,
            command_result=command_result,
            mi_result=mi_result,
            mi_console=mi_console,
        ),
    )
    monkeypatch.setattr("gdb_mcp.session.factory.GdbController", harness.controller_factory)
    return harness
