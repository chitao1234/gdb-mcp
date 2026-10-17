"""Helpers for converting raw GDB/MI payloads into typed domain records."""

from __future__ import annotations

from .models import (
    BacktraceInfo,
    BreakpointListInfo,
    BreakpointRecord,
    FrameInfo,
    FrameRecord,
    FrameSelectionInfo,
    MemoryBlockRecord,
    RegisterRecord,
    RegistersInfo,
    ThreadListInfo,
    ThreadRecord,
    ThreadSelectionInfo,
    VariableRecord,
    VariablesInfo,
)
from .parsing import as_mapping, as_optional_mapping, as_record_list, as_text


def string_field(payload: object, key: str) -> str | None:
    """Return a string field from a raw payload mapping when available."""

    return as_text(as_mapping(payload).get(key))


def thread_records(payload: object, key: str = "threads") -> list[ThreadRecord]:
    """Normalize a thread-record list from a raw payload mapping."""

    return as_record_list(as_mapping(payload).get(key))


def frame_records(payload: object, key: str = "stack") -> list[FrameRecord]:
    """Normalize a frame-record list from a raw payload mapping."""

    return as_record_list(as_mapping(payload).get(key))


def breakpoint_record(payload: object) -> BreakpointRecord:
    """Normalize a single breakpoint payload."""

    raw_mapping = as_mapping(payload)
    return as_mapping(raw_mapping.get("bkpt", raw_mapping))


def breakpoint_records(payload: object) -> list[BreakpointRecord]:
    """Normalize a list of breakpoint payloads."""

    breakpoint_table = as_mapping(as_mapping(payload).get("BreakpointTable"))
    return as_record_list(breakpoint_table.get("body"))


def variable_records(payload: object) -> list[VariableRecord]:
    """Normalize a list of variable payloads."""

    return as_record_list(as_mapping(payload).get("variables"))


def register_records(payload: object) -> list[RegisterRecord]:
    """Normalize a list of register payloads."""

    return as_record_list(as_mapping(payload).get("register-values"))


def memory_block_records(payload: object) -> list[MemoryBlockRecord]:
    """Normalize a list of memory blocks."""

    return as_record_list(as_mapping(payload).get("memory"))


def thread_list_info_from_payload(payload: object) -> ThreadListInfo:
    """Build a typed thread-list payload from raw MI data."""

    threads = thread_records(payload)
    return ThreadListInfo(
        threads=threads,
        current_thread_id=string_field(payload, "current-thread-id"),
        count=len(threads),
    )


def thread_selection_info_from_payload(thread_id: int, payload: object) -> ThreadSelectionInfo:
    """Build a typed thread-selection payload from raw MI data."""

    return ThreadSelectionInfo(
        thread_id=thread_id,
        new_thread_id=string_field(payload, "new-thread-id"),
        frame=as_optional_mapping(as_mapping(payload).get("frame")),
    )


def backtrace_info_from_payload(thread_id: int | None, payload: object) -> BacktraceInfo:
    """Build a typed backtrace payload from raw MI data."""

    frames = frame_records(payload)
    return BacktraceInfo(thread_id=thread_id, frames=frames, count=len(frames))


def frame_info_from_payload(payload: object) -> FrameInfo:
    """Build a typed current-frame payload from raw MI data."""

    return FrameInfo(frame=as_optional_mapping(as_mapping(payload).get("frame")))


def frame_selection_info_from_payload(frame_number: int, payload: object) -> FrameSelectionInfo:
    """Build a typed frame-selection payload from raw MI data."""

    return FrameSelectionInfo(
        frame_number=frame_number,
        frame=as_optional_mapping(as_mapping(payload).get("frame")),
    )


def variables_info_from_payload(
    thread_id: int | None, frame: int, payload: object
) -> VariablesInfo:
    """Build a typed local-variable payload from raw MI data."""

    return VariablesInfo(thread_id=thread_id, frame=frame, variables=variable_records(payload))


def registers_info_from_payload(payload: object) -> RegistersInfo:
    """Build a typed register payload from raw MI data."""

    return RegistersInfo(registers=register_records(payload))


def breakpoint_list_info_from_payload(payload: object) -> BreakpointListInfo:
    """Build a typed breakpoint-list payload from raw MI data."""

    breakpoints = breakpoint_records(payload)
    return BreakpointListInfo(breakpoints=breakpoints, count=len(breakpoints))
