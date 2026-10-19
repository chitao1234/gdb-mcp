"""Regression tests for the client-friendly published tool input schemas."""

from __future__ import annotations

import json
from typing import Any

import pytest

from gdb_mcp.contracts import (
    BATCH_STEP_TOOL_NAMES,
    BREAKPOINT_MANAGE_ACTIONS,
    INFERIOR_MANAGE_ACTIONS,
    INSPECT_QUERY_ACTIONS,
    LOCATION_KINDS,
    SESSION_QUERY_ACTIONS,
)
from gdb_mcp.mcp.schema_normalizer import public_input_schema
from gdb_mcp.mcp.schemas import StartSessionArgs, build_tool_definitions
from tests.support import dig, mapping

_UNSUPPORTED_KEYS = ("oneOf", "anyOf", "allOf", "$ref", "$defs", "discriminator")


@pytest.fixture(scope="module")
def tool_schemas() -> dict[str, dict[str, Any]]:
    """Return each published tool's normalized input schema, keyed by tool name."""

    return {tool.name: tool.inputSchema for tool in build_tool_definitions()}


def _find_unsupported_keys(node: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _UNSUPPORTED_KEYS:
                hits.append(f"{path}.{key}")
            hits.extend(_find_unsupported_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(_find_unsupported_keys(value, f"{path}[{index}]"))
    return hits


def test_every_published_tool_schema_is_combinator_free(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """No published schema may contain a JSON-Schema combinator or $ref."""

    for tool_name, schema in tool_schemas.items():
        assert _find_unsupported_keys(schema) == [], tool_name


def test_every_published_tool_schema_is_json_serializable(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Every published schema must survive a JSON round trip unchanged."""

    assert json.loads(json.dumps(tool_schemas)) == tool_schemas


def test_normalization_only_affects_the_published_copy() -> None:
    """Normalization is a published-surface concern, not a Pydantic change."""

    raw = StartSessionArgs.model_json_schema()

    assert _find_unsupported_keys(raw) != []
    assert _find_unsupported_keys(public_input_schema(StartSessionArgs)) == []


def test_public_input_schema_returns_independent_copies() -> None:
    """Mutating one normalized schema must not leak into the next call."""

    first = public_input_schema(StartSessionArgs)
    second = public_input_schema(StartSessionArgs)

    assert first == second
    assert first is not second

    mapping(dig(first, "properties", "program"))["type"] = "number"
    assert (
        mapping(dig(public_input_schema(StartSessionArgs), "properties", "program"))["type"]
        == "string"
    )


def test_public_input_schema_examples_do_not_pollute_the_cache() -> None:
    """Attaching examples must return a fresh copy, not mutate the cached one."""

    cached = public_input_schema(StartSessionArgs)

    with_examples = public_input_schema(
        StartSessionArgs,
        examples=({"program": "/bin/true"},),
    )

    assert "examples" not in cached
    assert with_examples is not cached
    assert with_examples["examples"] == [{"program": "/bin/true"}]


def test_action_discriminators_merge_into_enums(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Each action discriminator should become a plain enum of its variants."""

    assert tool_schemas["gdb_session_query"]["properties"]["action"]["enum"] == list(
        SESSION_QUERY_ACTIONS
    )
    assert tool_schemas["gdb_inferior_manage"]["properties"]["action"]["enum"] == list(
        INFERIOR_MANAGE_ACTIONS
    )
    assert tool_schemas["gdb_breakpoint_manage"]["properties"]["action"]["enum"] == list(
        BREAKPOINT_MANAGE_ACTIONS
    )
    assert tool_schemas["gdb_inspect_query"]["properties"]["action"]["enum"] == list(
        INSPECT_QUERY_ACTIONS
    )


def test_required_lists_keep_only_unconditional_requirements(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Only fields required for every action should stay in ``required``."""

    assert tool_schemas["gdb_session_query"]["required"] == ["action"]
    assert tool_schemas["gdb_inferior_manage"]["required"] == ["session_id", "action"]
    assert tool_schemas["gdb_inspect_query"]["required"] == ["session_id", "action"]
    assert tool_schemas["gdb_breakpoint_manage"]["required"] == [
        "session_id",
        "action",
        "breakpoint",
    ]
    assert "required" not in tool_schemas["gdb_breakpoint_manage"]["properties"]["breakpoint"]


def test_conditional_requirements_stay_documented(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Per-action requirements lost from ``required`` must appear in prose."""

    assert "action='status' requires session_id" in tool_schemas["gdb_session_query"]["description"]

    manage_description = tool_schemas["gdb_breakpoint_manage"]["description"]
    assert "action='create' requires breakpoint.kind" in manage_description
    assert "action='update' requires breakpoint.number, changes" in manage_description

    breakpoint = tool_schemas["gdb_breakpoint_manage"]["properties"]["breakpoint"]
    assert "kind='code' requires location" in breakpoint["description"]
    assert "kind='watch' requires expression" in breakpoint["description"]
    assert "kind='catch' requires event" in breakpoint["description"]

    inspect_description = tool_schemas["gdb_inspect_query"]["description"]
    assert "action='evaluate' requires query, query.expression" in inspect_description
    assert "action='memory' requires query, query.address, query.count" in inspect_description

    assert "Conditional requirements" not in tool_schemas["gdb_execution_manage"]["description"]


def test_per_action_accepted_fields_are_documented(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Action-dependent fields should be described on the payload object."""

    execution = tool_schemas["gdb_execution_manage"]["properties"]["execution"]
    assert "action='run' accepts" in execution["description"]
    assert "action='interrupt' accepts no fields" in execution["description"]

    query = tool_schemas["gdb_inspect_query"]["properties"]["query"]
    assert "action='evaluate' accepts context, expression" in query["description"]
    assert "action='registers' accepts" in query["description"]
    assert "value_format" in query["description"]


def test_alternatives_without_fields_do_not_describe_the_merged_object(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Actions that take no payload must not gain a spurious 'empty object' note."""

    execution = tool_schemas["gdb_execution_manage"]["properties"]["execution"]
    assert "Empty object payload" not in execution["description"]

    context_query = tool_schemas["gdb_context_query"]["properties"]["query"]
    assert "Empty object payload" not in context_query["description"]
    assert "action='threads' accepts no fields" in context_query["description"]


def test_uniform_field_sets_need_no_per_action_note(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """A payload identical for every action needs no action-dependent note."""

    # Every action takes an empty query, so nothing is action-dependent here.
    description = tool_schemas["gdb_session_query"]["properties"]["query"].get("description", "")
    assert "Accepted fields depend on" not in description


def test_nested_alternative_notes_survive_the_per_action_note(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Per-action notes must not clobber the nested alternative requirements."""

    breakpoint = tool_schemas["gdb_breakpoint_manage"]["properties"]["breakpoint"]

    assert "kind='code' requires location" in breakpoint["description"]
    assert "Accepted fields depend on action:" in breakpoint["description"]


def test_nested_location_union_merges_every_selector_kind(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """The nested location union should expose every selector kind and its rules."""

    query = tool_schemas["gdb_inspect_query"]["properties"]["query"]
    location = query["properties"]["location"]

    assert location["properties"]["kind"]["enum"] == list(LOCATION_KINDS)
    assert location["required"] == ["kind"]
    assert {"function", "address", "start_address", "file", "start_line"} <= set(
        location["properties"]
    )
    assert "kind='file_line' requires file, line" in location["description"]


def test_optional_fields_lose_null_wrappers_but_stay_optional(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Optional fields keep a plain type and stay out of ``required``."""

    schema = tool_schemas["gdb_session_start"]

    core = schema["properties"]["core"]
    assert core["type"] == "string"
    assert "default" not in core
    assert "required" not in schema


def test_heterogeneous_unions_keep_the_primary_declared_shape(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """A union with one structured variant keeps that variant's declared shape."""

    query = tool_schemas["gdb_inspect_query"]["properties"]["query"]
    assert query["properties"]["register_numbers"]["items"]["type"] == "integer"
    assert query["properties"]["max_registers"]["type"] == "integer"

    memory_range = tool_schemas["gdb_capture_bundle"]["properties"]["memory_ranges"]["items"]
    assert memory_range["type"] == "object"
    assert memory_range["required"] == ["address", "count"]

    startup_args = tool_schemas["gdb_run_until_failure"]["properties"]["startup"]["properties"][
        "args"
    ]
    assert startup_args["type"] == "array"
    assert startup_args["items"]["type"] == "string"


def test_batch_step_items_keep_the_tool_enum_and_shorthand_docs(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Batch step items should keep the tool enum and the shorthand hint."""

    steps = tool_schemas["gdb_workflow_batch"]["properties"]["steps"]

    assert steps["items"]["properties"]["tool"]["enum"] == list(BATCH_STEP_TOOL_NAMES)
    assert "shorthand tool-name string" in steps["description"]


def test_alternative_descriptions_merge_instead_of_dropping(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Merging action variants should concatenate, not drop, their descriptions."""

    action = tool_schemas["gdb_inferior_manage"]["properties"]["action"]

    assert "Create a new inferior" in action["description"]
    assert "Remove one inferior" in action["description"]
    assert "Select the active inferior" in action["description"]


def test_single_variant_actions_collapse_cleanly(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """An action with a single variant should be a scalar enum, not an object."""

    schema = tool_schemas["gdb_session_manage"]

    assert schema["properties"]["action"]["enum"] == ["stop"]
    assert schema["required"] == ["session_id", "action"]
