"""Pure payload builders for breakpoint-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import (
    BreakpointCreateInput,
    BreakpointManageInput,
    BreakpointQueryInput,
)


def _build_breakpoint_create_payload(typed_input: BreakpointCreateInput) -> dict[str, object]:
    payload: dict[str, object] = {"kind": typed_input.kind}
    if typed_input.kind == "code":
        if typed_input.location is not None:
            payload["location"] = typed_input.location
        if typed_input.condition is not None:
            payload["condition"] = typed_input.condition
        if typed_input.temporary:
            payload["temporary"] = True
        return payload

    if typed_input.kind == "watch":
        if typed_input.expression is not None:
            payload["expression"] = typed_input.expression
        if typed_input.access is not None:
            payload["access"] = typed_input.access
        return payload

    if typed_input.event is not None:
        payload["event"] = typed_input.event
    if typed_input.argument is not None:
        payload["argument"] = typed_input.argument
    if typed_input.temporary:
        payload["temporary"] = True
    return payload


def build_breakpoint_query_payload(typed_input: BreakpointQueryInput) -> dict[str, object]:
    """Build the raw breakpoint-query payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }
    query: dict[str, object] = {}
    if typed_input.action == "list":
        if typed_input.kinds:
            query["kinds"] = list(typed_input.kinds)
        if typed_input.enabled is not None:
            query["enabled"] = typed_input.enabled
    elif typed_input.number is not None:
        query["number"] = typed_input.number

    if query:
        payload["query"] = query
    return payload


def build_breakpoint_manage_payload(typed_input: BreakpointManageInput) -> dict[str, object]:
    """Build the raw breakpoint-manage payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }

    if typed_input.action == "create":
        if typed_input.breakpoint is not None:
            payload["breakpoint"] = _build_breakpoint_create_payload(typed_input.breakpoint)
        return payload

    if typed_input.number is not None:
        payload["breakpoint"] = {"number": typed_input.number}

    if typed_input.action == "update":
        changes: dict[str, object] = {}
        if typed_input.condition is not None:
            changes["condition"] = typed_input.condition
        if typed_input.clear_condition is not None:
            changes["clear_condition"] = typed_input.clear_condition
        payload["changes"] = changes

    return payload
