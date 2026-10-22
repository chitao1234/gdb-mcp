"""Internal MI command results and their public conversion."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.session.gdb_command import GdbCommandResult, command_output
from gdb_mcp.transport import ParsedMiResponse

from .conftest import MiRecordBuilder, ScriptedSessionBuilder


def test_inspection_consumes_parsed_mi_without_serializing_the_command(
    monkeypatch: pytest.MonkeyPatch,
    scripted_running_session: ScriptedSessionBuilder,
    mi_result: MiRecordBuilder,
) -> None:
    native = {
        "id": "1",
        "target-id": "native",
        "target_id": "distinct",
        "future-key": {"dash-key": 0},
    }
    session, _ = scripted_running_session([mi_result({"threads": [native]})])
    serialize = Mock(side_effect=AssertionError("internal command must remain typed"))
    monkeypatch.setattr("gdb_mcp.session.gdb_command.payload_to_mapping", serialize)

    result = session.get_threads()

    assert isinstance(result, OperationSuccess)
    assert result.value.threads[0].to_payload() == native
    serialize.assert_not_called()


def test_public_command_conversion_preserves_fields_warnings_and_action() -> None:
    payload = {"register-values": [{"number": "0", "value": "0x0", "future-key": None}]}
    parsed = ParsedMiResponse(console=["console\n"], result=payload, result_class="future-class")
    result = command_output(
        OperationSuccess(GdbCommandResult("command", parsed), warnings=("warning",), action="run")
    )

    assert isinstance(result, OperationSuccess)
    assert result.warnings == ("warning",)
    assert result.action == "run"
    assert result.value.backend_data.console_output == "console\n"
    assert result.value.backend_data.data == {
        "result": payload,
        "result_class": "future-class",
        "notify": [],
        "log": [],
        "output": [],
    }


def test_public_command_conversion_keeps_expected_errors() -> None:
    error = OperationError("future debugger failure", code="error", fatal=True)

    assert command_output(error) is error
