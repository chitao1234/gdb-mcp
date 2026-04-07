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
