"""Pure payload builders for execution-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import ExecutionManageInput


def build_execution_manage_payload(typed_input: ExecutionManageInput) -> dict[str, object]:
    """Build the raw execution-manage payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }

    if typed_input.action == "interrupt":
        payload["execution"] = {}
        return payload

    if typed_input.action == "wait_for_stop":
        wait_for_stop_payload: dict[str, object] = {
            "timeout_sec": 30 if typed_input.timeout_sec is None else typed_input.timeout_sec
        }
        if typed_input.stop_reasons:
            wait_for_stop_payload["stop_reasons"] = list(typed_input.stop_reasons)
        payload["execution"] = wait_for_stop_payload
        return payload

    execution_payload: dict[str, object] = {}
    if typed_input.action == "run" and typed_input.args:
        execution_payload["args"] = list(typed_input.args)
    if typed_input.wait_until is not None:
        execution_payload["wait_until"] = typed_input.wait_until
    if typed_input.timeout_sec is not None:
        execution_payload["timeout_sec"] = typed_input.timeout_sec

    payload["execution"] = execution_payload
    return payload
