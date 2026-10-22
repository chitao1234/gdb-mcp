"""Execution CLI compatibility at the request boundary."""

from __future__ import annotations

import pytest

from gdb_mcp import contracts as c

from .cli_support import SERVER_URL, invoke_cli


@pytest.mark.parametrize("action", c.EXECUTION_MANAGE_ACTIONS)
def test_execution_actions_preserve_omitted_defaults(action: c.ExecutionManageAction) -> None:
    flags: list[str] = []
    execution: dict[str, object] = {}
    if action in {c.ACTION_JUMP, c.ACTION_UNTIL}:
        flags = ["--location", "worker"]
        execution = {"location": "worker"}
    elif action == c.ACTION_WAIT_FOR_STOP:
        execution = {"timeout_sec": 30}

    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            "tool",
            c.TOOL_EXECUTION_MANAGE,
            "--session-id",
            "7",
            "--action",
            action,
            *flags,
        ]
    )

    assert result.exit_code == 0
    result.assert_invoked(
        c.TOOL_EXECUTION_MANAGE,
        {"session_id": 7, "action": action, "execution": execution},
    )


@pytest.mark.parametrize(
    ("action", "flags", "invalid_flag"),
    [
        (c.ACTION_RUN, ["--reverse"], "--reverse"),
        (c.ACTION_STEP, ["--arg", "value"], "--arg"),
        (c.ACTION_INTERRUPT, ["--timeout-sec", "1"], "--timeout-sec"),
        (c.ACTION_RETURN, ["--wait-until", "stop"], "--wait-until"),
        (c.ACTION_WAIT_FOR_STOP, ["--reverse"], "--reverse"),
        (c.ACTION_CONTINUE, ["--stop-reason", "breakpoint-hit"], "--stop-reason"),
        (c.ACTION_FINISH, ["--value", "0"], "--value"),
        (c.ACTION_NEXT, ["--location", "worker"], "--location"),
        (c.ACTION_JUMP, ["--location", "worker", "--reverse"], "--reverse"),
    ],
)
def test_execution_actions_keep_cli_flag_errors(
    action: c.ExecutionManageAction, flags: list[str], invalid_flag: str
) -> None:
    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            "tool",
            c.TOOL_EXECUTION_MANAGE,
            "--session-id",
            "7",
            "--action",
            action,
            *flags,
        ],
        expect_exit=True,
    )

    assert result.exit_code == 2
    assert invalid_flag in result.stderr
    result.invoke.assert_not_awaited()


def test_raw_execution_request_preserves_explicit_false() -> None:
    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            c.TOOL_EXECUTION_MANAGE,
            "--payload-json",
            '{"session_id":7,"action":"step","execution":{"reverse":false}}',
        ]
    )

    assert result.exit_code == 0
    result.assert_invoked(
        c.TOOL_EXECUTION_MANAGE,
        {"session_id": 7, "action": c.ACTION_STEP, "execution": {"reverse": False}},
    )
