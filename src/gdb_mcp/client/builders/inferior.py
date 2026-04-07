"""Pure payload builders for inferior-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import InferiorManageInput, InferiorQueryInput


def build_inferior_query_payload(typed_input: InferiorQueryInput) -> dict[str, object]:
    """Build the raw inferior-query payload from typed input."""

    return {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }


def build_inferior_manage_payload(typed_input: InferiorManageInput) -> dict[str, object]:
    """Build the raw inferior-manage payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }
    inferior: dict[str, object] = {}

    if typed_input.action == "create":
        if typed_input.executable is not None:
            inferior["executable"] = typed_input.executable
        if typed_input.make_current is not None:
            inferior["make_current"] = typed_input.make_current
    elif typed_input.action in {"remove", "select"}:
        if typed_input.inferior_id is not None:
            inferior["inferior_id"] = typed_input.inferior_id
    elif typed_input.action == "set_follow_fork_mode":
        if typed_input.mode is not None:
            inferior["mode"] = typed_input.mode
    elif typed_input.action == "set_detach_on_fork":
        inferior["enabled"] = True if typed_input.enabled is None else typed_input.enabled

    payload["inferior"] = inferior
    return payload
