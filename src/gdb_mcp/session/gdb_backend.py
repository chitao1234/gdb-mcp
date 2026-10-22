"""The GDB/MI backend: spawn GDB, speak MI, publish its records."""

from __future__ import annotations

from typing import Literal

from ..contracts import DEBUGGER_GDB
from ..domain import (
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CatchpointType,
    CommandExecutionInfo,
    DetachOnForkInfo,
    DisassemblyInfo,
    ExpressionValueInfo,
    FinishInfo,
    FollowForkMode,
    FollowForkModeInfo,
    FrameInfo,
    FrameSelectionInfo,
    FunctionCallInfo,
    InferiorAddInfo,
    InferiorListInfo,
    InferiorRemoveInfo,
    InferiorSelectionInfo,
    LocationExecutionInfo,
    MemoryReadInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
    SourceContextInfo,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from ..transport import MiClient
from ..transport.protocols import GdbControllerFactoryProtocol
from .backends import BackendCapabilities
from .breakpoints import SessionBreakpointService
from .command_runner import SessionCommandRunner
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC, INITIAL_COMMAND_TOKEN, POLL_TIMEOUT_SEC
from .execution import SessionExecutionService
from .inspection import SessionInspectionService
from .lifecycle import SessionLifecycleService
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .runtime import SessionRuntime


class GdbMiBackend:
    """Drive GDB through its machine interface."""

    def __init__(
        self,
        *,
        controller_factory: GdbControllerFactoryProtocol,
        os_module: OsModuleProtocol,
        time_module: TimeModuleProtocol,
        initial_command_token: int = INITIAL_COMMAND_TOKEN,
        poll_timeout_sec: float = POLL_TIMEOUT_SEC,
    ):
        self._transport = MiClient(
            controller_factory=controller_factory,
            initial_command_token=initial_command_token,
            poll_timeout_sec=poll_timeout_sec,
        )
        self.runtime = SessionRuntime(
            os_module=os_module,
            time_module=time_module,
        )
        self._command_runner = SessionCommandRunner(self.runtime, self._transport)
        self._lifecycle = SessionLifecycleService(
            self.runtime, self._command_runner, self._transport
        )
        self._execution = SessionExecutionService(
            self.runtime, self._command_runner, self._transport
        )
        self._breakpoints = SessionBreakpointService(self._command_runner)
        self._inspection = SessionInspectionService(self.runtime, self._command_runner)
        self._capabilities = BackendCapabilities.full_surface(DEBUGGER_GDB)

    @property
    def transport(self) -> MiClient:
        """Expose the MI transport this backend owns."""

        return self._transport

    @property
    def capabilities(self) -> BackendCapabilities:
        """GDB implements the whole operation surface."""

        return self._capabilities

    def start(
        self,
        config: SessionConfig,
    ) -> OperationSuccess[SessionStartupInfo] | OperationError:
        """Delegate session startup to the lifecycle service."""

        self._execution.reset_capability_cache()
        self._inspection.reset_capability_cache()
        return self._lifecycle.start(config)

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
        wait_for_stop: bool = True,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate target start to the execution service."""

        return self._execution.run(
            args=args,
            timeout_sec=timeout_sec,
            wait_for_stop=wait_for_stop,
        )

    def add_inferior(
        self,
        *,
        executable: str | None = None,
        make_current: bool = False,
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        """Delegate inferior creation to the execution service."""

        return self._execution.add_inferior(
            executable=executable,
            make_current=make_current,
        )

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        """Delegate inferior removal to the execution service."""

        return self._execution.remove_inferior(inferior_id)

    def attach_process(
        self,
        pid: int,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate process attach to the execution service."""

        return self._execution.attach_process(pid=pid, timeout_sec=timeout_sec)

    def set_follow_fork_mode(
        self,
        mode: FollowForkMode,
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError:
        """Delegate follow-fork-mode changes to the execution service."""

        return self._execution.set_follow_fork_mode(mode)

    def set_detach_on_fork(
        self,
        enabled: bool,
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError:
        """Delegate detach-on-fork changes to the execution service."""

        return self._execution.set_detach_on_fork(enabled)

    def continue_execution(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate continue to the execution service."""

        return self._execution.continue_execution(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
            reverse=reverse,
        )

    def wait_for_stop(
        self,
        *,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        stop_reasons: tuple[str, ...] = (),
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        """Delegate stop waiting to the execution service."""

        return self._execution.wait_for_stop(
            timeout_sec=timeout_sec,
            stop_reasons=stop_reasons,
        )

    def step(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate single-step to the execution service."""

        return self._execution.step(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
            reverse=reverse,
        )

    def next(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate next/step-over to the execution service."""

        return self._execution.next(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
            reverse=reverse,
        )

    def step_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate instruction-step to the execution service."""

        return self._execution.step_instruction(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
            reverse=reverse,
        )

    def next_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate instruction-step-over to the execution service."""

        return self._execution.next_instruction(
            wait_for_stop=wait_for_stop,
            timeout_sec=timeout_sec,
            reverse=reverse,
        )

    def return_from_frame(
        self, *, value: str | None = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate force-return to the execution service."""

        return self._execution.return_from_frame(value=value)

    def jump(
        self,
        location: str,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Delegate relocation and resume to the execution service."""

        return self._execution.jump(location, wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)

    def until(
        self,
        location: str,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Delegate frame-scoped execution to the execution service."""

        return self._execution.until(location, wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)

    def finish(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        """Delegate finish/step-out to the execution service."""

        return self._execution.finish(
            timeout_sec=timeout_sec,
            reverse=reverse,
            wait_for_stop=wait_for_stop,
        )

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

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """Delegate inferior inventory inspection to the inspection service."""

        return self._inspection.list_inferiors()

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        """Delegate inferior selection to the inspection service."""

        return self._inspection.select_inferior(inferior_id)

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

    def get_frame_info(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[FrameInfo] | OperationError:
        """Delegate current-frame inspection to the inspection service."""

        return self._inspection.get_frame_info(thread_id=thread_id, frame=frame)

    def disassemble(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        start_address: str | None = None,
        end_address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        instruction_count: int = 32,
        mode: Literal["assembly", "mixed"] = "mixed",
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        """Delegate structured disassembly to the inspection service."""

        return self._inspection.disassemble(
            thread_id=thread_id,
            frame=frame,
            function=function,
            address=address,
            start_address=start_address,
            end_address=end_address,
            file=file,
            line=line,
            instruction_count=instruction_count,
            mode=mode,
        )

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

    def read_memory(
        self,
        address: str,
        count: int,
        *,
        offset: int = 0,
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Delegate memory reads to the inspection service."""

        return self._inspection.read_memory(address, count, offset=offset)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Delegate local-variable inspection to the inspection service."""

        return self._inspection.get_variables(thread_id, frame)

    def get_source_context(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        context_before: int = 5,
        context_after: int = 5,
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        """Delegate structured source-context retrieval to the inspection service."""

        return self._inspection.get_source_context(
            thread_id=thread_id,
            frame=frame,
            function=function,
            address=address,
            file=file,
            line=line,
            start_line=start_line,
            end_line=end_line,
            context_before=context_before,
            context_after=context_after,
        )

    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        """Delegate register inspection to the inspection service."""

        return self._inspection.get_registers(
            thread_id=thread_id,
            frame=frame,
            register_numbers=register_numbers,
            register_names=register_names,
            include_vector_registers=include_vector_registers,
            max_registers=max_registers,
            value_format=value_format,
        )

    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate breakpoint creation to the breakpoint service."""

        return self._breakpoints.set_breakpoint(location, condition, temporary)

    def set_watchpoint(
        self,
        expression: str,
        *,
        access: WatchpointAccessType = "write",
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate watchpoint creation to the breakpoint service."""

        return self._breakpoints.set_watchpoint(expression, access=access)

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate watchpoint deletion to the breakpoint service."""

        return self._breakpoints.delete_watchpoint(number)

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate catchpoint creation to the breakpoint service."""

        return self._breakpoints.set_catchpoint(
            kind,
            argument=argument,
            temporary=temporary,
        )

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        """Delegate breakpoint listing to the breakpoint service."""

        return self._breakpoints.list_breakpoints()

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate breakpoint lookup to the breakpoint service."""

        return self._breakpoints.get_breakpoint(number)

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint deletion to the breakpoint service."""

        return self._breakpoints.delete_breakpoint(number)

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint enabling to the breakpoint service."""

        return self._breakpoints.enable_breakpoint(number)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint disabling to the breakpoint service."""

        return self._breakpoints.disable_breakpoint(number)

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate breakpoint updates to the breakpoint service."""

        return self._breakpoints.update_breakpoint(
            number,
            condition=condition,
            clear_condition=clear_condition,
        )
