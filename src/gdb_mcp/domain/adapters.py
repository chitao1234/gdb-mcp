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


def _as_int(value: object) -> int | None:
    """Parse an MI numeric field, which MI always spells as a string."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = as_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _as_bool(value: object) -> bool | None:
    """Parse an MI flag, which MI spells ``y``/``n``."""

    if isinstance(value, bool):
        return value
    text = as_text(value)
    if text is None:
        return None
    lowered = text.strip().lower()
    if lowered in {"y", "yes", "true", "1"}:
        return True
    if lowered in {"n", "no", "false", "0"}:
        return False
    return None


def _canonical_breakpoint(record: BreakpointRecord) -> BreakpointRecord:
    """Return a breakpoint record with canonical ``enabled`` and ``times``.

    The published contract promises a boolean and an integer, so the MI strings
    ("y", "3") are converted here. A value that cannot be parsed is left as it
    arrived instead of being dropped.
    """

    normalized = dict(record)
    enabled = _as_bool(normalized.get("enabled"))
    if enabled is not None:
        normalized["enabled"] = enabled
    times = _as_int(normalized.get("times"))
    if times is not None:
        normalized["times"] = times
    return normalized


def _canonical_frame(record: FrameRecord) -> FrameRecord:
    """Return a frame record with a canonical integer ``level``."""

    normalized = dict(record)
    level = _as_int(normalized.get("level"))
    if level is not None:
        normalized["level"] = level
    return normalized


def string_field(payload: object, key: str) -> str | None:
    """Return a string field from a raw payload mapping when available."""

    return as_text(as_mapping(payload).get(key))


def thread_records(payload: object, key: str = "threads") -> list[ThreadRecord]:
    """Normalize a thread-record list from a raw payload mapping."""

    return as_record_list(as_mapping(payload).get(key))


def frame_records(payload: object, key: str = "stack") -> list[FrameRecord]:
    """Normalize a frame-record list from a raw payload mapping."""

    return [_canonical_frame(record) for record in as_record_list(as_mapping(payload).get(key))]


def breakpoint_record(payload: object) -> BreakpointRecord:
    """Normalize a single breakpoint payload."""

    raw_mapping = as_mapping(payload)
    return _canonical_breakpoint(as_mapping(raw_mapping.get("bkpt", raw_mapping)))


def breakpoint_records(payload: object) -> list[BreakpointRecord]:
    """Normalize a list of breakpoint payloads."""

    breakpoint_table = as_mapping(as_mapping(payload).get("BreakpointTable"))
    return [
        _canonical_breakpoint(record) for record in as_record_list(breakpoint_table.get("body"))
    ]


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

    frame = as_optional_mapping(as_mapping(payload).get("frame"))
    return ThreadSelectionInfo(
        thread_id=thread_id,
        new_thread_id=string_field(payload, "new-thread-id"),
        frame=_canonical_frame(frame) if frame is not None else None,
    )


def backtrace_info_from_payload(thread_id: int | None, payload: object) -> BacktraceInfo:
    """Build a typed backtrace payload from raw MI data."""

    frames = frame_records(payload)
    return BacktraceInfo(thread_id=thread_id, frames=frames, count=len(frames))


def frame_info_from_payload(payload: object) -> FrameInfo:
    """Build a typed current-frame payload from raw MI data."""

    frame = as_optional_mapping(as_mapping(payload).get("frame"))
    return FrameInfo(frame=_canonical_frame(frame) if frame is not None else None)


def frame_selection_info_from_payload(frame_number: int, payload: object) -> FrameSelectionInfo:
    """Build a typed frame-selection payload from raw MI data."""

    frame = as_optional_mapping(as_mapping(payload).get("frame"))
    return FrameSelectionInfo(
        frame_number=frame_number,
        frame=_canonical_frame(frame) if frame is not None else None,
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
