"""The debugger backend seam.

One session drives one debugger. The service layer owns the tool-facing envelope,
locking, and the workflow/capture features; a backend owns spawning the debugger,
speaking its protocol, interpreting its events, and turning its output into the
domain payload types.

The seam is at the operation level on purpose: another debugger implements
these operations directly instead of imitating GDB's command set.
:data:`BackendOperation` names the same operations, so a backend publishes which
ones it can perform and the service can reject the rest before doing any work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, get_args

from ..contracts import ActionJumpName, ActionUntilName
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
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .runtime import SessionRuntime

BackendOperation: TypeAlias = Literal[
    "start",
    "stop",
    "status",
    "command",
    "run",
    "continue",
    "step",
    "next",
    "step_instruction",
    "next_instruction",
    "return_from_frame",
    "finish",
    ActionJumpName,
    ActionUntilName,
    "interrupt",
    "wait_for_stop",
    "attach",
    "call_function",
    "add_inferior",
    "remove_inferior",
    "select_inferior",
    "list_inferiors",
    "set_follow_fork_mode",
    "set_detach_on_fork",
    "threads",
    "backtrace",
    "frame_info",
    "select_thread",
    "select_frame",
    "evaluate",
    "variables",
    "registers",
    "read_memory",
    "disassemble",
    "source",
    "set_breakpoint",
    "set_watchpoint",
    "delete_watchpoint",
    "set_catchpoint",
    "list_breakpoints",
    "get_breakpoint",
    "delete_breakpoint",
    "enable_breakpoint",
    "disable_breakpoint",
    "update_breakpoint",
]

BACKEND_OPERATIONS: tuple[BackendOperation, ...] = get_args(BackendOperation)


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """What one backend can do, at the granularity of its operations."""

    debugger: str
    operations: frozenset[BackendOperation]
    concurrent_control: bool = False

    def supports(self, operation: BackendOperation) -> bool:
        """Return whether this backend implements one operation."""

        return operation in self.operations

    @classmethod
    def full_surface(cls, debugger: str) -> BackendCapabilities:
        """Capabilities of a backend that implements every operation."""

        return cls(debugger=debugger, operations=frozenset(BACKEND_OPERATIONS))


class DebuggerBackend(Protocol):
    """One debugger driven through the session's operation vocabulary."""

    @property
    def runtime(self) -> SessionRuntime:
        """Shared session state the backend keeps up to date."""
        ...

    @property
    def capabilities(self) -> BackendCapabilities:
        """The operations this backend can perform."""
        ...

    @property
    def has_controller(self) -> bool:
        """Return whether this backend owns a debugger handle."""
        ...

    def start(
        self,
        config: SessionConfig,
    ) -> OperationSuccess[SessionStartupInfo] | OperationError: ...

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError: ...

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]: ...

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def run(
        self,
        args: list[str] | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        wait_for_stop: bool = True,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def add_inferior(
        self,
        *,
        executable: str | None = None,
        make_current: bool = False,
    ) -> OperationSuccess[InferiorAddInfo] | OperationError: ...

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError: ...

    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def set_follow_fork_mode(
        self, mode: FollowForkMode
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError: ...

    def set_detach_on_fork(
        self, enabled: bool
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError: ...

    def continue_execution(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def wait_for_stop(
        self,
        *,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        stop_reasons: tuple[str, ...] = (),
    ) -> OperationSuccess[WaitForStopInfo] | OperationError: ...

    def step(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def next(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def step_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def next_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def return_from_frame(
        self, *, value: str | None = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError: ...

    def jump(
        self,
        location: str,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError: ...

    def until(
        self,
        location: str,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError: ...

    def finish(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError: ...

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError: ...

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError: ...

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError: ...

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError: ...

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError: ...

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError: ...

    def get_frame_info(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[FrameInfo] | OperationError: ...

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError: ...

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError: ...

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError: ...

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError: ...

    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo] | OperationError: ...

    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo] | OperationError: ...

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
    ) -> OperationSuccess[DisassemblyInfo] | OperationError: ...

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
    ) -> OperationSuccess[SourceContextInfo] | OperationError: ...

    def set_breakpoint(
        self,
        location: str,
        condition: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError: ...

    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo] | OperationError: ...

    def delete_watchpoint(
        self, number: int
    ) -> OperationSuccess[SessionMessage] | OperationError: ...

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError: ...

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError: ...

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError: ...

    def delete_breakpoint(
        self, number: int
    ) -> OperationSuccess[SessionMessage] | OperationError: ...

    def enable_breakpoint(
        self, number: int
    ) -> OperationSuccess[SessionMessage] | OperationError: ...

    def disable_breakpoint(
        self, number: int
    ) -> OperationSuccess[SessionMessage] | OperationError: ...

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError: ...
