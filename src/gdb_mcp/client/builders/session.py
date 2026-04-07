"""Pure payload builders for session-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import SessionQueryInput, SessionStartInput


def build_session_start_payload(typed_input: SessionStartInput) -> dict[str, object]:
    """Build the raw session-start payload from typed input."""

    payload: dict[str, object] = {}
    if typed_input.program is not None:
        payload["program"] = typed_input.program
    if typed_input.args:
        payload["args"] = list(typed_input.args)
    if typed_input.init_commands:
        payload["init_commands"] = list(typed_input.init_commands)
    if typed_input.env:
        payload["env"] = typed_input.env
    if typed_input.core is not None:
        payload["core"] = typed_input.core
    if typed_input.gdb_path is not None:
        payload["gdb_path"] = typed_input.gdb_path
    if typed_input.working_dir is not None:
        payload["working_dir"] = typed_input.working_dir
    return payload


def build_session_query_payload(typed_input: SessionQueryInput) -> dict[str, object]:
    """Build the raw session-query payload from typed input."""

    payload: dict[str, object] = {"action": typed_input.action}
    if typed_input.session_id is not None:
        payload["session_id"] = typed_input.session_id
    return payload
