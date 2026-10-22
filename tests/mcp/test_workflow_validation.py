"""Workflow request failures use the same field-error format as direct calls."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from gdb_mcp import contracts as c
from gdb_mcp.domain import OperationError
from gdb_mcp.mcp.handlers import dispatch_tool_call
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from tests.support import mapping, mapping_list


@pytest.mark.parametrize("campaign", [False, True])
def test_invalid_workflow_step_lists_every_field_error_with_its_step_path(campaign: bool) -> None:
    registry = SessionRegistry()
    session = create_default_session_service()
    registry._sessions[1] = session
    steps: list[dict[str, object]] = [
        {"tool": c.TOOL_CONTEXT_QUERY, "arguments": {"action": c.ACTION_THREADS}},
        {
            "tool": c.TOOL_INSPECT_QUERY,
            "arguments": {"action": c.ACTION_EVALUATE, "query": {"unexpected": True}},
        },
    ]
    tool = c.TOOL_WORKFLOW_BATCH
    steps_field = "steps"
    arguments: dict[str, object] = {"session_id": 1, "steps": steps}
    if campaign:
        tool = c.TOOL_RUN_UNTIL_FAILURE
        steps_field = "setup_steps"
        arguments = {"startup": {}, "setup_steps": steps}

    response = asyncio.run(
        dispatch_tool_call(tool, arguments, registry, logger=logging.getLogger(__name__))
    )

    assert response.isError
    payload = mapping(response.structuredContent)
    assert payload["code"] == "validation_error"
    assert payload["action"] == c.ACTION_EVALUATE
    assert payload["tool"] == c.TOOL_INSPECT_QUERY
    errors = mapping_list(mapping(payload["details"])["field_errors"])
    prefix = f"{steps_field}[1].arguments.query"
    assert {(error["field"], error["issue"]) for error in errors} == {
        (f"{prefix}.expression", "missing"),
        (f"{prefix}.unexpected", "not_allowed"),
    }
    assert all(str(error["message"]) in str(payload["message"]) for error in errors)
    content = response.content[0]
    assert isinstance(content, TextContent)
    assert json.loads(content.text) == payload
    assert session.command_transcript == ()


def test_batch_step_failure_has_same_session_annotations_as_direct_call() -> None:
    registry = SessionRegistry()
    session = create_default_session_service()
    session.runtime.mark_ready()
    registry._sessions[1] = session

    with patch.object(
        session,
        "get_backtrace",
        return_value=OperationError(
            message="No registers.", details={"command": "-stack-list-frames 0 1"}
        ),
    ):
        direct_response = asyncio.run(
            dispatch_tool_call(
                c.TOOL_CONTEXT_QUERY,
                {"session_id": 1, "action": c.ACTION_BACKTRACE, "query": {}},
                registry,
                logger=logging.getLogger(__name__),
            )
        )
        batch_response = asyncio.run(
            dispatch_tool_call(
                c.TOOL_WORKFLOW_BATCH,
                {
                    "session_id": 1,
                    "steps": [
                        {
                            "tool": c.TOOL_CONTEXT_QUERY,
                            "arguments": {"action": c.ACTION_BACKTRACE, "query": {}},
                        }
                    ],
                },
                registry,
                logger=logging.getLogger(__name__),
            )
        )

    direct = mapping(direct_response.structuredContent)
    batch = mapping(batch_response.structuredContent)
    steps = mapping_list(mapping(batch["result"])["steps"])
    nested = mapping(steps[0]["result"])
    assert direct["code"] == nested["code"] == "no_registers"
    assert direct["action"] == nested["action"] == c.ACTION_BACKTRACE
    assert mapping(direct["details"])["session_id"] == 1
    assert direct["details"] == nested["details"]
