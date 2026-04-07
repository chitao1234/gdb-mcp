"""Typed internal client inputs parsed from CLI namespaces."""

from __future__ import annotations

from dataclasses import dataclass

from gdb_mcp.contracts import (
    BatchStepToolName,
    BreakpointAccess,
    BreakpointEvent,
    BreakpointKind,
    BreakpointManageAction,
    BreakpointQueryAction,
    ContextManageAction,
    ContextQueryAction,
    DisassemblyMode,
    ExecutionManageAction,
    ExecutionWaitUntil,
    InferiorFollowForkMode,
    InferiorManageAction,
    InferiorQueryAction,
    InspectQueryAction,
    LocationKind,
    RegisterValueFormat,
    SessionQueryAction,
)


@dataclass(frozen=True, slots=True)
class SessionStartInput:
    """Parsed input for ``gdb_session_start``."""

    program: str | None
    args: tuple[str, ...]
    init_commands: tuple[str, ...]
    env: dict[str, str] | None
    core: str | None
    gdb_path: str | None
    working_dir: str | None


@dataclass(frozen=True, slots=True)
class SessionQueryInput:
    """Parsed input for ``gdb_session_query``."""

    action: SessionQueryAction
    session_id: int | None = None


@dataclass(frozen=True, slots=True)
class InferiorQueryInput:
    """Parsed input for ``gdb_inferior_query``."""

    action: InferiorQueryAction
    session_id: int


@dataclass(frozen=True, slots=True)
class InferiorManageInput:
    """Parsed input for ``gdb_inferior_manage``."""

    action: InferiorManageAction
    session_id: int
    executable: str | None
    make_current: bool | None
    inferior_id: int | None
    mode: InferiorFollowForkMode | None
    enabled: bool | None


@dataclass(frozen=True, slots=True)
class ExecutionWaitInput:
    """Optional wait configuration for execution actions."""

    until: ExecutionWaitUntil | None
    timeout_sec: int | None


@dataclass(frozen=True, slots=True)
class ExecutionManageInput:
    """Parsed input for ``gdb_execution_manage``."""

    action: ExecutionManageAction
    session_id: int
    args: tuple[str, ...]
    wait: ExecutionWaitInput | None
    timeout_sec: int | None
    stop_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextQueryInput:
    """Parsed input for ``gdb_context_query``."""

    action: ContextQueryAction
    session_id: int
    thread_id: int | None
    frame: int | None
    max_frames: int | None


@dataclass(frozen=True, slots=True)
class ContextManageInput:
    """Parsed input for ``gdb_context_manage``."""

    action: ContextManageAction
    session_id: int
    thread_id: int | None
    frame: int | None


@dataclass(frozen=True, slots=True)
class BreakpointQueryInput:
    """Parsed input for ``gdb_breakpoint_query``."""

    action: BreakpointQueryAction
    session_id: int
    number: int | None
    kinds: tuple[BreakpointKind, ...]
    enabled: bool | None


@dataclass(frozen=True, slots=True)
class BreakpointCreateInput:
    """Create payload for ``gdb_breakpoint_manage --action create``."""

    kind: BreakpointKind
    location: str | None
    expression: str | None
    access: BreakpointAccess | None
    event: BreakpointEvent | None
    argument: str | None
    condition: str | None
    temporary: bool
    kind_explicit: bool = False
    temporary_explicit: bool = False


@dataclass(frozen=True, slots=True)
class BreakpointManageInput:
    """Parsed input for ``gdb_breakpoint_manage``."""

    action: BreakpointManageAction
    session_id: int
    breakpoint: BreakpointCreateInput | None
    number: int | None
    condition: str | None
    clear_condition: bool | None


@dataclass(frozen=True, slots=True)
class LocationInput:
    """Typed source/disassembly location selector."""

    kind: LocationKind
    function: str | None = None
    address: str | None = None
    start_address: str | None = None
    end_address: str | None = None
    file: str | None = None
    line: int | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class InspectQueryInput:
    """Parsed input for ``gdb_inspect_query``."""

    action: InspectQueryAction
    session_id: int
    thread_id: int | None
    frame: int | None
    expression: str | None
    register_numbers: tuple[int, ...]
    register_names: tuple[str, ...]
    include_vector_registers: bool | None
    max_registers: int | None
    value_format: RegisterValueFormat | None
    memory_address: str | None
    count: int | None
    offset: int | None
    location: LocationInput | None
    instruction_count: int | None
    mode: DisassemblyMode | None
    context_before: int | None
    context_after: int | None
    location_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionStepInput:
    """Typed workflow/setup step used by batch-oriented commands."""

    tool: BatchStepToolName
    label: str | None
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class WorkflowBatchInput:
    """Parsed input for ``gdb_workflow_batch``."""

    session_id: int
    steps: tuple[SessionStepInput, ...]
    fail_fast: bool | None
    capture_stop_events: bool | None


@dataclass(frozen=True, slots=True)
class RunUntilFailureInput:
    """Parsed input for ``gdb_run_until_failure``."""

    startup_program: str | None
    startup_args: tuple[str, ...]
    startup_init_commands: tuple[str, ...]
    startup_env: dict[str, str] | None
    startup_gdb_path: str | None
    startup_working_dir: str | None
    startup_core: str | None
    setup_steps: tuple[SessionStepInput, ...] | None
    run_args: tuple[str, ...]
    run_timeout_sec: int | None
    max_iterations: int | None
    failure_on_error: bool | None
    failure_on_timeout: bool | None
    failure_stop_reasons: tuple[str, ...] | None
    failure_execution_states: tuple[str, ...] | None
    failure_exit_codes: tuple[int, ...] | None
    failure_result_text_regex: str | None
    capture_enabled: bool | None
    capture_output_dir: str | None
    capture_bundle_name_prefix: str | None
    capture_bundle_name: str | None
    capture_expressions: tuple[str, ...]
    capture_memory_ranges: tuple[str, ...]
    capture_max_frames: int | None
    capture_include_threads: bool | None
    capture_include_backtraces: bool | None
    capture_include_frame: bool | None
    capture_include_variables: bool | None
    capture_include_registers: bool | None
    capture_include_transcript: bool | None
    capture_include_stop_history: bool | None
