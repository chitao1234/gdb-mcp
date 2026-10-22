"""Debugger extensions and decoding failures through the public tool boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import Mock

import pytest
from mcp.types import CallToolResult, TextContent

from gdb_mcp import contracts as c
from gdb_mcp.mcp.handlers import dispatch_tool_call
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.service import SessionService
from gdb_mcp.transport.mi_models import MiTransportResponse
from gdb_mcp.transport.protocols import GdbControllerProtocol
from tests.support import attach_controller, dig, gdb_backend, mapping, mapping_list


def _session(
    monkeypatch: pytest.MonkeyPatch, responses: list[MiTransportResponse]
) -> tuple[SessionRegistry, SessionService, Mock]:
    session = create_default_session_service()
    controller = Mock(spec=GdbControllerProtocol)
    controller.gdb_process.poll.return_value = None
    attach_controller(session, controller)
    session.runtime.mark_ready()
    send = Mock(side_effect=responses)
    monkeypatch.setattr(gdb_backend(session).transport, "send_command_and_wait_for_prompt", send)
    registry = SessionRegistry()
    registry._sessions[1] = session
    return registry, session, send


def _response(payload: object) -> MiTransportResponse:
    return MiTransportResponse(
        command_responses=[{"type": "result", "message": "done", "payload": payload}]
    )


def _call(registry: SessionRegistry, tool: str, arguments: dict[str, object]) -> CallToolResult:
    result = asyncio.run(
        dispatch_tool_call(tool, arguments, registry, logger=logging.getLogger(__name__))
    )
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert json.loads(content.text) == result.structuredContent
    return result


@pytest.mark.parametrize(
    ("action", "payload", "path", "expected"),
    [
        (
            c.ACTION_THREADS,
            {
                "threads": [
                    {
                        "id": "1",
                        "target-id": "Thread 0x1",
                        "target_id": "extension",
                        "future-field": {"nested-key": [None, {"dash-key": 7}]},
                    }
                ],
            },
            ("result", "threads"),
            [
                {
                    "id": "1",
                    "target-id": "Thread 0x1",
                    "target_id": "extension",
                    "future-field": {"nested-key": [None, {"dash-key": 7}]},
                }
            ],
        ),
        (
            c.ACTION_FRAME,
            {"frame": {"level": "0", "line": "12", "future-field": {"nested-key": [1, 2]}}},
            ("result", "frame"),
            {"level": 0, "line": 12, "future-field": {"nested-key": [1, 2]}},
        ),
    ],
)
def test_debugger_extensions_survive_dispatch_and_serialization(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    payload: dict[str, object],
    path: tuple[str, ...],
    expected: object,
) -> None:
    registry, _, _ = _session(monkeypatch, [_response(payload)])

    response = _call(registry, c.TOOL_CONTEXT_QUERY, {"session_id": 1, "action": action})

    assert not response.isError
    assert dig(mapping(response.structuredContent), *path) == expected


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    ("action", "payload"),
    [
        (c.ACTION_THREADS, {"threads": [{"id": 123}]}),
        (c.ACTION_FRAME, {"frame": {"level": "0", "line": "not-a-line"}}),
        (c.ACTION_THREADS, ["unexpected payload shape"]),
    ],
)
def test_decode_failure_returns_protocol_error_and_the_next_call_succeeds(
    monkeypatch: pytest.MonkeyPatch, batch: bool, action: str, payload: object
) -> None:
    registry, session, _ = _session(monkeypatch, [_response(payload), _response({"threads": []})])
    tool = c.TOOL_CONTEXT_QUERY
    arguments: dict[str, object] = {"session_id": 1, "action": action}
    if batch:
        tool = c.TOOL_WORKFLOW_BATCH
        arguments = {
            "session_id": 1,
            "steps": [{"tool": c.TOOL_CONTEXT_QUERY, "arguments": {"action": action}}],
        }

    response = _call(registry, tool, arguments)
    failure = mapping(response.structuredContent)
    if batch:
        assert not response.isError
        step = mapping_list(dig(failure, "result", "steps"))[0]
        failure = mapping(step["result"])
    else:
        assert response.isError
        assert failure["action"] == action
        assert dig(failure, "details", "session_id") == 1
    assert failure["status"] == "error"
    assert failure["code"] == "protocol_error"
    assert "field_errors" not in mapping(failure.get("details", {}))
    assert session.state is c.SessionState.READY

    following = _call(registry, c.TOOL_CONTEXT_QUERY, {"session_id": 1, "action": c.ACTION_THREADS})
    assert not following.isError
    assert dig(mapping(following.structuredContent), "result", "threads") == []


@pytest.mark.parametrize("mixed", [False, True])
def test_disassembly_preserves_instruction_extensions(
    monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    instruction = {
        "address": "0x401000",
        "inst": "nop",
        "func-name": "main",
        "offset": "0",
        "future-field": {"nested-key": [None, {"dash-key": 7}]},
        "future_field": "separate extension",
    }
    entry: dict[str, object] = instruction
    if mixed:
        entry = {
            "file": "main.c",
            "line": "12",
            "line_asm_insn": [instruction],
            "source-extension": {"nested-key": 8},
        }
    registry, _, _ = _session(monkeypatch, [_response({"asm_insns": [entry]})])

    response = _call(
        registry,
        c.TOOL_INSPECT_QUERY,
        {
            "session_id": 1,
            "action": "disassembly",
            "query": {
                "location": {
                    "kind": "address_range",
                    "start_address": "0x401000",
                    "end_address": "0x401001",
                }
            },
        },
    )

    assert not response.isError
    record = mapping_list(dig(mapping(response.structuredContent), "result", "instructions"))[0]
    assert record["instruction"] == "nop"
    assert record["function"] == "main"
    assert record["offset"] == 0
    assert record["future-field"] == instruction["future-field"]
    assert record["future_field"] == instruction["future_field"]
    if mixed:
        assert record["file"] == "main.c"
        assert record["line"] == 12
        assert record["source-extension"] == {"nested-key": 8}


def test_request_validation_keeps_field_errors_and_does_not_call_debugger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _, send = _session(monkeypatch, [])

    response = _call(
        registry,
        c.TOOL_INSPECT_QUERY,
        {"session_id": 1, "action": c.ACTION_EVALUATE, "query": {}},
    )

    assert response.isError
    failure = mapping(response.structuredContent)
    assert failure["code"] == "validation_error"
    field_error = mapping_list(dig(failure, "details", "field_errors"))[0]
    assert field_error["field"] == "query.expression"
    send.assert_not_called()


def test_unrecognized_debugger_error_keeps_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    message = "A future debugger condition this server does not classify"
    registry, session, _ = _session(
        monkeypatch,
        [
            MiTransportResponse(
                command_responses=[
                    {"type": "result", "message": "error", "payload": {"msg": message}}
                ]
            )
        ],
    )

    response = _call(registry, c.TOOL_EXECUTE_COMMAND, {"session_id": 1, "command": "info files"})

    assert response.isError
    failure = mapping(response.structuredContent)
    assert failure["code"] == "error"
    assert failure["message"] == message
    assert session.state is c.SessionState.READY


def test_unknown_notification_and_stop_reason_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extension = {"future-key": {"nested-key": [None, 7]}}
    registry, session, _ = _session(
        monkeypatch,
        [
            MiTransportResponse(
                command_responses=[{"type": "result", "message": "done", "payload": extension}],
                async_notifications=[
                    {"type": "notify", "message": "future-notification", "payload": extension},
                    {
                        "type": "notify",
                        "message": "stopped",
                        "payload": {"reason": "future-stop-reason", **extension},
                    },
                ],
            )
        ],
    )

    response = _call(registry, c.TOOL_EXECUTE_COMMAND, {"session_id": 1, "command": "info files"})

    assert not response.isError
    data = mapping(dig(mapping(response.structuredContent), "result", "backend_data", "data"))
    assert data["result"] == extension
    notification = mapping_list(data["notify"])[0]
    assert notification["message"] == "future-notification"
    assert notification["payload"] == extension
    event = session.last_stop_event
    assert event is not None
    assert event.reason == "future-stop-reason"
    assert event.stop_kind == "unknown"
    assert event.backend_data is not None
    assert event.backend_data.data == extension


def test_implementation_error_returns_internal_error_and_logs_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    registry, session, _ = _session(monkeypatch, [])
    monkeypatch.setattr(
        session, "get_threads", Mock(side_effect=RuntimeError("implementation bug"))
    )

    response = _call(registry, c.TOOL_CONTEXT_QUERY, {"session_id": 1, "action": c.ACTION_THREADS})

    assert response.isError
    assert mapping(response.structuredContent)["code"] == "internal_error"
    assert any(record.exc_info is not None for record in caplog.records)
    assert session.state is c.SessionState.READY
