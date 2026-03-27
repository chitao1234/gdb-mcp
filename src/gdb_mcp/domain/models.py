"""Typed domain models shared across service layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonObject: TypeAlias = dict[str, "JsonValue"]
JsonArray: TypeAlias = list["JsonValue"]
JsonValue: TypeAlias = JsonScalar | JsonObject | JsonArray
StructuredPayload: TypeAlias = JsonObject


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
    execution_state: str = "unknown"
    stop_reason: str | None = None
    exit_code: int | None = None


@dataclass(slots=True, frozen=True)
class SessionSummary:
    """Structured summary of one active debugger session."""

    session_id: int
    lifecycle_state: str
    execution_state: str
    target_loaded: bool
    has_controller: bool
    program: str | None = None
    core: str | None = None
    working_dir: str | None = None
    attached_pid: int | None = None
    current_thread_id: int | None = None
    current_frame: int | None = None
    stop_reason: str | None = None
    exit_code: int | None = None
    last_failure_message: str | None = None


@dataclass(slots=True, frozen=True)
class SessionListInfo:
    """Structured list of currently registered sessions."""

    sessions: list[SessionSummary]
    count: int


@dataclass(slots=True, frozen=True)
class SessionStartInfo:
    """Structured result for a successful session start."""

    message: str
    program: str | None = None
    core: str | None = None
    target_loaded: bool = False
    execution_state: str = "unknown"
    stop_reason: str | None = None
    exit_code: int | None = None
    startup_output: str | None = None
    warnings: list[str] | None = None
    env_output: list[StructuredPayload] | None = None
    init_output: list[StructuredPayload] | None = None


@dataclass(slots=True, frozen=True)
class StopEvent:
    """Structured description of one inferior stop or exit event."""

    execution_state: str
    reason: str | None = None
    command: str | None = None
    thread_id: int | None = None
    frame: FrameRecord | None = None
    signal_name: str | None = None
    signal_meaning: str | None = None
    breakpoint_number: str | None = None
    exit_code: int | None = None
    timestamp: float | None = None
    details: StructuredPayload = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CommandTranscriptEntry:
    """Structured metadata for one debugger command execution."""

    command: str
    sent_command: str | None = None
    status: str = "success"
    result_class: str | None = None
    timed_out: bool = False
    fatal: bool = False
    error: str | None = None
    execution_state: str | None = None
    stop_reason: str | None = None
    timestamp: float | None = None


@dataclass(slots=True, frozen=True)
class BatchStepResult:
    """Structured result for one step executed by a batch workflow."""

    index: int
    tool: str
    status: str
    result: StructuredPayload
    label: str | None = None
    stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class BatchExecutionInfo:
    """Structured result for one workflow batch executed in a session."""

    steps: list[BatchStepResult]
    count: int
    completed_steps: int
    error_count: int
    stopped_early: bool = False
    failure_step_index: int | None = None
    final_execution_state: str | None = None
    final_stop_reason: str | None = None
    last_stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class CaptureArtifactInfo:
    """One file artifact produced by a capture bundle."""

    name: str
    path: str
    status: str
    kind: str = "json"


@dataclass(slots=True, frozen=True)
class CaptureBundleInfo:
    """Structured result for one on-disk forensic capture bundle."""

    message: str
    bundle_dir: str
    bundle_name: str
    manifest_path: str
    artifacts: list[CaptureArtifactInfo]
    artifact_count: int
    failed_sections: list[str] | None = None
    execution_state: str | None = None
    stop_reason: str | None = None
    last_stop_event: StopEvent | None = None


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
