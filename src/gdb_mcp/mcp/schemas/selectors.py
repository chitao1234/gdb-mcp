"""Small shared selector payloads used across MCP request models."""

from __future__ import annotations

from typing import Optional

from pydantic import (
    Field,
)


from .common import StrictArgsModel


class CaptureMemoryRangeArgs(StrictArgsModel):
    """One explicit memory range to include in a capture bundle."""

    address: str = Field(..., description="Address expression to read from")
    count: int = Field(..., gt=0, description="Number of bytes to capture for this range")
    offset: int = Field(0, ge=0, description="Optional offset relative to address")
    name: Optional[str] = Field(
        None,
        description="Optional stable label used in reports and failed-sections output",
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
