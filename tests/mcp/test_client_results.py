"""Client envelope interpretation preserves the response payload."""

from __future__ import annotations

import pytest

from gdb_mcp.client.results import ToolEnvelope
from gdb_mcp.contracts import ACTION_LIST, ACTION_STATUS
from gdb_mcp.domain import ToolResultStatus


def test_success_envelope_reads_nested_fields_and_keeps_original_result() -> None:
    result: dict[str, object] = {
        "session_id": 7,
        "target_loaded": False,
        "execution_state": "not_started",
        "message": "started",
        "action": "future-action",
        "future-field": {"nested-key": "kept"},
        "future_field": "distinct",
    }
    payload = {
        "status": "success",
        "action": ACTION_STATUS,
        "session_id": 99,
        "message": "outer message",
        "result": result,
    }

    envelope = ToolEnvelope.from_payload(payload)

    assert envelope.status == "success"
    assert envelope.action == ACTION_STATUS
    assert envelope.session_id == 7
    assert envelope.target_loaded is False
    assert envelope.execution_state == "not_started"
    assert envelope.message == "started"
    assert envelope.result is result
    assert envelope.result["future-field"] == {"nested-key": "kept"}
    assert envelope.result["future_field"] == "distinct"


def test_error_envelope_has_no_success_fields_and_keeps_the_error_message() -> None:
    payload: dict[str, object] = {
        "status": "error",
        "code": "future_error",
        "action": ACTION_LIST,
        "message": "unfamiliar debugger failure",
        "fatal": False,
        "details": {"future-field": {"nested-key": "kept"}},
    }

    envelope = ToolEnvelope.from_payload(payload)

    assert envelope.code == "future_error"
    assert envelope.message == "unfamiliar debugger failure"
    assert envelope.fatal is False
    assert envelope.result == {}
    assert envelope.session_id is None
    assert envelope.target_loaded is None
    assert envelope.execution_state is None
    assert envelope.is_list_action()
    assert "result" not in payload
    assert payload["details"] == {"future-field": {"nested-key": "kept"}}


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], False),
        (["success", "success"], False),
        (["success", "error"], True),
        (["error", "success"], True),
    ],
)
def test_workflow_step_errors_are_read_without_changing_step_payloads(
    statuses: list[ToolResultStatus], expected: bool
) -> None:
    steps = [
        {
            "result": {
                "status": status,
                "future-field": {"nested-key": "kept"},
                "future_field": "distinct",
            }
        }
        for status in statuses
    ]
    result = {"steps": steps}
    envelope = ToolEnvelope.from_payload({"status": "success", "result": result})

    assert envelope.has_step_errors() is expected
    assert envelope.result is result
    assert envelope.result["steps"] is steps
    for step in steps:
        assert step["result"]["future-field"] == {"nested-key": "kept"}
        assert step["result"]["future_field"] == "distinct"
