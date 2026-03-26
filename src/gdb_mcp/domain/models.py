"""Typed domain models shared across service layers."""

from dataclasses import dataclass
from typing import Any


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
    startup_output: str | None = None
    warnings: list[str] | None = None
    env_output: list[dict[str, Any]] | None = None
    init_output: list[dict[str, Any]] | None = None


@dataclass(slots=True, frozen=True)
class SessionMessage:
    """Simple message payload."""

    message: str


@dataclass(slots=True, frozen=True)
class MessageResult:
    """Message plus structured result payload."""

    message: str
    result: Any
    status: str = "success"


@dataclass(slots=True, frozen=True)
class CommandExecutionInfo:
    """Structured result for command execution."""

    command: str
    output: str | None = None
    result: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class ThreadListInfo:
    """Structured thread-list response."""

    threads: list[Any]
    current_thread_id: Any
    count: int


@dataclass(slots=True, frozen=True)
class ThreadSelectionInfo:
    """Structured thread-selection response."""

    thread_id: int
    new_thread_id: Any = None
    frame: Any = None


@dataclass(slots=True, frozen=True)
class BacktraceInfo:
    """Structured backtrace response."""

    thread_id: int | None
    frames: list[Any]
    count: int


@dataclass(slots=True, frozen=True)
class FrameInfo:
    """Current frame details."""

    frame: Any


@dataclass(slots=True, frozen=True)
class FrameSelectionInfo:
    """Structured frame-selection response."""

    frame_number: int
    frame: Any | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class BreakpointInfo:
    """Single breakpoint payload."""

    breakpoint: Any


@dataclass(slots=True, frozen=True)
class BreakpointListInfo:
    """Structured breakpoint-list response."""

    breakpoints: list[Any]
    count: int


@dataclass(slots=True, frozen=True)
class ExpressionValueInfo:
    """Expression evaluation payload."""

    expression: str
    value: Any


@dataclass(slots=True, frozen=True)
class VariablesInfo:
    """Local variable inspection payload."""

    thread_id: int | None
    frame: int
    variables: list[Any]


@dataclass(slots=True, frozen=True)
class RegistersInfo:
    """Register inspection payload."""

    registers: list[Any]


@dataclass(slots=True, frozen=True)
class FunctionCallInfo:
    """Function-call payload."""

    function_call: str
    result: str
