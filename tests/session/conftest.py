"""Shared fixtures for session-layer tests."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast
from unittest.mock import MagicMock, patch

import pytest

from gdb_mcp.domain import (
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    result_to_mapping,
)
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.gdb_command import GdbCommandResult
from gdb_mcp.session.service import SessionService
from gdb_mcp.transport.mi_models import MiRecord, MiTransportResponse
from gdb_mcp.transport.mi_parser import parse_mi_responses
from gdb_mcp.transport.protocols import (
    ControllerIoManagerProtocol,
    GdbControllerFactoryProtocol,
    GdbControllerProtocol,
    GdbProcessProtocol,
)
from tests.support import attach_controller, gdb_backend


class MiRecordBuilder(Protocol):
    def __call__(
        self, payload: dict[str, object] | None = None, *, message: str = ...
    ) -> dict[str, object]: ...


class MiConsoleBuilder(Protocol):
    def __call__(self, payload: str) -> dict[str, object]: ...


class MiNotifyBuilder(Protocol):
    def __call__(
        self, message: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]: ...


class PromptResponseBuilder(Protocol):
    def __call__(
        self,
        *,
        command_responses: list[dict[str, object]] | None = None,
        async_notifications: list[dict[str, object]] | None = None,
        timed_out: bool = False,
        error: str | None = None,
        fatal: bool = False,
    ) -> MiTransportResponse: ...


class CommandResultBuilder(Protocol):
    def __call__(
        self,
        command: str,
        *,
        result: StructuredPayload | None = None,
        output: str | None = None,
    ) -> OperationSuccess[GdbCommandResult]: ...


class ScriptedSessionBuilder(Protocol):
    def __call__(
        self, *response_batches: list[dict[str, object]]
    ) -> tuple[SessionService, ScriptedController]: ...


class ConsoleRowsBuilder(Protocol):
    def __call__(self, *rows: str) -> list[dict[str, object]]: ...


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

    attach_controller(
        session_service,
        MagicMock(
            spec=GdbControllerProtocol,
            gdb_process=MagicMock(spec=GdbProcessProtocol, poll=MagicMock(return_value=None)),
        ),
    )
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
        self.io_manager: ControllerIoManagerProtocol = MagicMock(spec=ControllerIoManagerProtocol)
        self.io_manager.stdin = self._stdin
        self._response_batches = [list(batch) for batch in response_batches]
        self._emit_empty = False
        self.gdb_process: GdbProcessProtocol | None = MagicMock(
            spec=GdbProcessProtocol, poll=MagicMock(return_value=None)
        )
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
        attach_controller(session_service, controller)
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
    ) -> OperationSuccess[GdbCommandResult]:
        return OperationSuccess(
            GdbCommandResult(
                command,
                parse_mi_responses(
                    [
                        {"type": "console", "payload": output or ""},
                        {
                            "type": "result",
                            "message": "done",
                            "payload": (result or {}).get("result"),
                        },
                    ]
                ),
            )
        )

    return build


@pytest.fixture
def prompt_response() -> PromptResponseBuilder:
    """Build a typed transport response for prompt waits."""

    def build(
        *,
        command_responses: list[dict[str, object]] | None = None,
        async_notifications: list[dict[str, object]] | None = None,
        timed_out: bool = False,
        error: str | None = None,
        fatal: bool = False,
    ) -> MiTransportResponse:
        return MiTransportResponse(
            command_responses=command_responses or [],
            async_notifications=async_notifications or [],
            timed_out=timed_out,
            error=error,
            fatal=fatal,
        )

    return build


@pytest.fixture
def inferiors_console(
    mi_console: MiConsoleBuilder, mi_result: MiRecordBuilder
) -> ConsoleRowsBuilder:
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
        prompt: MiTransportResponse | None = None,
        initial_output: list[dict[str, object]] | None = None,
        probe_target_loaded: bool | None = True,
        execute: (
            Callable[..., OperationResult[GdbCommandResult]]
            | list[OperationResult[GdbCommandResult]]
            | None
        ) = None,
        controller_side_effect: BaseException | None = None,
        program: str | None = None,
        debugger: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        debugger_path: str | None = None,
        debugger_args: list[str] | None = None,
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
                    gdb_backend(self.service)._command_runner,
                    "send_command_and_wait_for_prompt",
                    return_value=response,
                )
            )
            stack.enter_context(
                patch.object(
                    gdb_backend(self.service)._command_runner,
                    "execute_command_result",
                    side_effect=side_effect,
                )
            )
            if probe_target_loaded is not None:
                stack.enter_context(
                    patch.object(
                        gdb_backend(self.service)._lifecycle,
                        "_probe_target_loaded",
                        return_value=probe_target_loaded,
                    )
                )
            if initial_output is not None:
                stack.enter_context(
                    patch.object(
                        gdb_backend(self.service).transport,
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
                    debugger_path=debugger_path,
                    debugger_args=debugger_args,
                    working_dir=working_dir,
                    core=core,
                    endpoint=endpoint,
                    rr_replay=rr_replay,
                )
            )

    def _prompt_response(self, console: Sequence[str]) -> MiTransportResponse:
        return self.maker.prompt_response(
            command_responses=[
                *(self.maker.mi_console(payload) for payload in console),
                self.maker.mi_result(),
            ]
        )

    def _record_execute(self, command: str, **kwargs: object) -> OperationSuccess[GdbCommandResult]:
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
        controller_factory=MagicMock(spec=GdbControllerFactoryProtocol),
        maker=_RecordBuilders(
            prompt_response=prompt_response,
            command_result=command_result,
            mi_result=mi_result,
            mi_console=mi_console,
        ),
    )
    monkeypatch.setattr("gdb_mcp.session.factory.GdbController", harness.controller_factory)
    return harness


@pytest.fixture(autouse=True)
def _posix_startup_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep session unit tests on the POSIX startup contract.

    Windows sessions also enable async MI, which is covered by its own test;
    the scripted sequences here describe the platform-independent prologue.
    """

    from gdb_mcp.session import lifecycle

    monkeypatch.setattr(lifecycle, "_ASYNC_MI_INTERRUPT", False)


@pytest.fixture(autouse=True)
def _linux_rr_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scripted rr tests on the Linux launch contract.

    rr has no build outside Linux; the platform guard has its own test.
    """

    from gdb_mcp.session import lifecycle

    monkeypatch.setattr(lifecycle, "_RR_REPLAY_SUPPORTED", True)
