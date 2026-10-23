"""Public routing and CLI coverage for Go inspection options."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock

import jsonschema
import pytest
from pydantic import ValidationError

from gdb_mcp import contracts as c
from gdb_mcp.domain import (
    BacktraceInfo,
    CodeLocationRecord,
    DeferredCallRecord,
    FrameRecord,
    OperationSuccess,
    ThreadListInfo,
    VariablesInfo,
    result_to_envelope,
)
from gdb_mcp.mcp.handlers import dispatch_tool_call
from gdb_mcp.mcp.schemas import ContextQueryArgs, InspectQueryArgs
from gdb_mcp.mcp.schemas.outputs import public_output_schema
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.requests import ThreadQuery

from .cli_support import SERVER_URL, invoke_cli


def test_go_results_match_the_published_schema() -> None:
    frame = FrameRecord(
        level=2,
        defers=[
            DeferredCallRecord(
                index=1,
                function=CodeLocationRecord(func="main.cleanup", addr="0x10"),
                registered_at=CodeLocationRecord(file="main.go", line=20),
            )
        ],
    )
    result = OperationSuccess(BacktraceInfo(7, [frame]), action=c.ACTION_BACKTRACE)
    jsonschema.validate(result_to_envelope(result), public_output_schema(c.TOOL_CONTEXT_QUERY))
    threads = OperationSuccess(ThreadListInfo([], "7", truncated=True), action=c.ACTION_THREADS)
    jsonschema.validate(result_to_envelope(threads), public_output_schema(c.TOOL_CONTEXT_QUERY))


@pytest.mark.parametrize(
    "query",
    [
        {"waiting_on_channel": " "},
        {"max_results": 0},
        {"context": {"frame": 1}},
    ],
)
def test_invalid_thread_queries_are_validation_errors(query: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ContextQueryArgs.model_validate(
            {"session_id": 1, "action": c.ACTION_THREADS, "query": query}
        )


@pytest.mark.parametrize("action", [c.ACTION_EVALUATE, c.ACTION_VARIABLES, c.ACTION_REGISTERS])
def test_deferred_scope_validation(action: str) -> None:
    query: dict[str, object] = {"context": {"deferred_call": 0}}
    if action == c.ACTION_EVALUATE:
        query["expression"] = "a"
    with pytest.raises(ValidationError):
        InspectQueryArgs.model_validate({"session_id": 1, "action": action, "query": query})
    query["context"] = {"deferred_call": 1}
    if action == c.ACTION_REGISTERS:
        with pytest.raises(ValidationError):
            InspectQueryArgs.model_validate({"session_id": 1, "action": action, "query": query})
    else:
        InspectQueryArgs.model_validate({"session_id": 1, "action": action, "query": query})


def test_go_options_reach_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    session = create_default_session_service(c.DEBUGGER_DELVE)
    registry = SessionRegistry()
    registry._sessions[1] = session
    threads = Mock(return_value=OperationSuccess(ThreadListInfo([], None)))
    backtrace = Mock(return_value=OperationSuccess(BacktraceInfo(7, [])))
    variables = Mock(return_value=OperationSuccess(VariablesInfo(7, 2, [])))
    monkeypatch.setattr(session, "get_threads", threads)
    monkeypatch.setattr(session, "get_backtrace", backtrace)
    monkeypatch.setattr(session, "get_variables", variables)

    async def check() -> None:
        for tool, action, query in (
            (
                c.TOOL_CONTEXT_QUERY,
                c.ACTION_THREADS,
                {
                    "waiting_on_channel": "jobs",
                    "context": {"thread_id": 7, "frame": 2},
                    "max_results": 20,
                },
            ),
            (c.TOOL_CONTEXT_QUERY, c.ACTION_BACKTRACE, {"thread_id": 7, "include_defers": True}),
            (
                c.TOOL_INSPECT_QUERY,
                c.ACTION_VARIABLES,
                {"context": {"thread_id": 7, "frame": 2, "deferred_call": 1}},
            ),
        ):
            result = await dispatch_tool_call(
                tool,
                {"session_id": 1, "action": action, "query": query},
                registry,
                logger=logging.getLogger(__name__),
            )
            assert not result.isError

    asyncio.run(check())
    threads.assert_called_once_with(ThreadQuery("jobs", 7, 2, 20))
    backtrace.assert_called_once_with(thread_id=7, max_frames=100, include_defers=True)
    variables.assert_called_once_with(thread_id=7, frame=2, deferred_call=1)


@pytest.mark.parametrize(
    ("command", "options", "action", "query"),
    [
        (
            "threads",
            [
                "--waiting-on-channel",
                "jobs",
                "--thread-id",
                "7",
                "--frame",
                "2",
                "--max-results",
                "10",
            ],
            c.ACTION_THREADS,
            {
                "waiting_on_channel": "jobs",
                "context": {"thread_id": 7, "frame": 2},
                "max_results": 10,
            },
        ),
        ("bt", ["--include-defers"], c.ACTION_BACKTRACE, {"include_defers": True}),
        (
            "locals",
            ["--deferred-call", "1", "--frame", "2"],
            c.ACTION_VARIABLES,
            {"context": {"frame": 2, "deferred_call": 1}},
        ),
    ],
)
def test_cli_go_inspection_payloads(
    command: str, options: list[str], action: str, query: dict[str, object]
) -> None:
    result = invoke_cli(["--server-url", SERVER_URL, command, "--session-id", "1", *options])
    assert result.exit_code == 0, result.stderr
    assert result.sent_payload == {"session_id": 1, "action": action, "query": query}


@pytest.mark.parametrize(
    "options",
    [
        ["threads", "--session-id", "1", "--include-defers"],
        ["bt", "--session-id", "1", "--waiting-on-channel", "jobs"],
        ["regs", "--session-id", "1", "--deferred-call", "1"],
    ],
)
def test_cli_rejects_go_flags_on_other_actions(options: list[str]) -> None:
    result = invoke_cli(["--server-url", SERVER_URL, *options], expect_exit=True)
    assert result.exit_code == 2
    result.invoke.assert_not_awaited()
