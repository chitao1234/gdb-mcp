"""Validate published tool examples against the typed request models."""

from __future__ import annotations

import jsonschema
import pytest
from pydantic import BaseModel

from gdb_mcp.mcp.schemas import (
    BatchArgs,
    BreakpointManageArgs,
    CaptureBundleArgs,
    ExecutionManageArgs,
    InferiorManageArgs,
    InspectQueryArgs,
    RunUntilFailureArgs,
    SessionQueryArgs,
    build_tool_definitions,
)
from gdb_mcp.mcp.tool_examples import TOOL_EXAMPLES

_MODELS: dict[str, type[BaseModel]] = {
    "gdb_session_query": SessionQueryArgs,
    "gdb_inferior_manage": InferiorManageArgs,
    "gdb_execution_manage": ExecutionManageArgs,
    "gdb_breakpoint_manage": BreakpointManageArgs,
    "gdb_inspect_query": InspectQueryArgs,
    "gdb_workflow_batch": BatchArgs,
    "gdb_capture_bundle": CaptureBundleArgs,
    "gdb_run_until_failure": RunUntilFailureArgs,
}


def test_examples_cover_known_tools() -> None:
    assert set(TOOL_EXAMPLES) == set(_MODELS)


@pytest.mark.parametrize("tool_name", sorted(TOOL_EXAMPLES))
def test_examples_validate_against_request_models(tool_name: str) -> None:
    model = _MODELS[tool_name]

    for example in TOOL_EXAMPLES[tool_name]:
        model.model_validate(example)


@pytest.mark.parametrize("tool_name", sorted(TOOL_EXAMPLES))
def test_examples_validate_against_published_schemas(tool_name: str) -> None:
    schema = {tool.name: tool.inputSchema for tool in build_tool_definitions()}[tool_name]

    for example in TOOL_EXAMPLES[tool_name]:
        jsonschema.validate(instance=example, schema=schema)


def test_examples_are_published_in_tool_schemas() -> None:
    schemas = {tool.name: tool.inputSchema for tool in build_tool_definitions()}

    for tool_name, examples in TOOL_EXAMPLES.items():
        assert schemas[tool_name]["examples"] == [dict(example) for example in examples]
