"""Backend capabilities and control calls apply to direct and batch dispatch."""

from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import Mock

import pytest

from gdb_mcp import contracts as c
from gdb_mcp.domain import BackendData, CommandExecutionInfo, MessageResult, OperationSuccess
from gdb_mcp.mcp.handlers import dispatch_tool_call
from gdb_mcp.session.backends import BackendCapabilities
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry


def test_capabilities_reject_direct_and_batch_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    session = create_default_session_service()
    monkeypatch.setattr(
        session.backend, "_capabilities", BackendCapabilities(c.DEBUGGER_GDB, frozenset({"status"}))
    )
    threads = Mock()
    monkeypatch.setattr(session, "get_threads", threads)
    registry = SessionRegistry()
    registry._sessions[1] = session

    async def check() -> None:
        arguments: dict[str, object] = {"session_id": 1, "action": c.ACTION_THREADS, "query": {}}
        result = await dispatch_tool_call(
            c.TOOL_CONTEXT_QUERY, arguments, registry, logger=logging.getLogger(__name__)
        )
        assert result.isError
        assert result.structuredContent is not None
        assert result.structuredContent["code"] == "unsupported_operation"
        assert result.structuredContent["details"]["operation"] == "threads"
        result = await dispatch_tool_call(
            c.TOOL_WORKFLOW_BATCH,
            {
                "session_id": 1,
                "steps": [
                    {
                        "tool": c.TOOL_CONTEXT_QUERY,
                        "arguments": {"action": c.ACTION_THREADS, "query": {}},
                    }
                ],
            },
            registry,
            logger=logging.getLogger(__name__),
        )
        assert result.structuredContent is not None
        assert (
            result.structuredContent["result"]["steps"][0]["result"]["code"]
            == "unsupported_operation"
        )

    asyncio.run(check())
    threads.assert_not_called()


def test_interrupt_reaches_backend_while_execution_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    session = create_default_session_service()
    monkeypatch.setattr(
        session.backend,
        "_capabilities",
        BackendCapabilities(
            c.DEBUGGER_GDB, frozenset({"continue", "interrupt"}), concurrent_control=True
        ),
    )
    started, released = threading.Event(), threading.Event()
    data = BackendData(c.DEBUGGER_GDB, "", {})

    def continue_execution(
        *, wait_for_stop: bool, timeout_sec: int, reverse: bool
    ) -> OperationSuccess[CommandExecutionInfo]:
        started.set()
        assert released.wait(3)
        return OperationSuccess(CommandExecutionInfo("continue", data))

    def interrupt() -> OperationSuccess[MessageResult]:
        released.set()
        return OperationSuccess(MessageResult("interrupted", data))

    monkeypatch.setattr(session, "continue_execution", continue_execution)
    monkeypatch.setattr(session, "interrupt", interrupt)
    registry = SessionRegistry()
    registry._sessions[1] = session

    async def check() -> None:
        pending = asyncio.create_task(
            dispatch_tool_call(
                c.TOOL_EXECUTION_MANAGE,
                {"session_id": 1, "action": c.ACTION_CONTINUE, "execution": {"wait_until": "stop"}},
                registry,
                logger=logging.getLogger(__name__),
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            result = await asyncio.wait_for(
                dispatch_tool_call(
                    c.TOOL_EXECUTION_MANAGE,
                    {"session_id": 1, "action": c.ACTION_INTERRUPT},
                    registry,
                    logger=logging.getLogger(__name__),
                ),
                1,
            )
            assert not result.isError
            assert not (await asyncio.wait_for(pending, 1)).isError
        finally:
            released.set()
            await pending

    asyncio.run(check())
