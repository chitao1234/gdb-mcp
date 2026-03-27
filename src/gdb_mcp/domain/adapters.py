"""Helpers for converting raw GDB/MI payloads into typed domain records."""

from __future__ import annotations

from typing import cast

from .models import (
    BacktraceInfo,
    BreakpointListInfo,
    BreakpointRecord,
    FrameRecord,
    FrameInfo,
    FrameSelectionInfo,
    RegisterRecord,
    RegistersInfo,
    StructuredPayload,
    ThreadListInfo,
    ThreadRecord,
    ThreadSelectionInfo,
    VariableRecord,
    VariablesInfo,
)


def payload_mapping(payload: object) -> StructuredPayload:
    """Return a mapping payload or an empty mapping for malformed inputs."""

    return cast(StructuredPayload, payload) if isinstance(payload, dict) else {}


def string_field(payload: object, key: str) -> str | None:
    """Return a string field from a raw payload mapping when available."""

    value = payload_mapping(payload).get(key)
    return value if isinstance(value, str) else None


def frame_record(payload: object) -> FrameRecord:
    """Normalize a raw frame payload."""

    return cast(FrameRecord, payload_mapping(payload))


def thread_records(payload: object, key: str = "threads") -> list[ThreadRecord]:
    """Normalize a thread-record list from a raw payload mapping."""

    value = payload_mapping(payload).get(key, [])
    return cast(list[ThreadRecord], value) if isinstance(value, list) else []


def frame_records(payload: object, key: str = "stack") -> list[FrameRecord]:
    """Normalize a frame-record list from a raw payload mapping."""

    value = payload_mapping(payload).get(key, [])
    return cast(list[FrameRecord], value) if isinstance(value, list) else []


def breakpoint_record(payload: object) -> BreakpointRecord:
    """Normalize a single breakpoint payload."""

    raw_mapping = payload_mapping(payload)
    raw_breakpoint = raw_mapping.get("bkpt", raw_mapping)
    return cast(BreakpointRecord, raw_breakpoint) if isinstance(raw_breakpoint, dict) else {}


def breakpoint_records(payload: object) -> list[BreakpointRecord]:
    """Normalize a list of breakpoint payloads."""

    breakpoint_table = payload_mapping(payload).get("BreakpointTable", {})
    if not isinstance(breakpoint_table, dict):
        return []

    body = breakpoint_table.get("body", [])
    return cast(list[BreakpointRecord], body) if isinstance(body, list) else []


def variable_records(payload: object) -> list[VariableRecord]:
    """Normalize a list of variable payloads."""

    value = payload_mapping(payload).get("variables", [])
    return cast(list[VariableRecord], value) if isinstance(value, list) else []


def register_records(payload: object) -> list[RegisterRecord]:
    """Normalize a list of register payloads."""

    value = payload_mapping(payload).get("register-values", [])
    return cast(list[RegisterRecord], value) if isinstance(value, list) else []


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
        frame=frame_record(payload_mapping(payload).get("frame")),
    )


def backtrace_info_from_payload(thread_id: int | None, payload: object) -> BacktraceInfo:
    """Build a typed backtrace payload from raw MI data."""

    frames = frame_records(payload)
    return BacktraceInfo(thread_id=thread_id, frames=frames, count=len(frames))


def frame_info_from_payload(payload: object) -> FrameInfo:
    """Build a typed current-frame payload from raw MI data."""

    return FrameInfo(frame=frame_record(payload_mapping(payload).get("frame")))


def frame_selection_info_from_payload(frame_number: int, payload: object) -> FrameSelectionInfo:
    """Build a typed frame-selection payload from raw MI data."""

    return FrameSelectionInfo(
        frame_number=frame_number,
        frame=frame_record(payload_mapping(payload).get("frame")),
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
