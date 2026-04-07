"""Typed internal client inputs parsed from CLI namespaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SessionQueryAction = Literal["list", "status"]
InferiorQueryAction = Literal["list", "current"]
InferiorManageAction = Literal[
    "create",
    "remove",
    "select",
    "set_follow_fork_mode",
    "set_detach_on_fork",
]
InferiorFollowForkMode = Literal["parent", "child"]
ExecutionWaitUntil = Literal["acknowledged", "stop"]
ExecutionManageAction = Literal[
    "run",
    "continue",
    "interrupt",
    "step",
    "next",
    "finish",
    "wait_for_stop",
]
ContextQueryAction = Literal["threads", "backtrace", "frame"]
ContextManageAction = Literal["select_thread", "select_frame"]
BreakpointKind = Literal["code", "watch", "catch"]
BreakpointAccess = Literal["write", "read", "access"]
BreakpointEvent = Literal[
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
BreakpointQueryAction = Literal["list", "get"]
BreakpointManageAction = Literal["create", "update", "delete", "enable", "disable"]
LocationKind = Literal[
    "current",
    "function",
    "address",
    "address_range",
    "file_line",
    "file_range",
]
InspectQueryAction = Literal[
    "evaluate",
    "variables",
    "registers",
    "memory",
    "disassembly",
    "source",
]
RegisterValueFormat = Literal["hex", "natural"]
DisassemblyMode = Literal["assembly", "mixed"]


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
