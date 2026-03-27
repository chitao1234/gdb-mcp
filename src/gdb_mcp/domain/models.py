"""Typed domain models shared across service layers."""

from dataclasses import dataclass
from typing import TypedDict, TypeAlias

StructuredPayload: TypeAlias = dict[str, object]


class FrameRecord(TypedDict, total=False):
    """Structured stack-frame payload returned by GDB."""

    level: str
    addr: str
    func: str
    file: str
    fullname: str
    line: str
    arch: str


class ThreadRecord(TypedDict, total=False):
    """Structured thread payload returned by GDB."""

    id: str
    target_id: str
    name: str
    state: str
    core: str
    frame: FrameRecord


class BreakpointRecord(TypedDict, total=False):
    """Structured breakpoint payload returned by GDB."""

    number: str
    type: str
    disp: str
    enabled: str
    addr: str
    func: str
    file: str
    fullname: str
    line: str
    times: str
    original_location: str


class VariableRecord(TypedDict, total=False):
    """Structured local-variable payload returned by GDB."""

    name: str
    value: str
    type: str
    arg: str


class RegisterRecord(TypedDict, total=False):
    """Structured register payload returned by GDB."""

    number: str
    value: str


@dataclass(slots=True, frozen=True)
class SessionStatusSnapshot:
    """Serializable snapshot of the externally visible session status."""

    is_running: bool
    target_loaded: bool
    has_controller: bool


@dataclass(slots=True, frozen=True)
class SessionStartInfo:
    """Structured result for a successful session start."""

    message: str
    program: str | None = None
    core: str | None = None
    target_loaded: bool = False
    startup_output: str | None = None
    warnings: list[str] | None = None
    env_output: list[StructuredPayload] | None = None
    init_output: list[StructuredPayload] | None = None


@dataclass(slots=True, frozen=True)
class SessionMessage:
    """Simple message payload."""

    message: str


@dataclass(slots=True, frozen=True)
class MessageResult:
    """Message plus structured result payload."""

    message: str
    result: StructuredPayload
    status: str = "success"


@dataclass(slots=True, frozen=True)
class CommandExecutionInfo:
    """Structured result for command execution."""

    command: str
    output: str | None = None
    result: StructuredPayload | None = None


@dataclass(slots=True, frozen=True)
class ThreadListInfo:
    """Structured thread-list response."""

    threads: list[ThreadRecord]
    current_thread_id: str | None
    count: int


@dataclass(slots=True, frozen=True)
class ThreadSelectionInfo:
    """Structured thread-selection response."""

    thread_id: int
    new_thread_id: str | None = None
    frame: FrameRecord | None = None


@dataclass(slots=True, frozen=True)
class BacktraceInfo:
    """Structured backtrace response."""

    thread_id: int | None
    frames: list[FrameRecord]
    count: int


@dataclass(slots=True, frozen=True)
class FrameInfo:
    """Current frame details."""

    frame: FrameRecord


@dataclass(slots=True, frozen=True)
class FrameSelectionInfo:
    """Structured frame-selection response."""

    frame_number: int
    frame: FrameRecord | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class BreakpointInfo:
    """Single breakpoint payload."""

    breakpoint: BreakpointRecord


@dataclass(slots=True, frozen=True)
class BreakpointListInfo:
    """Structured breakpoint-list response."""

    breakpoints: list[BreakpointRecord]
    count: int


@dataclass(slots=True, frozen=True)
class ExpressionValueInfo:
    """Expression evaluation payload."""

    expression: str
    value: object


@dataclass(slots=True, frozen=True)
class VariablesInfo:
    """Local variable inspection payload."""

    thread_id: int | None
    frame: int
    variables: list[VariableRecord]


@dataclass(slots=True, frozen=True)
class RegistersInfo:
    """Register inspection payload."""

    registers: list[RegisterRecord]


@dataclass(slots=True, frozen=True)
class FunctionCallInfo:
    """Function-call payload."""

    function_call: str
    result: str
