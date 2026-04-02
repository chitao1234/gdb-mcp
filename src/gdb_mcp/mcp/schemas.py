"""Pydantic schemas and tool definitions for the MCP layer."""

from __future__ import annotations

from typing import Annotated, Literal, Optional, TypeAlias

from mcp.types import Tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationInfo,
    field_validator,
    model_validator,
)


class StrictArgsModel(BaseModel):
    """Base model for MCP request validation."""

    model_config = ConfigDict(extra="forbid")


def _coerce_int_like(
    value: object,
    *,
    field_name: str,
    minimum: int,
    allow_none: bool = False,
) -> int | None:
    """Normalize integer-like fields while accepting numeric strings."""

    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field_name} is required")

    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must be an integer")
        if text.startswith(("+", "-")):
            sign = text[0]
            digits = text[1:]
            if not digits.isdigit():
                raise ValueError(f"{field_name} must be an integer")
            parsed = int(f"{sign}{digits}", 10)
        elif text.isdigit():
            parsed = int(text, 10)
        else:
            raise ValueError(f"{field_name} must be an integer")
    else:
        raise ValueError(f"{field_name} must be an integer")

    if parsed < minimum:
        if minimum == 1:
            raise ValueError(f"{field_name} must be > 0")
        raise ValueError(f"{field_name} must be >= {minimum}")
    return parsed


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    """Normalize optional string selectors while rejecting blanks."""

    if value is None:
        return None

    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


BATCH_STEP_TOOL_NAMES = (
    "gdb_session_query",
    "gdb_inferior_query",
    "gdb_inferior_manage",
    "gdb_execution_manage",
    "gdb_breakpoint_query",
    "gdb_breakpoint_manage",
    "gdb_context_query",
    "gdb_context_manage",
    "gdb_inspect_query",
    "gdb_capture_bundle",
    "gdb_execute_command",
    "gdb_attach_process",
    "gdb_call_function",
)
BatchStepToolName: TypeAlias = Literal[
    "gdb_session_query",
    "gdb_inferior_query",
    "gdb_inferior_manage",
    "gdb_execution_manage",
    "gdb_breakpoint_query",
    "gdb_breakpoint_manage",
    "gdb_context_query",
    "gdb_context_manage",
    "gdb_inspect_query",
    "gdb_capture_bundle",
    "gdb_execute_command",
    "gdb_attach_process",
    "gdb_call_function",
]


class StartSessionArgs(StrictArgsModel):
    program: Optional[str] = Field(None, description="Path to executable to debug")
    args: Optional[list[str] | str] = Field(
        None,
        description=(
            "Command-line arguments for the program. "
            "Accepts either an explicit argv list or one shell-style string. "
            "Cannot be combined with core."
        ),
    )
    init_commands: Optional[list[str]] = Field(
        None,
        description=(
            "GDB commands to run on startup after environment variables have been applied "
            "(e.g., 'core-file /path/to/core', 'set sysroot /path')"
        ),
    )
    env: Optional[dict[str, str]] = Field(
        None,
        description=(
            "Environment variables to set for the debugged program before init_commands run "
            "(e.g., {'LD_LIBRARY_PATH': '/custom/libs'})"
        ),
    )
    gdb_path: Optional[str] = Field(
        None,
        description="Path to GDB executable (default: from GDB_PATH env var or 'gdb')",
    )
    working_dir: Optional[str] = Field(
        None,
        description=(
            "Working directory to use when starting GDB. "
            "Use this when debugging programs that need to be run from a specific directory, "
            "or when the program expects to find files (config, data, etc.) relative to its working directory. "
            "GDB will be started in this directory. "
            "Example: If debugging a server that loads config from './config.json', set working_dir to the server's directory."
        ),
    )
    core: Optional[str] = Field(
        None,
        description=(
            "Path to core dump file for post-mortem debugging. "
            "When specified, GDB is started with --core flag which properly initializes symbol resolution. "
            "Cannot be combined with args. "
            "IMPORTANT: When using a sysroot with core dumps, set sysroot AFTER the core is loaded "
            "(either via this parameter or core-file command) for symbols to resolve correctly."
        ),
    )


class ExecuteCommandArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    command: str = Field(..., description="GDB command to execute")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class RunArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    args: Optional[list[str] | str] = Field(
        None,
        description=(
            "Override inferior arguments for this run. "
            "Accepts either an explicit argv list or one shell-style string."
        ),
    )
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")
    wait_for_stop: bool = Field(
        True,
        description=(
            "When true, wait for the next stop/prompt state. "
            "When false, return as soon as GDB acknowledges that execution is running."
        ),
    )


class AddInferiorArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    executable: str | None = Field(
        None,
        description="Optional executable to associate with the new inferior after creation.",
    )
    make_current: bool = Field(
        False,
        description="When true, leave the new inferior selected after the call.",
    )

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str | None) -> str | None:
        """Reject blank executable overrides."""

        return _normalize_optional_text(value, field_name="executable")


class RemoveInferiorArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    inferior_id: int = Field(..., gt=0, description="Inferior ID to remove")


class FinishArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class AttachProcessArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    pid: int = Field(..., gt=0, description="PID of the process to attach to")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class GetBacktraceArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: Optional[int | str] = Field(
        None,
        description="Thread ID as an integer or numeric string (None for current thread)",
    )
    max_frames: int = Field(100, gt=0, description="Maximum number of frames to retrieve")

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive thread IDs."""

        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)


class SetBreakpointArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    location: str = Field(..., description="Breakpoint location (function, file:line, or *address)")
    condition: Optional[str] = Field(None, description="Conditional expression")
    temporary: bool = Field(False, description="Whether breakpoint is temporary")


class SetWatchpointArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    expression: str = Field(..., description="Expression to watch for memory access")
    access: Literal["write", "read", "access"] = Field(
        "write",
        description="Whether to stop on writes only, reads only, or any access",
    )


class SetCatchpointArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    kind: Literal[
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
    ] = Field(..., description="Debugger event kind to catch")
    argument: Optional[str] = Field(
        None,
        description=(
            "Optional event argument such as an exception regex, syscall name or group, "
            "signal name, or shared library regex."
        ),
    )
    temporary: bool = Field(False, description="Use a temporary catchpoint (tcatch)")


class EvaluateExpressionArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    expression: str = Field(..., description="C/C++ expression to evaluate")
    thread_id: Optional[int | str] = Field(
        None,
        description="Thread ID override as an integer or numeric string",
    )
    frame: Optional[int | str] = Field(
        None,
        description="Frame number override as an integer or numeric string",
    )

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive thread IDs."""

        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing non-negative frame indices."""

        return _coerce_int_like(value, field_name="frame", minimum=0, allow_none=True)


class GetVariablesArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: Optional[int | str] = Field(
        None,
        description="Thread ID as an integer or numeric string (None for current)",
    )
    frame: int | str = Field(
        0,
        description="Frame number as an integer or numeric string (0 is current)",
    )

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive thread IDs."""

        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str) -> int:
        """Accept numeric strings while enforcing non-negative frame indices."""

        normalized = _coerce_int_like(value, field_name="frame", minimum=0, allow_none=False)
        if normalized is None:
            raise ValueError("frame is required")
        return normalized


class DisassembleArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int | str | None = Field(None, description="Optional thread override")
    frame: int | str | None = Field(None, description="Optional frame override")
    function: str | None = Field(None, description="Function name to disassemble")
    address: str | None = Field(None, description="Single address selector")
    start_address: str | None = Field(None, description="Start of explicit address range")
    end_address: str | None = Field(None, description="End of explicit address range")
    file: str | None = Field(None, description="Source file selector")
    line: int | str | None = Field(None, description="Source line selector")
    instruction_count: int = Field(32, gt=0, description="Upper bound on returned instructions")
    mode: Literal["assembly", "mixed"] = Field(
        "mixed",
        description="Whether to request assembly only or mixed source/assembly output",
    )

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive thread IDs."""

        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing non-negative frame indices."""

        return _coerce_int_like(value, field_name="frame", minimum=0, allow_none=True)

    @field_validator("line")
    @classmethod
    def validate_line(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive source lines."""

        return _coerce_int_like(value, field_name="line", minimum=1, allow_none=True)

    @field_validator("function", "address", "start_address", "end_address", "file")
    @classmethod
    def validate_selector_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Reject blank direct-location selectors."""

        field_name = info.field_name or "selector"
        return _normalize_optional_text(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_selector_mode(self) -> "DisassembleArgs":
        """Require exactly zero or one disassembly selector group."""

        selector_modes: list[str] = []
        has_context_override = self.thread_id is not None or self.frame is not None

        if self.function is not None:
            selector_modes.append("function")
        if self.address is not None:
            selector_modes.append("address")
        if self.start_address is not None or self.end_address is not None:
            if self.start_address is None or self.end_address is None:
                raise ValueError("start_address and end_address must be provided together")
            selector_modes.append("address_range")
        if self.file is not None or self.line is not None:
            if self.file is None or self.line is None:
                raise ValueError("file and line must be provided together")
            selector_modes.append("file_line")

        if len(selector_modes) > 1:
            raise ValueError("selector groups are mutually exclusive")
        if has_context_override and selector_modes:
            raise ValueError("thread_id and frame cannot be combined with direct location selectors")
        return self


class GetSourceContextArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int | str | None = Field(None, description="Optional thread override")
    frame: int | str | None = Field(None, description="Optional frame override")
    function: str | None = Field(None, description="Function name selector")
    address: str | None = Field(None, description="Address selector")
    file: str | None = Field(None, description="Source file selector")
    line: int | str | None = Field(None, description="Source line selector")
    start_line: int | str | None = Field(None, description="Start of explicit line range")
    end_line: int | str | None = Field(None, description="End of explicit line range")
    context_before: int = Field(5, ge=0, description="Lines before focal line")
    context_after: int = Field(5, ge=0, description="Lines after focal line")

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive thread IDs."""

        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing non-negative frame indices."""

        return _coerce_int_like(value, field_name="frame", minimum=0, allow_none=True)

    @field_validator("line", "start_line", "end_line")
    @classmethod
    def validate_lines(cls, value: int | str | None, info: ValidationInfo) -> int | None:
        """Accept numeric strings while enforcing positive source lines."""

        field_name = info.field_name or "line"
        return _coerce_int_like(value, field_name=field_name, minimum=1, allow_none=True)

    @field_validator("function", "address", "file")
    @classmethod
    def validate_selector_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Reject blank direct-location selectors."""

        field_name = info.field_name or "selector"
        return _normalize_optional_text(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_selector_mode(self) -> "GetSourceContextArgs":
        """Require exactly zero or one source-context selector group."""

        selector_modes: list[str] = []
        has_context_override = self.thread_id is not None or self.frame is not None
        has_range = self.start_line is not None or self.end_line is not None
        start_line = self.start_line if isinstance(self.start_line, int) else None
        end_line = self.end_line if isinstance(self.end_line, int) else None

        if self.line is not None and has_range:
            raise ValueError("line cannot be combined with start_line or end_line")
        if has_range:
            if start_line is None or end_line is None:
                raise ValueError("start_line and end_line must be provided together")
            if start_line > end_line:
                raise ValueError("start_line must be <= end_line")

        if self.function is not None:
            selector_modes.append("function")
        if self.address is not None:
            selector_modes.append("address")
        if self.file is not None or self.line is not None or has_range:
            if self.file is None:
                raise ValueError("file is required when line, start_line, or end_line is provided")
            if self.line is not None:
                selector_modes.append("file_line")
            elif has_range:
                selector_modes.append("file_range")
            else:
                raise ValueError("file selector requires line or start_line and end_line")

        if len(selector_modes) > 1:
            raise ValueError("selector groups are mutually exclusive")
        if has_context_override and selector_modes:
            raise ValueError("thread_id and frame cannot be combined with direct location selectors")
        return self


class ThreadSelectArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int = Field(..., gt=0, description="Thread ID to select")


class BreakpointNumberArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    number: int = Field(..., gt=0, description="Breakpoint number")


class FrameSelectArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    frame_number: int = Field(..., ge=0, description="Frame number (0 is current/innermost frame)")


class CallFunctionArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    function_call: str = Field(
        ...,
        description="Function call expression (e.g., 'printf(\"hello\\n\")' or 'my_func(arg1, arg2)')",
    )
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class GetRegistersArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: Optional[int | str] = Field(
        None,
        description="Thread ID override as an integer or numeric string",
    )
    frame: Optional[int | str] = Field(
        None,
        description="Frame number override as an integer or numeric string",
    )
    register_numbers: list[int | str] = Field(
        default_factory=list,
        description=(
            "Optional explicit register numbers to query. "
            "Accepts integers or numeric strings."
        ),
    )
    register_names: list[str] = Field(
        default_factory=list,
        description=(
            "Optional explicit register names to query (for example ['rax', 'rip']). "
            "Resolved to register numbers at runtime."
        ),
    )
    include_vector_registers: bool = Field(
        True,
        description=(
            "When false, omit vector/SIMD-style registers from the returned set where names are available."
        ),
    )
    max_registers: Optional[int] = Field(
        None,
        gt=0,
        description="Optional upper bound on the number of returned register records.",
    )
    value_format: Literal["hex", "natural"] = Field(
        "hex",
        description="Value rendering mode: 'hex' for MI format 'x', 'natural' for MI format 'N'.",
    )

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing positive thread IDs."""

        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str | None) -> int | None:
        """Accept numeric strings while enforcing non-negative frame indices."""

        return _coerce_int_like(value, field_name="frame", minimum=0, allow_none=True)

    @field_validator("register_numbers")
    @classmethod
    def validate_register_numbers(cls, value: list[int | str]) -> list[int]:
        """Accept numeric-string register numbers while enforcing positivity."""

        normalized: list[int] = []
        for index, raw_number in enumerate(value):
            normalized_number = _coerce_int_like(
                raw_number,
                field_name=f"register_numbers[{index}]",
                minimum=0,
                allow_none=False,
            )
            if normalized_number is None:
                raise ValueError(f"register_numbers[{index}] is required")
            normalized.append(normalized_number)
        return normalized

    @field_validator("register_names")
    @classmethod
    def validate_register_names(cls, value: list[str]) -> list[str]:
        """Reject empty register-name selectors."""

        normalized: list[str] = []
        for index, register_name in enumerate(value):
            text = register_name.strip()
            if not text:
                raise ValueError(f"register_names[{index}] must be a non-empty string")
            normalized.append(text)
        return normalized


class ReadMemoryArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    address: str = Field(..., description="Address expression to read from")
    count: int = Field(..., gt=0, description="Number of addressable memory units to read")
    offset: int = Field(0, ge=0, description="Optional offset relative to address")


class SessionIdArgs(StrictArgsModel):
    """Arguments for tools that only need session_id."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")


class InferiorSelectArgs(StrictArgsModel):
    """Arguments for selecting one inferior by its GDB-visible numeric ID."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    inferior_id: int = Field(
        ...,
        gt=0,
        description="Inferior ID from gdb_list_inferiors",
    )


class FollowForkModeArgs(StrictArgsModel):
    """Arguments for configuring follow-fork-mode."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    mode: Literal["parent", "child"] = Field(
        ...,
        description="Whether GDB should follow the parent or child after fork/vfork.",
    )


class DetachOnForkArgs(StrictArgsModel):
    """Arguments for configuring detach-on-fork."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    enabled: bool = Field(
        ...,
        description="Whether GDB should detach from the non-followed side of a fork.",
    )


class WaitForStopArgs(StrictArgsModel):
    """Arguments for waiting on the next stop notification."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    timeout_sec: int = Field(30, gt=0, description="Maximum time to wait for a stop event")
    stop_reasons: list[str] = Field(
        default_factory=list,
        description="Optional stop reasons that should count as a match",
    )


class CaptureMemoryRangeArgs(StrictArgsModel):
    """One explicit memory range to include in a capture bundle."""

    address: str = Field(..., description="Address expression to read from")
    count: int = Field(..., gt=0, description="Number of bytes to capture for this range")
    offset: int = Field(0, ge=0, description="Optional offset relative to address")
    name: Optional[str] = Field(
        None,
        description="Optional stable label used in reports and failed-sections output",
    )


class ListSessionsArgs(StrictArgsModel):
    """Arguments for tools that do not require any parameters."""


class BatchStepArgs(StrictArgsModel):
    """One validated batch step definition."""

    tool: BatchStepToolName = Field(..., description="Existing session-scoped tool to execute")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        description="Tool-specific arguments excluding session_id, which comes from the batch",
    )
    label: Optional[str] = Field(
        None,
        description="Optional human-readable label to make batch results easier to scan",
    )


BatchStepInput: TypeAlias = BatchStepArgs | BatchStepToolName


class BatchArgs(StrictArgsModel):
    """Arguments for executing a structured batch against one live session."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    steps: list[BatchStepInput] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered step list. "
            "Each entry can be either a full object "
            "({'tool': ..., 'arguments': {...}, 'label': ...}) "
            "or a shorthand tool-name string."
        ),
    )
    fail_fast: bool = Field(
        True,
        description="Stop executing later steps after the first error result",
    )
    capture_stop_events: bool = Field(
        True,
        description="Include any new stop event produced by each step in the batch result",
    )


class CaptureBundleArgs(StrictArgsModel):
    """Arguments for writing a structured capture bundle to disk."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    output_dir: Optional[str] = Field(
        None,
        description="Directory in which to create the capture bundle. Defaults to artifact_root or the system temp directory.",
    )
    bundle_name: Optional[str] = Field(
        None,
        description="Optional deterministic subdirectory name for the bundle.",
    )
    expressions: list[str] = Field(
        default_factory=list,
        description="Expressions to evaluate and include in the bundle.",
    )
    memory_ranges: list[CaptureMemoryRangeArgs | str] = Field(
        default_factory=list,
        description=(
            "Explicit memory ranges to capture. "
            "Each entry can be either a structured object or shorthand "
            "string '<address>:<count>' (optional offset: '<address>:<count>@<offset>'). "
            "Each range is opt-in and bounded by server-side size limits."
        ),
    )
    max_frames: int = Field(
        100,
        gt=0,
        description="Maximum number of frames to include per thread backtrace.",
    )
    include_threads: bool = Field(True, description="Capture thread inventory.")
    include_backtraces: bool = Field(
        True, description="Capture backtraces for all enumerated threads."
    )
    include_frame: bool = Field(True, description="Capture the currently selected frame.")
    include_variables: bool = Field(
        True, description="Capture local variables for the current selection."
    )
    include_registers: bool = Field(
        True, description="Capture registers for the current selection."
    )
    include_transcript: bool = Field(True, description="Capture the bounded command transcript.")
    include_stop_history: bool = Field(True, description="Capture the bounded stop-event history.")


class RunUntilFailureFailureArgs(StrictArgsModel):
    """Failure predicates for repeat-until-failure campaigns."""

    failure_on_error: bool = Field(
        True,
        description="Treat an error result from gdb_run or session startup as a matching failure.",
    )
    failure_on_timeout: bool = Field(
        True,
        description="Treat a timeout from gdb_run as a matching failure.",
    )
    stop_reasons: list[str] = Field(
        default_factory=lambda: ["signal-received", "exited-signalled"],
        description="Stop reasons that should count as a matching failure.",
    )
    execution_states: list[str] = Field(
        default_factory=list,
        description="Inferior execution states that should count as a matching failure.",
    )
    exit_codes: list[int] = Field(
        default_factory=list,
        description="Exit codes that should count as a matching failure.",
    )
    result_text_regex: Optional[str] = Field(
        None,
        description="Regular expression applied to the serialized run result payload.",
    )


class RunUntilFailureCaptureArgs(StrictArgsModel):
    """Capture settings used when a run-until-failure campaign matches."""

    enabled: bool = Field(True, description="Capture a forensic bundle when a failure matches.")
    output_dir: Optional[str] = Field(
        None,
        description="Directory in which to place the capture bundle for the matching iteration.",
    )
    bundle_name_prefix: Optional[str] = Field(
        None,
        description="Deterministic bundle name prefix. The iteration number is appended automatically.",
    )
    bundle_name: Optional[str] = Field(
        None,
        description=(
            "Optional exact bundle name to use for the matching iteration. "
            "Cannot be combined with bundle_name_prefix."
        ),
    )
    expressions: list[str] = Field(
        default_factory=list,
        description="Expressions to evaluate and include in the capture bundle.",
    )
    memory_ranges: list[CaptureMemoryRangeArgs | str] = Field(
        default_factory=list,
        description=(
            "Explicit memory ranges to capture when a failure matches. "
            "Each entry can be either a structured object or shorthand "
            "string '<address>:<count>' (optional offset: '<address>:<count>@<offset>')."
        ),
    )
    max_frames: int = Field(100, gt=0, description="Maximum frames per thread backtrace.")
    include_threads: bool = Field(True, description="Capture thread inventory.")
    include_backtraces: bool = Field(True, description="Capture backtraces for all threads.")
    include_frame: bool = Field(True, description="Capture the currently selected frame.")
    include_variables: bool = Field(
        True, description="Capture variables for the current selection."
    )
    include_registers: bool = Field(
        True, description="Capture registers for the current selection."
    )
    include_transcript: bool = Field(True, description="Capture the bounded command transcript.")
    include_stop_history: bool = Field(True, description="Capture the bounded stop-event history.")

    @model_validator(mode="after")
    def validate_bundle_naming(self) -> "RunUntilFailureCaptureArgs":
        """Reject ambiguous capture naming configuration."""

        if self.bundle_name is not None and self.bundle_name_prefix is not None:
            raise ValueError("capture.bundle_name and capture.bundle_name_prefix are mutually exclusive")
        return self


class RunUntilFailureArgs(StrictArgsModel):
    """Arguments for repeating fresh-session runs until one failure matches."""

    startup: StartSessionArgs = Field(
        default_factory=lambda: StartSessionArgs.model_validate({}),
        description="Session startup configuration used for every iteration.",
    )
    setup_steps: list[BatchStepInput] = Field(
        default_factory=list,
        description="Optional structured setup steps run after startup and before gdb_run.",
    )
    run_args: Optional[list[str] | str] = Field(
        None,
        description=(
            "Arguments passed to gdb_run for each iteration. "
            "Accepts either an explicit argv list or one shell-style string."
        ),
    )
    run_timeout_sec: int = Field(30, gt=0, description="Timeout for each gdb_run attempt.")
    max_iterations: int = Field(1, gt=0, description="Maximum number of iterations to attempt.")
    failure: RunUntilFailureFailureArgs = Field(
        default_factory=lambda: RunUntilFailureFailureArgs.model_validate({}),
        description="Failure predicates that stop the campaign.",
    )
    capture: RunUntilFailureCaptureArgs = Field(
        default_factory=lambda: RunUntilFailureCaptureArgs.model_validate({}),
        description="Capture settings used when a failure matches.",
    )


class EmptyQuery(StrictArgsModel):
    """Empty object payload for query and no-op action wrappers."""


class BreakpointSelectorArgs(StrictArgsModel):
    """Selector for one existing breakpoint/watchpoint/catchpoint number."""

    number: int = Field(..., gt=0, description="Breakpoint number")


class ThreadSelectorArgs(StrictArgsModel):
    """Selector for one thread by numeric thread ID."""

    thread_id: int = Field(..., gt=0, description="Thread ID")


class FrameSelectorArgs(StrictArgsModel):
    """Selector for one frame by zero-based frame index."""

    frame: int = Field(..., ge=0, description="Frame number (0 is innermost/current)")


class ThreadFrameContextArgs(StrictArgsModel):
    """Optional thread/frame inspection context override."""

    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    frame: int | None = Field(None, ge=0, description="Optional frame override")


class SessionQueryListAction(StrictArgsModel):
    action: Literal["list"] = Field(..., description="List all active sessions")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionQueryStatusAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["status"] = Field(..., description="Query one live session")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionQueryArgs(
    RootModel[
        Annotated[
            SessionQueryListAction | SessionQueryStatusAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for session queries."""


class SessionManageStopAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["stop"] = Field(..., description="Stop one live session")
    session: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionManageArgs(
    RootModel[
        Annotated[
            SessionManageStopAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for session lifecycle mutations."""


class InferiorQueryListAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["list"] = Field(..., description="List inferiors in one live session")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class InferiorQueryCurrentAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["current"] = Field(..., description="Inspect the selected inferior")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class InferiorQueryArgs(
    RootModel[
        Annotated[
            InferiorQueryListAction | InferiorQueryCurrentAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for inferior queries."""


class InferiorCreatePayload(StrictArgsModel):
    executable: str | None = Field(
        None,
        description="Optional executable to associate with the new inferior.",
    )
    make_current: bool = Field(
        False,
        description="Whether to leave the new inferior selected after creation.",
    )

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="executable")


class InferiorIdPayload(StrictArgsModel):
    inferior_id: int = Field(..., gt=0, description="Inferior ID")


class InferiorFollowForkPayload(StrictArgsModel):
    mode: Literal["parent", "child"] = Field(
        ...,
        description="Whether GDB should follow the parent or child after fork/vfork.",
    )


class InferiorDetachOnForkPayload(StrictArgsModel):
    enabled: bool = Field(..., description="Whether GDB should detach from the non-followed fork.")


class InferiorManageCreateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["create"] = Field(..., description="Create a new inferior")
    inferior: InferiorCreatePayload


class InferiorManageRemoveAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["remove"] = Field(..., description="Remove one inferior")
    inferior: InferiorIdPayload


class InferiorManageSelectAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["select"] = Field(..., description="Select the active inferior")
    inferior: InferiorIdPayload


class InferiorManageFollowForkAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["set_follow_fork_mode"] = Field(..., description="Change follow-fork-mode")
    inferior: InferiorFollowForkPayload


class InferiorManageDetachOnForkAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["set_detach_on_fork"] = Field(..., description="Change detach-on-fork")
    inferior: InferiorDetachOnForkPayload


class InferiorManageArgs(
    RootModel[
        Annotated[
            InferiorManageCreateAction
            | InferiorManageRemoveAction
            | InferiorManageSelectAction
            | InferiorManageFollowForkAction
            | InferiorManageDetachOnForkAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for inferior mutations."""


class ExecutionWaitArgs(StrictArgsModel):
    until: Literal["acknowledged", "stop"] = Field(
        "stop",
        description="Whether to return when GDB acknowledges running or when a stop is observed.",
    )
    timeout_sec: int | None = Field(
        None,
        gt=0,
        description="Optional timeout override for the execution command.",
    )


class ExecutionRunPayload(StrictArgsModel):
    args: list[str] | str | None = Field(
        None,
        description="Optional inferior argv override for this run.",
    )
    wait: ExecutionWaitArgs | None = Field(
        None,
        description="Optional wait policy for the run command.",
    )


class ExecutionControlPayload(StrictArgsModel):
    wait: ExecutionWaitArgs | None = Field(
        None,
        description="Optional wait policy for the execution command.",
    )


class ExecutionWaitForStopPayload(StrictArgsModel):
    timeout_sec: int = Field(30, gt=0, description="Maximum time to wait for a stop event")
    stop_reasons: list[str] = Field(
        default_factory=list,
        description="Optional stop reasons that should count as a match",
    )


class ExecutionRunAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["run"] = Field(..., description="Start the inferior")
    execution: ExecutionRunPayload


class ExecutionContinueAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["continue"] = Field(..., description="Continue execution")
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionInterruptAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["interrupt"] = Field(..., description="Interrupt the running inferior")
    execution: EmptyQuery = Field(default_factory=EmptyQuery)


class ExecutionStepAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["step"] = Field(..., description="Step into the next line or instruction")
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionNextAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["next"] = Field(..., description="Step over the next line or instruction")
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionFinishAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["finish"] = Field(..., description="Finish the current frame")
    execution: ExecutionControlPayload = Field(
        default_factory=lambda: ExecutionControlPayload.model_validate({})
    )


class ExecutionWaitForStopAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["wait_for_stop"] = Field(..., description="Wait for the next stop event")
    execution: ExecutionWaitForStopPayload


class ExecutionManageArgs(
    RootModel[
        Annotated[
            ExecutionRunAction
            | ExecutionContinueAction
            | ExecutionInterruptAction
            | ExecutionStepAction
            | ExecutionNextAction
            | ExecutionFinishAction
            | ExecutionWaitForStopAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for execution control."""


class BreakpointCodeCreateArgs(StrictArgsModel):
    kind: Literal["code"] = Field(..., description="Create a code breakpoint")
    location: str = Field(..., description="Function, file:line, or *address")
    condition: str | None = Field(None, description="Optional breakpoint condition")
    temporary: bool = Field(False, description="Whether the breakpoint is temporary")

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="location")
        if normalized is None:
            raise ValueError("location is required")
        return normalized


class BreakpointWatchCreateArgs(StrictArgsModel):
    kind: Literal["watch"] = Field(..., description="Create a watchpoint")
    expression: str = Field(..., description="Expression to watch")
    access: Literal["write", "read", "access"] = Field(
        "write",
        description="Whether to stop on writes only, reads only, or any access",
    )

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="expression")
        if normalized is None:
            raise ValueError("expression is required")
        return normalized


class BreakpointCatchCreateArgs(StrictArgsModel):
    kind: Literal["catch"] = Field(..., description="Create a catchpoint")
    event: Literal[
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
    ] = Field(..., description="Debugger event kind to catch")
    argument: str | None = Field(
        None,
        description="Optional event filter such as a syscall name or signal name.",
    )
    temporary: bool = Field(False, description="Use a temporary catchpoint")

    @field_validator("argument")
    @classmethod
    def validate_argument(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="argument")


BreakpointCreateArgs = Annotated[
    BreakpointCodeCreateArgs | BreakpointWatchCreateArgs | BreakpointCatchCreateArgs,
    Field(discriminator="kind"),
]


class BreakpointUpdateChangesArgs(StrictArgsModel):
    condition: str | None = Field(None, description="New condition to set on the breakpoint")
    clear_condition: bool = Field(False, description="Remove the existing breakpoint condition")

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="condition")

    @model_validator(mode="after")
    def validate_requested_change(self) -> "BreakpointUpdateChangesArgs":
        if self.condition is None and self.clear_condition is False:
            raise ValueError("At least one breakpoint change is required")
        if self.condition is not None and self.clear_condition:
            raise ValueError("condition and clear_condition are mutually exclusive")
        return self


class BreakpointListQueryArgs(StrictArgsModel):
    kinds: list[Literal["code", "watch", "catch"]] = Field(
        default_factory=list,
        description="Optional breakpoint kinds to include",
    )
    enabled: bool | None = Field(None, description="Optional enabled-state filter")


class BreakpointGetQueryArgs(StrictArgsModel):
    number: int = Field(..., gt=0, description="Breakpoint number")


class BreakpointManageCreateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["create"] = Field(..., description="Create a breakpoint/watchpoint/catchpoint")
    breakpoint: BreakpointCreateArgs


class BreakpointManageUpdateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["update"] = Field(..., description="Update one existing breakpoint")
    breakpoint: BreakpointSelectorArgs
    changes: BreakpointUpdateChangesArgs


class BreakpointManageNumberAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["delete", "enable", "disable"] = Field(..., description="Mutate one existing breakpoint")
    breakpoint: BreakpointSelectorArgs


class BreakpointManageArgs(
    RootModel[
        Annotated[
            BreakpointManageCreateAction
            | BreakpointManageUpdateAction
            | BreakpointManageNumberAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for breakpoint mutations."""


class BreakpointQueryListAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["list"] = Field(..., description="List all breakpoints")
    query: BreakpointListQueryArgs = Field(
        default_factory=lambda: BreakpointListQueryArgs.model_validate({})
    )


class BreakpointQueryGetAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["get"] = Field(..., description="Fetch one breakpoint")
    query: BreakpointGetQueryArgs


class BreakpointQueryArgs(
    RootModel[
        Annotated[
            BreakpointQueryListAction | BreakpointQueryGetAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for breakpoint queries."""


class LocationCurrentArgs(StrictArgsModel):
    kind: Literal["current"] = Field(..., description="Use the current selected location")


class LocationFunctionArgs(StrictArgsModel):
    kind: Literal["function"] = Field(..., description="Resolve one function")
    function: str = Field(..., description="Function name selector")

    @field_validator("function")
    @classmethod
    def validate_function(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="function")
        if normalized is None:
            raise ValueError("function is required")
        return normalized


class LocationAddressArgs(StrictArgsModel):
    kind: Literal["address"] = Field(..., description="Resolve one address")
    address: str = Field(..., description="Address selector")

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="address")
        if normalized is None:
            raise ValueError("address is required")
        return normalized


class LocationAddressRangeArgs(StrictArgsModel):
    kind: Literal["address_range"] = Field(..., description="Resolve an address range")
    start_address: str = Field(..., description="Start of address range")
    end_address: str = Field(..., description="End of address range")

    @field_validator("start_address", "end_address")
    @classmethod
    def validate_range_address(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "address"
        normalized = _normalize_optional_text(value, field_name=field_name)
        if normalized is None:
            raise ValueError(f"{field_name} is required")
        return normalized


class LocationFileLineArgs(StrictArgsModel):
    kind: Literal["file_line"] = Field(..., description="Resolve one source file line")
    file: str = Field(..., description="Source file selector")
    line: int = Field(..., gt=0, description="Source line selector")

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="file")
        if normalized is None:
            raise ValueError("file is required")
        return normalized


class LocationFileRangeArgs(StrictArgsModel):
    kind: Literal["file_range"] = Field(..., description="Resolve one explicit source file range")
    file: str = Field(..., description="Source file selector")
    start_line: int = Field(..., gt=0, description="Start line of the range")
    end_line: int = Field(..., gt=0, description="End line of the range")

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="file")
        if normalized is None:
            raise ValueError("file is required")
        return normalized

    @model_validator(mode="after")
    def validate_range(self) -> "LocationFileRangeArgs":
        if self.start_line > self.end_line:
            raise ValueError("start_line must be <= end_line")
        return self


LocationSelectorArgs = Annotated[
    LocationCurrentArgs
    | LocationFunctionArgs
    | LocationAddressArgs
    | LocationAddressRangeArgs
    | LocationFileLineArgs
    | LocationFileRangeArgs,
    Field(discriminator="kind"),
]


class ContextBacktraceQueryArgs(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    max_frames: int = Field(100, gt=0, description="Maximum number of frames to return")


class ContextFrameQueryArgs(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0, description="Optional thread override")
    frame: int | None = Field(None, ge=0, description="Optional frame override")


class ContextQueryThreadsAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["threads"] = Field(..., description="List threads")
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class ContextQueryBacktraceAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["backtrace"] = Field(..., description="Inspect a backtrace")
    query: ContextBacktraceQueryArgs = Field(
        default_factory=lambda: ContextBacktraceQueryArgs.model_validate({})
    )


class ContextQueryFrameAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["frame"] = Field(..., description="Inspect frame information")
    query: ContextFrameQueryArgs = Field(
        default_factory=lambda: ContextFrameQueryArgs.model_validate({})
    )


class ContextQueryArgs(
    RootModel[
        Annotated[
            ContextQueryThreadsAction
            | ContextQueryBacktraceAction
            | ContextQueryFrameAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for thread/frame queries."""


class ContextManageSelectThreadAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["select_thread"] = Field(..., description="Select the current thread")
    context: ThreadSelectorArgs


class ContextManageSelectFrameAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["select_frame"] = Field(..., description="Select the current frame")
    context: FrameSelectorArgs


class ContextManageArgs(
    RootModel[
        Annotated[
            ContextManageSelectThreadAction | ContextManageSelectFrameAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for thread/frame selection."""


class InspectEvaluateQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(None, description="Optional thread/frame override")
    expression: str = Field(..., description="Expression to evaluate")

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="expression")
        if normalized is None:
            raise ValueError("expression is required")
        return normalized


class InspectVariablesQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(None, description="Optional thread/frame override")


class InspectRegistersQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(None, description="Optional thread/frame override")
    register_numbers: list[int | str] = Field(default_factory=list, description="Optional register-number selectors")
    register_names: list[str] = Field(default_factory=list, description="Optional register-name selectors")
    include_vector_registers: bool = Field(True, description="Whether to include vector/SIMD registers")
    max_registers: int | None = Field(None, gt=0, description="Optional maximum register count")
    value_format: Literal["hex", "natural"] = Field("hex", description="Value rendering mode")

    @field_validator("register_numbers")
    @classmethod
    def validate_register_numbers(cls, value: list[int | str]) -> list[int]:
        normalized: list[int] = []
        for index, raw_number in enumerate(value):
            normalized_number = _coerce_int_like(
                raw_number,
                field_name=f"register_numbers[{index}]",
                minimum=0,
                allow_none=False,
            )
            if normalized_number is None:
                raise ValueError(f"register_numbers[{index}] is required")
            normalized.append(normalized_number)
        return normalized

    @field_validator("register_names")
    @classmethod
    def validate_register_names(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for index, register_name in enumerate(value):
            text = register_name.strip()
            if not text:
                raise ValueError(f"register_names[{index}] must be a non-empty string")
            normalized.append(text)
        return normalized


class InspectMemoryQueryArgs(StrictArgsModel):
    address: str = Field(..., description="Address expression to read from")
    count: int = Field(..., gt=0, description="Number of addressable memory units to read")
    offset: int = Field(0, ge=0, description="Optional offset relative to address")

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="address")
        if normalized is None:
            raise ValueError("address is required")
        return normalized


class InspectDisassemblyQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(None, description="Optional thread/frame override")
    location: LocationSelectorArgs
    instruction_count: int = Field(32, gt=0, description="Upper bound on returned instructions")
    mode: Literal["assembly", "mixed"] = Field(
        "mixed",
        description="Whether to request assembly only or mixed source/assembly output",
    )


class InspectSourceQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(None, description="Optional thread/frame override")
    location: LocationSelectorArgs
    context_before: int = Field(5, ge=0, description="Lines before the focal location")
    context_after: int = Field(5, ge=0, description="Lines after the focal location")


class InspectEvaluateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["evaluate"] = Field(..., description="Evaluate one expression")
    query: InspectEvaluateQueryArgs


class InspectVariablesAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["variables"] = Field(..., description="Inspect variables in one context")
    query: InspectVariablesQueryArgs = Field(
        default_factory=lambda: InspectVariablesQueryArgs.model_validate({})
    )


class InspectRegistersAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["registers"] = Field(..., description="Inspect registers in one context")
    query: InspectRegistersQueryArgs = Field(
        default_factory=lambda: InspectRegistersQueryArgs.model_validate({})
    )


class InspectMemoryAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["memory"] = Field(..., description="Read target memory")
    query: InspectMemoryQueryArgs


class InspectDisassemblyAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["disassembly"] = Field(..., description="Inspect disassembly for one location")
    query: InspectDisassemblyQueryArgs


class InspectSourceAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["source"] = Field(..., description="Inspect source context for one location")
    query: InspectSourceQueryArgs


class InspectQueryArgs(
    RootModel[
        Annotated[
            InspectEvaluateAction
            | InspectVariablesAction
            | InspectRegistersAction
            | InspectMemoryAction
            | InspectDisassemblyAction
            | InspectSourceAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for read-only inspection operations."""


def build_tool_definitions() -> list[Tool]:
    """Build the MCP tool definitions exposed by this server."""

    return [
        Tool(
            name="gdb_session_start",
            description=(
                "Start a new GDB debugging session. Can load an executable, core dump, "
                "or run custom initialization commands. "
                "Automatically detects and reports important warnings such as: "
                "missing debug symbols (not compiled with -g), file not found, or invalid executable. "
                "Check the 'warnings' field in the response for critical issues that may affect debugging. "
                "Available parameters: program (executable path), args (program arguments), "
                "core (core dump path - uses --core flag for proper symbol resolution), "
                "init_commands (GDB commands to run after environment setup), "
                "env (environment variables applied before init_commands), gdb_path (GDB binary path), "
                "working_dir (directory to run program from). "
                "NOTE: 'args' and 'core' are mutually exclusive in one startup request. "
                "The success response includes 'target_loaded' so callers can distinguish "
                "between 'GDB started' and 'requested target actually loaded'. "
                "IMPORTANT for core dump debugging: Set 'sysroot' and 'solib-search-path' AFTER "
                "loading the core (either via 'core' parameter or 'core-file' init_command) "
                "for symbols to resolve correctly. "
                "Returns a session_id integer that must be passed to all other GDB tools."
            ),
            inputSchema=StartSessionArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_session_query",
            description=(
                "Query session inventory or inspect one live session. "
                "Use action='list' to enumerate active sessions or action='status' to inspect one session."
            ),
            inputSchema=SessionQueryArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_session_manage",
            description="Mutate session lifecycle state, such as stopping one live session.",
            inputSchema=SessionManageArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_inferior_query",
            description=(
                "Query inferior inventory or the currently selected inferior inside one live session."
            ),
            inputSchema=InferiorQueryArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_inferior_manage",
            description=(
                "Create, remove, select, or reconfigure inferiors and fork-follow settings."
            ),
            inputSchema=InferiorManageArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_execution_manage",
            description=(
                "Run, continue, interrupt, step, next, finish, or wait for stop events "
                "using action-scoped execution payloads."
            ),
            inputSchema=ExecutionManageArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_breakpoint_query",
            description="List breakpoints or fetch one breakpoint record by number.",
            inputSchema=BreakpointQueryArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_breakpoint_manage",
            description=(
                "Create, delete, enable, disable, or update code breakpoints, watchpoints, "
                "and catchpoints through one action-based tool."
            ),
            inputSchema=BreakpointManageArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_context_query",
            description="List threads or inspect backtraces and frame information.",
            inputSchema=ContextQueryArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_context_manage",
            description="Select the current thread or frame in one live session.",
            inputSchema=ContextManageArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_inspect_query",
            description=(
                "Evaluate expressions and inspect variables, registers, memory, source context, "
                "or disassembly without using raw debugger commands."
            ),
            inputSchema=InspectQueryArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_workflow_batch",
            description=(
                "Execute a structured sequence of session-scoped v2 GDB tools atomically within one session. "
                "Each step names an existing tool plus tool-specific arguments excluding "
                "session_id, which is inherited from the enclosing batch request."
            ),
            inputSchema=BatchArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_capture_bundle",
            description=(
                "Write a structured forensic capture bundle to disk for the current session. "
                "The bundle includes a manifest plus JSON artifacts such as session status, "
                "last stop event, optional stop history, optional command transcript, thread "
                "inventory, thread backtraces, current frame, variables, registers, requested "
                "expression evaluations, and any explicitly requested memory ranges. "
                "Use output_dir and bundle_name when you need deterministic artifact paths."
            ),
            inputSchema=CaptureBundleArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_run_until_failure",
            description=(
                "Run fresh debugger sessions repeatedly until a failure predicate matches or the "
                "iteration limit is reached. "
                "Each iteration uses the same startup configuration, optional structured setup "
                "steps, and one gdb_execution_manage(action='run') invocation. "
                "When a failure matches, the tool can automatically write a capture bundle to "
                "disk and return the bundle metadata, including any explicitly requested memory "
                "ranges."
            ),
            inputSchema=RunUntilFailureArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_execute_command",
            description=(
                "Execute a GDB command. Supports both CLI and MI commands. "
                "CLI commands (like 'info breakpoints', 'list', 'print x') are automatically "
                "handled and their output is formatted for readability. "
                "MI commands (starting with '-', like '-break-list', '-exec-run') return "
                "structured data. "
                "Supports an optional timeout_sec override. "
                "NOTE: For calling functions in the target process, prefer using the dedicated "
                "gdb_call_function tool instead of 'call' command, as it provides better "
                "structured output and can be separately permissioned. "
                "Common examples: 'info breakpoints', 'info threads', 'run', 'print variable', "
                "'list main', 'disassemble func'. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=ExecuteCommandArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_attach_process",
            description=(
                "Attach GDB to a running process by PID. "
                "This is a privileged operation that should be separately permissioned from "
                "general command execution when possible. "
                "On success, the attached process is typically paused and inspectable. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=AttachProcessArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_call_function",
            description=(
                "Call a function in the target process. "
                "WARNING: This is a privileged operation that executes code in the debugged program. "
                "It can call any function accessible in the current context, including: "
                "- Standard library functions: printf, malloc, free, etc. "
                "- Program functions: any function defined in the program "
                "- System calls via wrappers "
                "The function executes with full privileges of the debugged process. "
                "Use with caution as it may have side effects and modify program state. "
                "Supports an optional timeout_sec override. "
                "Examples: 'printf(\"debug: x=%d\\n\", x)', 'my_cleanup_func()', 'strlen(str)'. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=CallFunctionArgs.model_json_schema(),
        ),
    ]
