"""Typed internal client inputs parsed from CLI namespaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SessionQueryAction = Literal["list", "status"]


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
