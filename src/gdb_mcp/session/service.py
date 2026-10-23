"""The tool-facing session service.

Orchestrates one debugger session on top of a :class:`DebuggerBackend`: it owns
the service-level features (batches, capture bundles, the campaign entry point)
and the state a caller reads, while every debugger operation is delegated to the
backend that implements it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ..domain import (
    BacktraceInfo,
    BatchExecutionInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CaptureBundleInfo,
    CatchpointType,
    CommandExecutionInfo,
    CommandTranscriptEntry,
    DetachOnForkInfo,
    DisassemblyInfo,
    ExecutionState,
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
    MemoryCaptureRange,
    MemoryReadInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
    SourceContextInfo,
    StopEvent,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from .backends import BackendCapabilities, DebuggerBackend
from .capture import SessionCaptureService
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .requests import (
    DEFAULT_EXECUTION_OPTIONS,
    DEFAULT_THREAD_QUERY,
    DisassemblyRequest,
    ExecutionOptions,
    SourceContextRequest,
    ThreadQuery,
)
from .runtime import SessionRuntime
from .state import SessionState
from .workflow import BatchStepInvocation, BatchStepTemplate, SessionWorkflowService


class SessionService:
    """Orchestrates one debugger session on top of a debugger backend."""

    def __init__(self, *, backend: DebuggerBackend) -> None:
        self._backend = backend
        self._workflow = SessionWorkflowService(backend.runtime)
        self._capture = SessionCaptureService(backend)

    @property
    def backend(self) -> DebuggerBackend:
        """Expose the debugger backend this session drives."""

        return self._backend

    @property
    def runtime(self) -> SessionRuntime:
        """Expose the mutable session state the backend keeps up to date."""

        return self._backend.runtime

    @property
    def state(self) -> SessionState:
        """Expose the session lifecycle state."""

        return self.runtime.state

    @property
    def config(self) -> SessionConfig | None:
        """Expose the normalized session configuration."""

        return self.runtime.config

    @property
    def has_controller(self) -> bool:
        """Expose whether a debugger process or stream is attached."""

        return self._backend.has_controller

    @property
    def capabilities(self) -> BackendCapabilities:
        """Expose what the backend can do."""

        return self._backend.capabilities

    @property
    def is_running(self) -> bool:
        """Expose whether the session is active."""

        return self.runtime.is_running

    @property
    def target_loaded(self) -> bool:
        """Expose whether a debug target is currently loaded."""

        return self.runtime.target_loaded

    @property
    def execution_state(self) -> ExecutionState:
        """Expose the current inferior execution state."""

        return self.runtime.execution_state

    @property
    def last_stop_event(self) -> StopEvent | None:
        """Expose the most recent structured stop event."""

        return self.runtime.last_stop_event

    @property
    def stop_history(self) -> tuple[StopEvent, ...]:
        """Expose the bounded structured stop history."""

        return tuple(self.runtime.stop_history)

    @property
    def command_transcript(self) -> tuple[CommandTranscriptEntry, ...]:
        """Expose the bounded command transcript."""

        return tuple(self.runtime.command_transcript)

    def start(
        self,
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
    ) -> OperationSuccess[SessionStartupInfo] | OperationError:
        """Delegate session startup to the debugger backend."""

        selected_debugger = self._backend.capabilities.debugger
        if debugger is not None:
            selected_debugger = debugger
        return self._backend.start(
            SessionConfig.from_inputs(
                program=program,
                debugger=selected_debugger,
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

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate session shutdown to the debugger backend."""

        return self._backend.stop()

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        """Delegate status inspection to the debugger backend."""

        return self._backend.get_status()

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate command execution to the debugger backend."""

        return self._backend.execute_command(command=command, timeout_sec=timeout_sec)

    def run(
        self,
        args: list[str] | None = None,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate target start to the debugger backend."""

        return self._backend.run(args=args, options=options)

    def add_inferior(
        self, *, executable: str | None = None, make_current: bool = False
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        """Delegate inferior creation to the debugger backend."""

        return self._backend.add_inferior(executable=executable, make_current=make_current)

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        """Delegate inferior removal to the debugger backend."""

        return self._backend.remove_inferior(inferior_id=inferior_id)

    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate process attach to the debugger backend."""

        return self._backend.attach_process(pid=pid, timeout_sec=timeout_sec)

    def set_follow_fork_mode(
        self, mode: FollowForkMode
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError:
        """Delegate follow-fork-mode changes to the execution service."""

        return self._backend.set_follow_fork_mode(mode=mode)

    def set_detach_on_fork(
        self, enabled: bool
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError:
        """Delegate detach-on-fork changes to the execution service."""

        return self._backend.set_detach_on_fork(enabled=enabled)

    def continue_execution(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate continue to the debugger backend."""

        return self._backend.continue_execution(reverse=reverse, options=options)

    def wait_for_stop(
        self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC, stop_reasons: tuple[str, ...] = ()
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        """Delegate stop waiting to the debugger backend."""

        return self._backend.wait_for_stop(timeout_sec=timeout_sec, stop_reasons=stop_reasons)

    def step(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate single-step to the execution service."""

        return self._backend.step(reverse=reverse, options=options)

    def next(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate next/step-over to the execution service."""

        return self._backend.next(reverse=reverse, options=options)

    def step_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate instruction-step to the execution service."""

        return self._backend.step_instruction(reverse=reverse, options=options)

    def next_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate instruction-step-over to the execution service."""

        return self._backend.next_instruction(reverse=reverse, options=options)

    def return_from_frame(
        self, *, value: str | None = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Delegate force-return to the execution service."""

        return self._backend.return_from_frame(value=value)

    def jump(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Relocate the executing frame and resume through the debugger backend."""

        return self._backend.jump(location, options=options)

    def until(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Execute toward a destination using the debugger's frame-scoped until."""

        return self._backend.until(location, options=options)

    def finish(
        self,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        """Delegate finish/step-out to the execution service."""

        return self._backend.finish(reverse=reverse, options=options)

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        """Delegate interrupt to the debugger backend."""

        return self._backend.interrupt()

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        """Delegate function call execution to the debugger backend."""

        return self._backend.call_function(function_call=function_call, timeout_sec=timeout_sec)

    def get_threads(
        self, query: ThreadQuery = DEFAULT_THREAD_QUERY
    ) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Delegate thread inspection to the debugger backend."""

        return self._backend.get_threads(query)

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """Delegate inferior inventory inspection to the debugger backend."""

        return self._backend.list_inferiors()

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        """Delegate inferior selection to the debugger backend."""

        return self._backend.select_inferior(inferior_id=inferior_id)

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Delegate thread selection to the debugger backend."""

        return self._backend.select_thread(thread_id=thread_id)

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100, *, include_defers: bool = False
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Delegate backtrace retrieval to the debugger backend."""

        return self._backend.get_backtrace(
            thread_id=thread_id, max_frames=max_frames, include_defers=include_defers
        )

    def get_frame_info(
        self, *, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[FrameInfo] | OperationError:
        """Delegate current-frame inspection to the inspection service."""

        return self._backend.get_frame_info(thread_id=thread_id, frame=frame)

    def disassemble(
        self, request: DisassemblyRequest
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        """Delegate structured disassembly to the debugger backend."""

        return self._backend.disassemble(request)

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Delegate frame selection to the debugger backend."""

        return self._backend.select_frame(frame_number=frame_number)

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
        *,
        deferred_call: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Delegate expression evaluation to the debugger backend."""

        return self._backend.evaluate_expression(
            expression=expression, thread_id=thread_id, frame=frame, deferred_call=deferred_call
        )

    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Delegate memory reads to the debugger backend."""

        return self._backend.read_memory(address=address, count=count, offset=offset)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0, *, deferred_call: int | None = None
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Delegate local-variable inspection to the inspection service."""

        return self._backend.get_variables(
            thread_id=thread_id, frame=frame, deferred_call=deferred_call
        )

    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        """Delegate structured source-context retrieval to the inspection service."""

        return self._backend.get_source_context(request)

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
        """Delegate register inspection to the debugger backend."""

        return self._backend.get_registers(
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
        """Delegate breakpoint creation to the debugger backend."""

        return self._backend.set_breakpoint(
            location=location, condition=condition, temporary=temporary
        )

    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate watchpoint creation to the debugger backend."""

        return self._backend.set_watchpoint(expression=expression, access=access)

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate watchpoint deletion to the debugger backend."""

        return self._backend.delete_watchpoint(number=number)

    def set_catchpoint(
        self, kind: CatchpointType, *, argument: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate catchpoint creation to the debugger backend."""

        return self._backend.set_catchpoint(kind=kind, argument=argument, temporary=temporary)

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        """Delegate breakpoint listing to the debugger backend."""

        return self._backend.list_breakpoints()

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate breakpoint lookup to the debugger backend."""

        return self._backend.get_breakpoint(number=number)

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint deletion to the debugger backend."""

        return self._backend.delete_breakpoint(number=number)

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint enabling to the debugger backend."""

        return self._backend.enable_breakpoint(number=number)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delegate breakpoint disabling to the debugger backend."""

        return self._backend.disable_breakpoint(number=number)

    def update_breakpoint(
        self, number: int, *, condition: str | None = None, clear_condition: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Delegate breakpoint updates to the debugger backend."""

        return self._backend.update_breakpoint(
            number=number, condition=condition, clear_condition=clear_condition
        )

    def execute_batch(
        self,
        steps: Sequence[BatchStepInvocation],
        *,
        fail_fast: bool = True,
        capture_stop_events: bool = True,
    ) -> OperationSuccess[BatchExecutionInfo]:
        """Execute a structured batch of existing session operations."""

        with self.runtime.workflow_lock:
            return self._workflow.execute_batch(
                steps,
                fail_fast=fail_fast,
                capture_stop_events=capture_stop_events,
            )

    def execute_batch_templates(
        self,
        steps: Sequence[BatchStepTemplate],
        *,
        fail_fast: bool = True,
        capture_stop_events: bool = True,
    ) -> OperationSuccess[BatchExecutionInfo]:
        """Execute reusable validated batch templates against this session."""

        with self.runtime.workflow_lock:
            return self._workflow.execute_batch_templates(
                self,
                steps,
                fail_fast=fail_fast,
                capture_stop_events=capture_stop_events,
            )

    def capture_bundle(
        self,
        *,
        output_dir: str | None = None,
        bundle_name: str | None = None,
        expressions: list[str] | None = None,
        memory_ranges: list[MemoryCaptureRange] | None = None,
        max_frames: int = 100,
        include_threads: bool = True,
        include_backtraces: bool = True,
        include_frame: bool = True,
        include_variables: bool = True,
        include_registers: bool = True,
        include_transcript: bool = True,
        include_stop_history: bool = True,
    ) -> OperationSuccess[CaptureBundleInfo] | OperationError:
        """Capture a file-oriented forensic bundle for the current session."""

        with self.runtime.workflow_lock:
            return self._capture.capture_bundle(
                output_dir=output_dir,
                bundle_name=bundle_name,
                expressions=expressions,
                memory_ranges=memory_ranges,
                max_frames=max_frames,
                include_threads=include_threads,
                include_backtraces=include_backtraces,
                include_frame=include_frame,
                include_variables=include_variables,
                include_registers=include_registers,
                include_transcript=include_transcript,
                include_stop_history=include_stop_history,
            )
