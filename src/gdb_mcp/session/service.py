"""Session service composition root."""

from __future__ import annotations

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
    SessionStartInfo,
    SessionStatusSnapshot,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
)
from ..transport import MiClient
from ..transport.protocols import GdbControllerFactoryProtocol, GdbControllerProtocol
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
        controller_factory: GdbControllerFactoryProtocol,
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
        self._lifecycle = SessionLifecycleService(self.runtime, self._command_runner)
        self._execution = SessionExecutionService(self.runtime, self._command_runner)
        self._breakpoints = SessionBreakpointService(self._command_runner)
        self._inspection = SessionInspectionService(self.runtime, self._command_runner)

    @property
    def controller(self) -> GdbControllerProtocol | None:
        """Expose the underlying controller for orchestration and tests."""

        return self.runtime.controller

    @property
    def state(self) -> SessionState:
        """Expose the session lifecycle state."""

        return self.runtime.state

    @property
    def config(self) -> SessionConfig | None:
        """Expose the normalized session configuration."""

        return self.runtime.config

    @property
    def is_running(self) -> bool:
        """Expose whether the session is active."""

        return self.runtime.is_running

    @property
    def target_loaded(self) -> bool:
        """Expose whether a debug target is currently loaded."""

        return self.runtime.target_loaded

    @property
    def execution_state(self) -> str:
        """Expose the current inferior execution state."""

        return self.runtime.execution_state

    def start(
        self,
        program: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        gdb_path: str | None = None,
        working_dir: str | None = None,
        core: str | None = None,
    ) -> OperationSuccess[SessionStartInfo] | OperationError:
        """Delegate session startup to the lifecycle service."""

        return self._lifecycle.start(
            program=program,
            args=args,
            init_commands=init_commands,
            env=env,
            gdb_path=gdb_path,
            working_dir=working_dir,
            core=core,
        )

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
        self,
        args: list[str] | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate target start to the execution service."""

        return self._execution.run(args=args, timeout_sec=timeout_sec)

    def attach_process(
        self,
        pid: int,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate process attach to the execution service."""

        return self._execution.attach_process(pid=pid, timeout_sec=timeout_sec)

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

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
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

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Delegate frame selection to the inspection service."""

        return self._inspection.select_frame(frame_number)

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Delegate expression evaluation to the inspection service."""

        return self._inspection.evaluate_expression(expression, thread_id=thread_id, frame=frame)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Delegate local-variable inspection to the inspection service."""

        return self._inspection.get_variables(thread_id, frame)

    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        """Delegate register inspection to the inspection service."""

        return self._inspection.get_registers(thread_id=thread_id, frame=frame)

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
