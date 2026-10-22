"""Tests for normalization at the session command-result boundary."""

from __future__ import annotations

from gdb_mcp.domain import (
    BackendData,
    CommandExecutionInfo,
    OperationSuccess,
    StructuredPayload,
)
from gdb_mcp.session.result_utils import command_result_mapping


def _command_result(data: StructuredPayload) -> OperationSuccess[CommandExecutionInfo]:
    return OperationSuccess(
        CommandExecutionInfo(
            command="-thread-info",
            backend_data=BackendData(debugger="gdb", console_output="", data=data),
        )
    )


def test_missing_mi_result_becomes_one_empty_mapping_at_session_boundary() -> None:
    assert command_result_mapping(_command_result({})) == {}


def test_mi_result_mapping_is_passed_through_without_key_normalization() -> None:
    payload: StructuredPayload = {
        "thread-id": "2",
        "future-field": {"nested-key": "kept"},
    }

    assert command_result_mapping(_command_result({"result": payload})) is payload
