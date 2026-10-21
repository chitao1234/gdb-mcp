"""Typed domain models shared across service layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from typing_extensions import TypeAliasType

from .records import (
    BreakpointRecord,
    DisassemblyInstructionRecord,
    FrameRecord,
    InferiorRecord,
    InferiorStateRecord,
    MemoryBlockRecord,
    RegisterRecord,
    SourceLineRecord,
    ThreadRecord,
    VariableRecord,
)
from .stop_kinds import StopKind

JsonScalar: TypeAlias = str | int | float | bool | None
# A debugger payload is arbitrary JSON, and JSON nests by nature. It is spelled
# with an explicit alias because that is the only form pydantic can build a JSON
# Schema from on this project's Python floor: the implicit `dict[str, "JsonValue"]`
# spelling makes schema generation recurse until it fails.
JsonValue = TypeAliasType("JsonValue", "JsonScalar | dict[str, JsonValue] | list[JsonValue]")
JsonObject = TypeAliasType("JsonObject", "dict[str, JsonValue]")
JsonArray = TypeAliasType("JsonArray", "list[JsonValue]")
StructuredPayload: TypeAlias = JsonObject
FollowForkMode: TypeAlias = Literal["parent", "child"]
WatchpointAccessType: TypeAlias = Literal["write", "read", "access"]
ExecutionState: TypeAlias = Literal["unknown", "not_started", "running", "paused", "exited"]
CatchpointType: TypeAlias = Literal[
    "throw",
    "rethrow",
    "catch",
    "exec",
    "fork",
    "vfork",
    "load",
    "unload",
    "signal",
    "syscall",
]


@dataclass(slots=True, frozen=True)
class SessionStatusSnapshot:
    """Serializable snapshot of the externally visible session status."""

    is_running: bool
    target_loaded: bool
    has_controller: bool
    execution_state: ExecutionState = "unknown"
    state_detail: Literal["crashed"] | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    inferior_states: list[InferiorStateRecord] | None = None
    follow_fork_mode: FollowForkMode | None = None
    detach_on_fork: bool | None = None


@dataclass(slots=True, frozen=True)
class InferiorCurrentInfo:
    """The currently selected inferior."""

    inferior: InferiorRecord


@dataclass(slots=True, frozen=True)
class SessionSummary:
    """Structured summary of one active debugger session."""

    session_id: int
    lifecycle_state: str
    execution_state: ExecutionState
    target_loaded: bool
    has_controller: bool
    program: str | None = None
    core: str | None = None
    working_dir: str | None = None
    attached_pid: int | None = None
    current_thread_id: int | None = None
    current_frame: int | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    inferior_states: list[InferiorStateRecord] | None = None
    follow_fork_mode: FollowForkMode | None = None
    detach_on_fork: bool | None = None
    last_failure_message: str | None = None
    endpoint: str | None = None
    rr_replay: str | None = None


@dataclass(slots=True, frozen=True)
class SessionListInfo:
    """Structured list of currently registered sessions."""

    sessions: list[SessionSummary]
    count: int


@dataclass(slots=True, frozen=True)
class SessionStartInfo:
    """Structured result for a successful session start."""

    session_id: int | None = None
    message: str = ""
    program: str | None = None
    core: str | None = None
    target_loaded: bool = False
    execution_state: ExecutionState = "unknown"
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    startup_output: str | None = None
    env_output: list[StructuredPayload] | None = None
    init_output: list[StructuredPayload] | None = None
    endpoint: str | None = None
    rr_replay: str | None = None


@dataclass(slots=True, frozen=True)
class StopEvent:
    """Structured description of one inferior stop or exit event."""

    execution_state: ExecutionState
    state_detail: Literal["crashed"] | None = None
    reason: str | None = None
    stop_kind: StopKind | None = None
    command: str | None = None
    inferior_id: int | None = None
    thread_id: int | None = None
    frame: FrameRecord | None = None
    signal_name: str | None = None
    signal_meaning: str | None = None
    signal_code: int | None = None
    fault_address: str | None = None
    crash: StructuredPayload | None = None
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
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    timestamp: float | None = None


@dataclass(slots=True, frozen=True)
class BatchStepResult:
    """Structured result for one step executed by a batch workflow."""

    index: int
    tool: str
    status: str
    result: StructuredPayload
    label: str | None = None
    action: str | None = None
    code: str | None = None
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
    final_execution_state: ExecutionState | None = None
    final_stop_reason: str | None = None
    final_stop_kind: StopKind | None = None
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
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    last_stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class RunUntilFailureIterationInfo:
    """Structured summary for one iteration of a campaign run."""

    iteration: int
    status: str
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    matched_failure: bool = False
    trigger: str | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class RunUntilFailureInfo:
    """Structured result for one repeat-until-failure campaign."""

    message: str
    matched_failure: bool
    iterations_requested: int
    iterations_completed: int
    failure_iteration: int | None = None
    trigger: str | None = None
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    capture_bundle: CaptureBundleInfo | None = None
    capture_error: str | None = None
    last_result: StructuredPayload | None = None
    iterations: list[RunUntilFailureIterationInfo] | None = None


@dataclass(slots=True, frozen=True)
class InferiorListInfo:
    """Structured inferior inventory for one debugger session."""

    inferiors: list[InferiorRecord]
    count: int
    current_inferior_id: int | None = None


@dataclass(slots=True, frozen=True)
class InferiorSelectionInfo:
    """Structured response for inferior selection."""

    inferior_id: int
    is_current: bool = True
    display: str | None = None
    description: str | None = None
    connection: str | None = None
    executable: str | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class InferiorAddInfo:
    """Structured response for inferior creation."""

    inferior_id: int
    is_current: bool = False
    display: str | None = None
    description: str | None = None
    connection: str | None = None
    executable: str | None = None
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class InferiorRemoveInfo:
    """Structured response for inferior removal."""

    inferior_id: int
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class FollowForkModeInfo:
    """Structured response for follow-fork-mode changes."""

    mode: FollowForkMode
    message: str


@dataclass(slots=True, frozen=True)
class DetachOnForkInfo:
    """Structured response for detach-on-fork changes."""

    enabled: bool
    message: str


@dataclass(slots=True, frozen=True)
class MemoryReadInfo:
    """Structured response for one memory-read request."""

    address: str
    count: int
    offset: int = 0
    blocks: list[MemoryBlockRecord] = field(default_factory=list)
    block_count: int = 0
    captured_bytes: int = 0


@dataclass(slots=True, frozen=True)
class WaitForStopInfo:
    """Structured response for waiting on the next or current stop event."""

    message: str
    matched: bool
    timed_out: bool = False
    source: str = "waited"
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    reason_filter: list[str] | None = None
    last_stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class FinishInfo:
    """Structured response for finishing the current frame."""

    message: str
    return_value: str | None = None
    result_var: str | None = None
    frame: FrameRecord | None = None
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    last_stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class MemoryCaptureRange:
    """One explicit memory range requested for bundle capture."""

    address: str
    count: int
    offset: int = 0
    name: str | None = None


@dataclass(slots=True, frozen=True)
class SessionMessage:
    """Simple message payload."""

    message: str


@dataclass(slots=True, frozen=True)
class MessageResult:
    """Message plus structured result payload."""

    message: str
    debugger_result: StructuredPayload


@dataclass(slots=True, frozen=True)
class CommandExecutionInfo:
    """Structured result for command execution."""

    command: str
    output: str | None = None
    debugger_result: StructuredPayload | None = None


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

    frame: FrameRecord | None


@dataclass(slots=True, frozen=True)
class DisassemblyInfo:
    """Structured disassembly response."""

    scope: str
    thread_id: int | None
    frame: int | None
    function: str | None
    file: str | None
    fullname: str | None
    line: int | None
    start_address: str | None
    end_address: str | None
    mode: str
    instructions: list[DisassemblyInstructionRecord]
    count: int


@dataclass(slots=True, frozen=True)
class SourceContextInfo:
    """Structured source-context response."""

    scope: str
    thread_id: int | None
    frame: int | None
    function: str | None
    address: str | None
    file: str
    fullname: str | None
    line: int | None
    start_line: int
    end_line: int
    lines: list[SourceLineRecord]
    count: int


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
    type: str | None = None
    summary: str | None = None


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
    type: str | None = None
    summary: str | None = None
