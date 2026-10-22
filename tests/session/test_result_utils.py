"""Tests for normalization at the session command-result boundary."""

from __future__ import annotations

import pytest

from gdb_mcp.domain import (
    OperationSuccess,
    StructuredPayload,
)
from gdb_mcp.session.gdb_command import GdbCommandResult
from gdb_mcp.session.result_utils import command_result_mapping
from gdb_mcp.transport.mi_parser import parse_mi_responses


def _command_result(data: StructuredPayload) -> OperationSuccess[GdbCommandResult]:
    return OperationSuccess(
        GdbCommandResult(
            "-thread-info",
            parse_mi_responses(
                [{"type": "result", "message": "done", "payload": data.get("result")}]
            ),
        )
    )


@pytest.mark.parametrize("data", [{}, {"result": None}])
def test_missing_mi_result_becomes_one_empty_mapping_at_session_boundary(
    data: StructuredPayload,
) -> None:
    assert command_result_mapping(_command_result(data)) == {}


def test_mi_result_mapping_is_passed_through_without_key_normalization() -> None:
    payload: StructuredPayload = {
        "thread-id": "2",
        "future-field": {"nested-key": "kept"},
    }

    assert command_result_mapping(_command_result({"result": payload})) is payload
