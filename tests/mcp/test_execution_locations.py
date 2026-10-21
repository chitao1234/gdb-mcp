"""Location execution inputs and CLI forwarding, without debugger policy checks."""

from __future__ import annotations

import jsonschema
import pytest
from pydantic import ValidationError

from gdb_mcp.contracts import ACTION_JUMP, ACTION_UNTIL, TOOL_EXECUTION_MANAGE
from gdb_mcp.mcp.schemas import (
    ExecutionJumpAction,
    ExecutionManageArgs,
    ExecutionUntilAction,
    build_tool_definitions,
)
from tests.support import mapping

from .cli_support import SERVER_URL, invoke_cli


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
@pytest.mark.parametrize(
    "location",
    [
        "0",
        "-2",
        "missing.c:999999",
        "*$pc + 4",
        "not_a_symbol",
        "  worker  ",
    ],
)
def test_location_semantics_are_left_to_the_debugger(action: str, location: str) -> None:
    args = ExecutionManageArgs.model_validate(
        {"session_id": 7, "action": action, "execution": {"location": location}}
    ).root

    assert isinstance(args, ExecutionJumpAction | ExecutionUntilAction)
    assert args.execution.location == location.strip()
    assert args.execution.wait_until == "stop"


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
@pytest.mark.parametrize(
    "execution",
    [
        {},
        {"location": {"kind": "file_line", "file": "main.c", "line": 10}},
        {"location": "10", "reverse": True},
        {"location": ""},
        {"location": "   "},
    ],
)
def test_location_execution_still_requires_the_declared_input_shape(
    action: str, execution: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        ExecutionManageArgs.model_validate(
            {"session_id": 7, "action": action, "execution": execution}
        )


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
@pytest.mark.parametrize(
    ("flags", "location"),
    [
        (["42"], "42"),
        (
            ["--location", "a b.c:42"],
            "a b.c:42",
        ),
        (
            ["--location", "*$pc + 4"],
            "*$pc + 4",
        ),
        (["worker"], "worker"),
    ],
)
def test_cli_forwards_location_and_wait_policy(
    action: str, flags: list[str], location: str
) -> None:
    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            action,
            "--session-id",
            "7",
            *flags,
            "--wait-until",
            "acknowledged",
            "--timeout-sec",
            "5",
        ]
    )

    assert result.exit_code == 0
    result.assert_invoked(
        TOOL_EXECUTION_MANAGE,
        {
            "session_id": 7,
            "action": action,
            "execution": {"location": location, "wait_until": "acknowledged", "timeout_sec": 5},
        },
    )


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
def test_cli_rejects_removed_structured_location_flags(action: str) -> None:
    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            action,
            "--session-id",
            "7",
            "--location-kind",
            "line",
            "--line",
            "60",
        ],
        expect_exit=True,
    )

    assert result.exit_code == 2
    result.invoke.assert_not_awaited()


@pytest.mark.parametrize("action", [ACTION_JUMP, ACTION_UNTIL])
def test_location_execution_output_schema_accepts_running_without_a_stop(action: str) -> None:
    tool = next(tool for tool in build_tool_definitions() if tool.name == TOOL_EXECUTION_MANAGE)
    assert tool.outputSchema is not None
    jsonschema.validate(
        {
            "status": "success",
            "action": action,
            "result": {
                "command": action,
                "location": "*$pc + 4",
                "execution_state": "running",
                "stop_reason": None,
                "stop_kind": None,
                "last_stop_event": None,
                "backend_data": {"debugger": "gdb", "console_output": "", "data": {}},
            },
        },
        mapping(tool.outputSchema),
    )
