"""Session service composition root."""

from __future__ import annotations

from typing import Any

from ..domain import (
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CommandExecutionInfo,
    ExpressionValueInfo,
    FrameInfo,
    FrameSelectionInfo,
    FunctionCallInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    SessionMessage,
    SessionStatusSnapshot,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
)

from ..transport import MiClient, is_cli_command, parse_mi_responses, wrap_cli_command
from .breakpoints import SessionBreakpointService
from .command_runner import SessionCommandRunner
from .config import SessionConfig
from .constants import (
    DEFAULT_TIMEOUT_SEC,
    INITIAL_COMMAND_TOKEN,
    POLL_TIMEOUT_SEC,
)
from .execution import SessionExecutionService
from .inspection import SessionInspectionService
from .lifecycle import SessionLifecycleService
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .runtime import SessionRuntime
from .state import SessionState


class SessionService:
    """
    Orchestrates one debugger session on top of the transport layer.

    The public API remains on this class, while focused collaborators own the
    lifecycle, execution, breakpoint, and inspection behavior.
    """

    def __init__(
        self,
        *,
        controller_factory: Any,
        os_module: OsModuleProtocol,
        time_module: TimeModuleProtocol,
        initial_command_token: int = INITIAL_COMMAND_TOKEN,
        poll_timeout_sec: float = POLL_TIMEOUT_SEC,
    ):
        transport = MiClient(
            controller_factory=controller_factory,
            initial_command_token=initial_command_token,
            poll_timeout_sec=poll_timeout_sec,
        )
        self.runtime = SessionRuntime(
            transport=transport,
            os_module=os_module,
            time_module=time_module,
        )
        self._command_runner = SessionCommandRunner(self.runtime)
        self._lifecycle = SessionLifecycleService(self)
        self._execution = SessionExecutionService(self)
        self._breakpoints = SessionBreakpointService(self)
        self._inspection = SessionInspectionService(self)

    @property
    def controller(self) -> Any:
        """Expose the underlying controller for orchestration and tests."""

        return self.runtime.controller

    @controller.setter
    def controller(self, value: Any) -> None:
        """Allow tests to replace the underlying controller."""

        self.runtime.controller = value

    @property
    def state(self) -> SessionState:
        """Expose the session lifecycle state."""

        return self.runtime.state

    @state.setter
    def state(self, value: SessionState) -> None:
        self.runtime.state = value

    @property
    def config(self) -> SessionConfig | None:
        """Expose the normalized session configuration."""

        return self.runtime.config

    @config.setter
    def config(self, value: SessionConfig | None) -> None:
        self.runtime.config = value

    @property
    def is_running(self) -> bool:
        """Expose whether the session is active."""

        return self.runtime.is_running

    @is_running.setter
    def is_running(self, value: bool) -> None:
        self.runtime.is_running = value

    @property
    def target_loaded(self) -> bool:
        """Expose whether a debug target is currently loaded."""

        return self.runtime.target_loaded

    @target_loaded.setter
    def target_loaded(self, value: bool) -> None:
        self.runtime.target_loaded = value

    @property
    def _os(self) -> OsModuleProtocol:
        """Compatibility alias for tests that patch the injected os module."""

        return self.runtime.os_module

    @property
    def _time(self) -> TimeModuleProtocol:
        """Compatibility alias for tests that patch the injected time module."""

        return self.runtime.time_module

    def _is_gdb_alive(self) -> bool:
        """Compatibility wrapper for tests and collaborators."""

        return self._command_runner.is_gdb_alive()

    def _send_command_and_wait_for_prompt(
        self, command: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC
    ) -> dict[str, object]:
        """Compatibility wrapper for tests and collaborators."""

        return self._command_runner.send_command_and_wait_for_prompt(command, timeout_sec)

    def _execute_command_result(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Compatibility wrapper for tests and collaborators."""

        if not self.runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self._is_gdb_alive():
            return OperationError(
                message="GDB process has exited - cannot execute command",
                details={"command": command},
            )

        cli_command = is_cli_command(command)
        actual_command = wrap_cli_command(command) if cli_command else command
        result = self._send_command_and_wait_for_prompt(actual_command, timeout_sec)

        if "error" in result:
            return OperationError(
                message=str(result["error"]),
                fatal=bool(result.get("fatal", False)),
                details={"command": command},
            )

        if result.get("timed_out"):
            return OperationError(
                message=f"Timeout waiting for command response after {timeout_sec}s",
                details={"command": command},
            )

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)

        if parsed.is_error_result():
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"command": command},
            )

        if cli_command:
            console_output = "".join(item for item in parsed.console if isinstance(item, str))
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    output=console_output.strip() if console_output else "(no output)",
                )
            )

        return OperationSuccess(CommandExecutionInfo(command=command, result=parsed.to_dict()))

    def start(self, *args: Any, **kwargs: Any) -> OperationSuccess[Any] | OperationError:
        """Delegate session startup to the lifecycle service."""

        return self._lifecycle.start(*args, **kwargs)

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate session shutdown to the lifecycle service."""

        return self._lifecycle.stop()

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        """Delegate status inspection to the lifecycle service."""

        return self._lifecycle.get_status()

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate command execution to the execution service."""

        return self._execution.execute_command(command, timeout_sec)

    def run(
        self, args: list[str] | None = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate target start to the execution service."""

        return self._execution.run(args=args)

    def continue_execution(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate continue to the execution service."""

        return self._execution.continue_execution()

    def step(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate single-step to the execution service."""

        return self._execution.step()

    def next(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate next/step-over to the execution service."""

        return self._execution.next()

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        """Delegate interrupt to the execution service."""

        return self._execution.interrupt()

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        """Delegate function call execution to the execution service."""

        return self._execution.call_function(function_call, timeout_sec)

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Delegate thread inspection to the inspection service."""

        return self._inspection.get_threads()

    def select_thread(self, thread_id: int) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Delegate thread selection to the inspection service."""

        return self._inspection.select_thread(thread_id)

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Delegate backtrace retrieval to the inspection service."""

        return self._inspection.get_backtrace(thread_id, max_frames)

    def get_frame_info(self) -> OperationSuccess[FrameInfo] | OperationError:
        """Delegate current-frame inspection to the inspection service."""

        return self._inspection.get_frame_info()

    def select_frame(self, frame_number: int) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Delegate frame selection to the inspection service."""

        return self._inspection.select_frame(frame_number)

    def evaluate_expression(self, expression: str) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Delegate expression evaluation to the inspection service."""

        return self._inspection.evaluate_expression(expression)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Delegate local-variable inspection to the inspection service."""

        return self._inspection.get_variables(thread_id, frame)

    def get_registers(self) -> OperationSuccess[RegistersInfo] | OperationError:
        """Delegate register inspection to the inspection service."""

        return self._inspection.get_registers()

    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate breakpoint creation to the breakpoint service."""

        return self._breakpoints.set_breakpoint(location, condition, temporary)

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        """Delegate breakpoint listing to the breakpoint service."""

        return self._breakpoints.list_breakpoints()

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint deletion to the breakpoint service."""

        return self._breakpoints.delete_breakpoint(number)

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint enabling to the breakpoint service."""

        return self._breakpoints.enable_breakpoint(number)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint disabling to the breakpoint service."""

        return self._breakpoints.disable_breakpoint(number)
