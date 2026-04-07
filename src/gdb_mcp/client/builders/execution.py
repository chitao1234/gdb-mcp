"""Pure payload builders for execution-oriented CLI commands."""

from __future__ import annotations

from gdb_mcp.client.inputs import ExecutionManageInput, ExecutionWaitInput


def _build_execution_wait_payload(
    typed_input: ExecutionWaitInput | None,
) -> dict[str, object] | None:
    if typed_input is None:
        return None

    payload: dict[str, object] = {}
    if typed_input.until is not None:
        payload["until"] = typed_input.until
    if typed_input.timeout_sec is not None:
        payload["timeout_sec"] = typed_input.timeout_sec
    return payload or None


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

    wait_payload = _build_execution_wait_payload(typed_input.wait)
    if wait_payload is not None:
        execution_payload["wait"] = wait_payload

    payload["execution"] = execution_payload
    return payload
