"""Typed domain models shared across service layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from typing_extensions import TypeAliasType

from ..contracts import DisassemblyMode, ExecutionState, LocationKind
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
ToolResultStatus: TypeAlias = Literal["success", "error"]
FollowForkMode: TypeAlias = Literal["parent", "child"]
WatchpointAccessType: TypeAlias = Literal["write", "read", "access"]
CommandStatus: TypeAlias = Literal["success", "error", "timeout"]
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
class StructuredOutput:
    """Nominal marker for dataclasses that form object-shaped tool results."""


@dataclass(slots=True, frozen=True)
class SessionStatusSnapshot(StructuredOutput):
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
class InferiorCurrentInfo(StructuredOutput):
    """The currently selected inferior."""

    inferior: InferiorRecord


@dataclass(slots=True, frozen=True)
class SessionSummary(StructuredOutput):
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
class SessionListInfo(StructuredOutput):
    """Structured list of currently registered sessions."""

    sessions: list[SessionSummary]


@dataclass(slots=True, frozen=True)
class SessionStartInfo(StructuredOutput):
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
class StopEvent(StructuredOutput):
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
class CommandTranscriptEntry(StructuredOutput):
    """Structured metadata for one debugger command execution."""

    command: str
    sent_command: str | None = None
    status: CommandStatus = "success"
    result_class: str | None = None
    timed_out: bool = False
    fatal: bool = False
    error: str | None = None
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    timestamp: float | None = None


@dataclass(slots=True, frozen=True)
class BatchStepResult(StructuredOutput):
    """Structured result for one step executed by a batch workflow."""

    index: int
    tool: str
    result: StructuredPayload
    label: str | None = None
    stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class BatchExecutionInfo(StructuredOutput):
    """Structured result for one workflow batch executed in a session."""

    steps: list[BatchStepResult]
    requested_steps: int
    final_execution_state: ExecutionState | None = None
    final_stop_reason: str | None = None
    final_stop_kind: StopKind | None = None
    last_stop_event: StopEvent | None = None

    @property
    def has_errors(self) -> bool:
        """Return whether any completed batch step returned an error envelope."""

        return any(step.result["status"] == "error" for step in self.steps)


@dataclass(slots=True, frozen=True)
class CaptureArtifactInfo(StructuredOutput):
    """One file artifact produced by a capture bundle."""

    name: str
    path: str
    status: ToolResultStatus
    kind: str = "json"


@dataclass(slots=True, frozen=True)
class CaptureBundleInfo(StructuredOutput):
    """Structured result for one on-disk forensic capture bundle."""

    message: str
    bundle_dir: str
    bundle_name: str
    manifest_path: str
    artifacts: list[CaptureArtifactInfo]
    failed_sections: list[str] | None = None
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    last_stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class RunUntilFailureIterationInfo(StructuredOutput):
    """Structured summary for one iteration of a campaign run."""

    iteration: int
    status: ToolResultStatus
    execution_state: ExecutionState | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    matched_failure: bool = False
    trigger: str | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class RunUntilFailureInfo(StructuredOutput):
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
class InferiorListInfo(StructuredOutput):
    """Structured inferior inventory for one debugger session."""

    inferiors: list[InferiorRecord]
    current_inferior_id: int | None = None


@dataclass(slots=True, frozen=True)
class InferiorSelectionInfo(StructuredOutput):
    """Structured response for inferior selection."""

    inferior_id: int
    is_current: bool = True
    display: str | None = None
    description: str | None = None
    connection: str | None = None
    executable: str | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class InferiorAddInfo(StructuredOutput):
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
class InferiorRemoveInfo(StructuredOutput):
    """Structured response for inferior removal."""

    inferior_id: int
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class FollowForkModeInfo(StructuredOutput):
    """Structured response for follow-fork-mode changes."""

    mode: FollowForkMode
    message: str


@dataclass(slots=True, frozen=True)
class DetachOnForkInfo(StructuredOutput):
    """Structured response for detach-on-fork changes."""

    enabled: bool
    message: str


@dataclass(slots=True, frozen=True)
class MemoryReadInfo(StructuredOutput):
    """Structured response for one memory-read request."""

    address: str
    requested_bytes: int
    offset: int = 0
    blocks: list[MemoryBlockRecord] = field(default_factory=list)
    captured_bytes: int = 0


@dataclass(slots=True, frozen=True)
class WaitForStopInfo(StructuredOutput):
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
class FinishInfo(StructuredOutput):
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
class MemoryCaptureRange(StructuredOutput):
    """One explicit memory range requested for bundle capture."""

    address: str
    count: int
    offset: int = 0
    name: str | None = None


@dataclass(slots=True, frozen=True)
class SessionMessage(StructuredOutput):
    """Simple message payload."""

    message: str


@dataclass(slots=True, frozen=True)
class MessageResult(StructuredOutput):
    """Message plus structured result payload."""

    message: str
    debugger_result: StructuredPayload


@dataclass(slots=True, frozen=True)
class CommandExecutionInfo(StructuredOutput):
    """Structured result for command execution."""

    command: str
    output: str | None = None
    debugger_result: StructuredPayload | None = None


@dataclass(slots=True, frozen=True)
class ThreadListInfo(StructuredOutput):
    """Structured thread-list response."""

    threads: list[ThreadRecord]
    current_thread_id: str | None


@dataclass(slots=True, frozen=True)
class ThreadSelectionInfo(StructuredOutput):
    """Structured thread-selection response."""

    thread_id: int
    new_thread_id: str | None = None
    frame: FrameRecord | None = None


@dataclass(slots=True, frozen=True)
class BacktraceInfo(StructuredOutput):
    """Structured backtrace response."""

    thread_id: int | None
    frames: list[FrameRecord]


@dataclass(slots=True, frozen=True)
class FrameInfo(StructuredOutput):
    """Current frame details."""

    frame: FrameRecord | None


@dataclass(slots=True, frozen=True)
class DisassemblyInfo(StructuredOutput):
    """Structured disassembly response."""

    scope: LocationKind
    thread_id: int | None
    frame: int | None
    function: str | None
    file: str | None
    fullname: str | None
    line: int | None
    start_address: str | None
    end_address: str | None
    mode: DisassemblyMode
    instructions: list[DisassemblyInstructionRecord]


@dataclass(slots=True, frozen=True)
class SourceContextInfo(StructuredOutput):
    """Structured source-context response."""

    scope: LocationKind
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


@dataclass(slots=True, frozen=True)
class FrameSelectionInfo(StructuredOutput):
    """Structured frame-selection response."""

    frame_number: int
    frame: FrameRecord | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class BreakpointInfo(StructuredOutput):
    """Single breakpoint payload."""

    breakpoint: BreakpointRecord


@dataclass(slots=True, frozen=True)
class BreakpointListInfo(StructuredOutput):
    """Structured breakpoint-list response."""

    breakpoints: list[BreakpointRecord]


@dataclass(slots=True, frozen=True)
class ExpressionValueInfo(StructuredOutput):
    """Expression evaluation payload."""

    expression: str
    value: object
    type: str | None = None
    summary: str | None = None


@dataclass(slots=True, frozen=True)
class VariablesInfo(StructuredOutput):
    """Local variable inspection payload."""

    thread_id: int | None
    frame: int
    variables: list[VariableRecord]


@dataclass(slots=True, frozen=True)
class RegistersInfo(StructuredOutput):
    """Register inspection payload."""

    registers: list[RegisterRecord]


@dataclass(slots=True, frozen=True)
class FunctionCallInfo(StructuredOutput):
    """Function-call payload."""

    function_call: str
    result: str
    type: str | None = None
    summary: str | None = None
