"""Helpers for converting raw GDB/MI payloads into typed domain records.

Every record that leaves this module is a :class:`~gdb_mcp.domain.records.RecordModel`,
built from the debugger's own payload. The payloads here are the mappings the
transport already narrowed; ``None`` means the command reported nothing and is
read as an empty payload. The adapters convert the spellings MI uses for flags
and counters (``"y"``, ``"3"``) into the types this server publishes, so
:meth:`RecordModel.from_payload` validates a record instead of accepting
whatever arrived.
"""

from __future__ import annotations

from collections.abc import Mapping

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


def _flag(value: object) -> bool:
    """Read an MI flag, which MI spells ``y``/``n``.

    MI only ever emits those two, so anything else means GDB and this server
    disagree about the payload. That is raised rather than read as ``False``,
    which would turn a malformed breakpoint into a silently disabled one.
    """

    text = as_text(value)
    if text == "y":
        return True
    if text == "n":
        return False
    raise ValueError(f"MI flag must be 'y' or 'n', got {value!r}")


def _count(value: object) -> int:
    """Read an MI count, which MI spells as a decimal string.

    A value that is not a number is a GDB/MI contract violation and is raised
    here on purpose: silently recording ``None`` would hide the anomaly that the
    declared type exists to catch.
    """

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = as_text(value)
    if text is None:
        raise ValueError(f"MI count must be a number, got {type(value).__name__}")
    return int(text)


def _canonical_breakpoint(payload: Mapping[str, object]) -> BreakpointRecord:
    """Build a breakpoint record with canonical locations and watchpoint data.

    The published contract replaced the old flat ``addr``/``func``/``file``/
    ``line`` fields with a ``locations`` list. GDB/MI still reports those fields
    directly for single-location breakpoints and under ``locations`` for
    multi-location breakpoints, so both forms are folded into the new shape here.
    """

    normalized = dict(payload)
    if "enabled" in normalized:
        normalized["enabled"] = _flag(normalized["enabled"])
    if "times" in normalized:
        normalized["times"] = _count(normalized["times"])

    location_keys = ("addr", "func", "file", "fullname", "line", "column")
    direct_location: dict[str, object] = {}
    for key in location_keys:
        value = normalized.pop(key, None)
        if value is not None:
            direct_location[key] = value

    raw_locations = normalized.pop("locations", None)
    locations: list[dict[str, object]] = []
    if isinstance(raw_locations, list) and raw_locations:
        for item in raw_locations:
            if not isinstance(item, Mapping):
                continue
            location = dict(item.get("bkpt", item))
            if "line" in location:
                location["line"] = _optional_count(location["line"])
            if "column" in location:
                location["column"] = _optional_count(location["column"])
            location.setdefault("resolved", _location_is_resolved(location))
            locations.append(location)
    elif direct_location:
        direct_location["line"] = _optional_count(direct_location.get("line"))
        direct_location["column"] = _optional_count(direct_location.get("column"))
        direct_location["resolved"] = _location_is_resolved(direct_location)
        locations.append(direct_location)

    normalized["locations"] = locations

    breakpoint_type = str(normalized.get("type") or "").lower()
    what = normalized.pop("what", None)
    if "watch" in breakpoint_type:
        address = locations[0].get("addr") if locations else None
        size = _optional_count(normalized.pop("size", None))
        normalized["watch"] = {
            "access": _watchpoint_access(breakpoint_type),
            "address": address,
            "size": size,
            "spec": what or normalized.get("original-location"),
        }

    return BreakpointRecord.from_payload(normalized)


def _optional_count(value: object) -> int | None:
    """Read a numeric MI field that may be missing."""

    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = as_text(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def _location_is_resolved(location: Mapping[str, object]) -> bool:
    """Whether a breakpoint location points at a real address."""

    address = as_text(location.get("addr"))
    return bool(address) and address != "<PENDING>"


def _watchpoint_access(breakpoint_type: str) -> str:
    """Map GDB's watchpoint type spelling to the published access mode."""

    if "read" in breakpoint_type:
        return "read"
    if "access" in breakpoint_type or "acc" in breakpoint_type:
        return "access"
    return "write"


def _canonical_frame(payload: Mapping[str, object]) -> FrameRecord:
    """Build a frame record with a canonical integer ``level``."""

    normalized = dict(payload)
    if "level" in normalized:
        normalized["level"] = _count(normalized["level"])
    return FrameRecord.from_payload(normalized)


def _canonical_register(payload: Mapping[str, object]) -> RegisterRecord:
    """Build a register record with a canonical integer ``number``.

    The number indexes the register-name list, so it has to be an integer by the
    time a record carries it.
    """

    normalized = dict(payload)
    if "number" in normalized:
        normalized["number"] = _count(normalized["number"])
    return RegisterRecord.from_payload(normalized)


def frame_record(payload: Mapping[str, object] | None) -> FrameRecord:
    """Build one frame record with the canonical ``level``."""

    return _canonical_frame(as_mapping(payload))


def string_field(payload: Mapping[str, object] | None, key: str) -> str | None:
    """Return a string field from a raw payload mapping when available."""

    return as_text(as_mapping(payload).get(key))


def thread_records(
    payload: Mapping[str, object] | None, key: str = "threads"
) -> list[ThreadRecord]:
    """Build thread records from a raw payload mapping."""

    return [
        ThreadRecord.from_payload(record) for record in as_record_list(as_mapping(payload).get(key))
    ]


def frame_records(payload: Mapping[str, object] | None, key: str = "stack") -> list[FrameRecord]:
    """Build frame records from a raw payload mapping."""

    return [_canonical_frame(record) for record in as_record_list(as_mapping(payload).get(key))]


def breakpoint_record(payload: Mapping[str, object] | None) -> BreakpointRecord:
    """Build a single breakpoint record."""

    raw_mapping = as_mapping(payload)
    return _canonical_breakpoint(as_mapping(raw_mapping.get("bkpt", raw_mapping)))


def breakpoint_records(payload: Mapping[str, object] | None) -> list[BreakpointRecord]:
    """Build a list of breakpoint records."""

    breakpoint_table = as_mapping(as_mapping(payload).get("BreakpointTable"))
    return [
        _canonical_breakpoint(record) for record in as_record_list(breakpoint_table.get("body"))
    ]


def variable_records(payload: Mapping[str, object] | None) -> list[VariableRecord]:
    """Build a list of variable records."""

    return [
        VariableRecord.from_payload(record)
        for record in as_record_list(as_mapping(payload).get("variables"))
    ]


def register_records(payload: Mapping[str, object] | None) -> list[RegisterRecord]:
    """Build a list of register records."""

    return [
        _canonical_register(record)
        for record in as_record_list(as_mapping(payload).get("register-values"))
    ]


def memory_block_records(payload: Mapping[str, object] | None) -> list[MemoryBlockRecord]:
    """Build a list of memory blocks."""

    return [
        MemoryBlockRecord.from_payload(record)
        for record in as_record_list(as_mapping(payload).get("memory"))
    ]


def _optional_frame(payload: Mapping[str, object]) -> FrameRecord | None:
    """Build the optional frame a selection payload carries."""

    frame = as_optional_mapping(payload.get("frame"))
    return None if frame is None else _canonical_frame(frame)


def thread_list_info_from_payload(payload: Mapping[str, object] | None) -> ThreadListInfo:
    """Build a typed thread-list payload from raw MI data."""

    threads = thread_records(payload)
    return ThreadListInfo(
        threads=threads,
        current_thread_id=string_field(payload, "current-thread-id"),
        count=len(threads),
    )


def thread_selection_info_from_payload(
    thread_id: int, payload: Mapping[str, object] | None
) -> ThreadSelectionInfo:
    """Build a typed thread-selection payload from raw MI data."""

    raw_payload = as_mapping(payload)
    return ThreadSelectionInfo(
        thread_id=thread_id,
        new_thread_id=string_field(payload, "new-thread-id"),
        frame=_optional_frame(raw_payload),
    )


def backtrace_info_from_payload(
    thread_id: int | None, payload: Mapping[str, object] | None
) -> BacktraceInfo:
    """Build a typed backtrace payload from raw MI data."""

    frames = frame_records(payload)
    return BacktraceInfo(thread_id=thread_id, frames=frames, count=len(frames))


def frame_info_from_payload(payload: Mapping[str, object] | None) -> FrameInfo:
    """Build a typed current-frame payload from raw MI data."""

    return FrameInfo(frame=_optional_frame(as_mapping(payload)))


def frame_selection_info_from_payload(
    frame_number: int, payload: Mapping[str, object] | None
) -> FrameSelectionInfo:
    """Build a typed frame-selection payload from raw MI data."""

    return FrameSelectionInfo(frame_number=frame_number, frame=_optional_frame(as_mapping(payload)))


def variables_info_from_payload(
    thread_id: int | None, frame: int, payload: Mapping[str, object] | None
) -> VariablesInfo:
    """Build a typed local-variable payload from raw MI data."""

    return VariablesInfo(thread_id=thread_id, frame=frame, variables=variable_records(payload))


def registers_info_from_payload(payload: Mapping[str, object] | None) -> RegistersInfo:
    """Build a typed register payload from raw MI data."""

    return RegistersInfo(registers=register_records(payload))


def breakpoint_list_info_from_payload(payload: Mapping[str, object] | None) -> BreakpointListInfo:
    """Build a typed breakpoint-list payload from raw MI data."""

    breakpoints = breakpoint_records(payload)
    return BreakpointListInfo(breakpoints=breakpoints, count=len(breakpoints))
