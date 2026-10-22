"""Delve selection and backend-specific startup argument contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gdb_mcp import contracts as c
from gdb_mcp.mcp.schemas import StartSessionArgs

from .cli_support import SERVER_URL, invoke_cli


def test_delve_accepts_its_own_flags_and_values() -> None:
    request = StartSessionArgs(
        debugger=c.DEBUGGER_DELVE,
        debugger_args=["--backend", "native", "--log", "--log-output=rpc"],
    )
    assert request.debugger == c.DEBUGGER_DELVE
    assert request.debugger_args is not None and request.debugger_args[1] == "native"


@pytest.mark.parametrize(
    "argument",
    [
        "--headless=false",
        "--api-version=1",
        "--listen=:123",
        "-l:123",
        "--accept-multiclient",
        "--continue",
        "--init=file",
        "--wd=/tmp",
        "--log-dest=file",
        "--",
        "exec",
    ],
)
def test_delve_reserves_startup_wiring(argument: str) -> None:
    with pytest.raises(ValidationError):
        StartSessionArgs(debugger=c.DEBUGGER_DELVE, debugger_args=[argument])


def test_lldb_rejects_unconsumed_debugger_arguments() -> None:
    with pytest.raises(ValidationError, match="LLDB"):
        StartSessionArgs(debugger=c.DEBUGGER_LLDB, debugger_args=["--log"])


@pytest.mark.parametrize("command", ["start", "connect"])
def test_cli_selects_delve(command: str) -> None:
    arguments = [command, "--debugger", c.DEBUGGER_DELVE]
    if command == "connect":
        arguments += ["tcp://127.0.0.1:1234"]
    result = invoke_cli(
        ["--server-url", SERVER_URL, *arguments],
        response={"status": "success", "result": {"session_id": 1}},
    )
    assert result.exit_code == 0
    assert result.sent_payload["debugger"] == c.DEBUGGER_DELVE


def test_campaign_cli_preserves_debugger_selection() -> None:
    result = invoke_cli(
        [
            "--server-url",
            SERVER_URL,
            "campaign",
            "--startup-debugger",
            c.DEBUGGER_DELVE,
            "--startup-program",
            "/tmp/program",
        ],
        response={"status": "success"},
    )
    assert result.exit_code == 0
    assert result.sent_payload["startup"] == {
        "debugger": c.DEBUGGER_DELVE,
        "program": "/tmp/program",
    }
