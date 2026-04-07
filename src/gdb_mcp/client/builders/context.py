"""Pure payload builders for context-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import ContextManageInput, ContextQueryInput


def build_context_query_payload(typed_input: ContextQueryInput) -> dict[str, object]:
    """Build the raw context-query payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }
    query: dict[str, object] = {}

    if typed_input.action == "backtrace":
        if typed_input.thread_id is not None:
            query["thread_id"] = typed_input.thread_id
        if typed_input.max_frames is not None:
            query["max_frames"] = typed_input.max_frames
    elif typed_input.action == "frame":
        if typed_input.thread_id is not None:
            query["thread_id"] = typed_input.thread_id
        if typed_input.frame is not None:
            query["frame"] = typed_input.frame

    if query:
        payload["query"] = query
    return payload


def build_context_manage_payload(typed_input: ContextManageInput) -> dict[str, object]:
    """Build the raw context-manage payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }

    if typed_input.action == "select_thread":
        payload["context"] = (
            {"thread_id": typed_input.thread_id} if typed_input.thread_id is not None else {}
        )
    elif typed_input.action == "select_frame":
        payload["context"] = {"frame": typed_input.frame} if typed_input.frame is not None else {}

    return payload
