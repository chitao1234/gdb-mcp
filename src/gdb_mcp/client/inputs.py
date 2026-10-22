"""Typed internal client inputs parsed from CLI namespaces."""

from __future__ import annotations

from dataclasses import dataclass

from gdb_mcp.contracts import (
    BatchStepToolName,
)


@dataclass(frozen=True, slots=True)
class SessionStepInput:
    """Typed workflow/setup step used by batch-oriented commands."""

    tool: BatchStepToolName
    label: str | None
    arguments: dict[str, object]
