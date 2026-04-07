"""Pure payload builders for inspect-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import InspectQueryInput, LocationInput


def _build_context_payload(typed_input: InspectQueryInput) -> dict[str, object] | None:
    payload: dict[str, object] = {}
    if typed_input.thread_id is not None:
        payload["thread_id"] = typed_input.thread_id
    if typed_input.frame is not None:
        payload["frame"] = typed_input.frame
    return payload or None


def _build_location_payload(location: LocationInput) -> dict[str, object]:
    if location.kind == "current":
        return {"kind": "current"}
    if location.kind == "function":
        return {"kind": "function", "function": location.function}
    if location.kind == "address":
        return {"kind": "address", "address": location.address}
    if location.kind == "address_range":
        return {
            "kind": "address_range",
            "start_address": location.start_address,
            "end_address": location.end_address,
        }
    if location.kind == "file_line":
        return {"kind": "file_line", "file": location.file, "line": location.line}
    return {
        "kind": "file_range",
        "file": location.file,
        "start_line": location.start_line,
        "end_line": location.end_line,
    }


def build_inspect_query_payload(typed_input: InspectQueryInput) -> dict[str, object]:
    """Build the raw inspect-query payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }
    query: dict[str, object] = {}
    context = _build_context_payload(typed_input)

    if typed_input.action == "evaluate":
        if context is not None:
            query["context"] = context
        if typed_input.expression is not None:
            query["expression"] = typed_input.expression
    elif typed_input.action == "variables":
        if context is not None:
            query["context"] = context
    elif typed_input.action == "registers":
        if context is not None:
            query["context"] = context
        if typed_input.register_numbers:
            query["register_numbers"] = list(typed_input.register_numbers)
        if typed_input.register_names:
            query["register_names"] = list(typed_input.register_names)
        if typed_input.include_vector_registers is not None:
            query["include_vector_registers"] = typed_input.include_vector_registers
        if typed_input.max_registers is not None:
            query["max_registers"] = typed_input.max_registers
        if typed_input.value_format is not None:
            query["value_format"] = typed_input.value_format
    elif typed_input.action == "memory":
        if typed_input.memory_address is not None:
            query["address"] = typed_input.memory_address
        if typed_input.count is not None:
            query["count"] = typed_input.count
        if typed_input.offset is not None:
            query["offset"] = typed_input.offset
    elif typed_input.action == "disassembly":
        if context is not None:
            query["context"] = context
        if typed_input.location is not None:
            query["location"] = _build_location_payload(typed_input.location)
        if typed_input.instruction_count is not None:
            query["instruction_count"] = typed_input.instruction_count
        if typed_input.mode is not None:
            query["mode"] = typed_input.mode
    elif typed_input.action == "source":
        if context is not None:
            query["context"] = context
        if typed_input.location is not None:
            query["location"] = _build_location_payload(typed_input.location)
        if typed_input.context_before is not None:
            query["context_before"] = typed_input.context_before
        if typed_input.context_after is not None:
            query["context_after"] = typed_input.context_after

    if query:
        payload["query"] = query
    return payload
