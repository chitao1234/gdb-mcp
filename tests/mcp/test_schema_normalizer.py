"""Regression tests for the client-friendly published tool input schemas."""

from __future__ import annotations

import json
from typing import Any

from gdb_mcp.contracts import (
    BATCH_STEP_TOOL_NAMES,
    BREAKPOINT_MANAGE_ACTIONS,
    INFERIOR_MANAGE_ACTIONS,
    INSPECT_QUERY_ACTIONS,
    LOCATION_KINDS,
    SESSION_QUERY_ACTIONS,
)
from gdb_mcp.mcp.schemas import StartSessionArgs, build_tool_definitions
from gdb_mcp.mcp.schema_normalizer import public_input_schema

_UNSUPPORTED_KEYS = ("oneOf", "anyOf", "allOf", "$ref", "$defs", "discriminator")


def _tool_schemas() -> dict[str, dict[str, Any]]:
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


def test_every_published_tool_schema_is_combinator_free() -> None:
    for tool in build_tool_definitions():
        assert _find_unsupported_keys(tool.inputSchema) == [], tool.name


def test_every_published_tool_schema_is_json_serializable() -> None:
    json.dumps([tool.inputSchema for tool in build_tool_definitions()])


def test_normalization_only_affects_the_published_copy() -> None:
    raw = StartSessionArgs.model_json_schema()

    assert _find_unsupported_keys(raw) != []
    assert _find_unsupported_keys(public_input_schema(StartSessionArgs)) == []


def test_public_input_schema_is_cached_per_model() -> None:
    first = public_input_schema(StartSessionArgs)

    assert public_input_schema(StartSessionArgs) is first


def test_public_input_schema_examples_do_not_pollute_the_cache() -> None:
    cached = public_input_schema(StartSessionArgs)

    with_examples = public_input_schema(
        StartSessionArgs,
        examples=({"program": "/bin/true"},),
    )

    assert "examples" not in cached
    assert with_examples is not cached
    assert with_examples["examples"] == [{"program": "/bin/true"}]


def test_action_discriminators_merge_into_enums() -> None:
    schemas = _tool_schemas()

    assert schemas["gdb_session_query"]["properties"]["action"]["enum"] == list(
        SESSION_QUERY_ACTIONS
    )
    assert schemas["gdb_inferior_manage"]["properties"]["action"]["enum"] == list(
        INFERIOR_MANAGE_ACTIONS
    )
    assert schemas["gdb_breakpoint_manage"]["properties"]["action"]["enum"] == list(
        BREAKPOINT_MANAGE_ACTIONS
    )
    assert schemas["gdb_inspect_query"]["properties"]["action"]["enum"] == list(
        INSPECT_QUERY_ACTIONS
    )


def test_required_lists_keep_only_unconditional_requirements() -> None:
    schemas = _tool_schemas()

    assert schemas["gdb_session_query"]["required"] == ["action"]
    assert schemas["gdb_inferior_manage"]["required"] == ["session_id", "action"]
    assert schemas["gdb_inspect_query"]["required"] == ["session_id", "action"]
    assert schemas["gdb_breakpoint_manage"]["required"] == ["session_id", "action", "breakpoint"]
    assert "required" not in schemas["gdb_breakpoint_manage"]["properties"]["breakpoint"]


def test_conditional_requirements_stay_documented() -> None:
    schemas = _tool_schemas()

    assert "action='status' requires session_id" in schemas["gdb_session_query"]["description"]

    manage_description = schemas["gdb_breakpoint_manage"]["description"]
    assert "action='create' requires breakpoint.kind" in manage_description
    assert "action='update' requires breakpoint.number, changes" in manage_description

    breakpoint = schemas["gdb_breakpoint_manage"]["properties"]["breakpoint"]
    assert "kind='code' requires location" in breakpoint["description"]
    assert "kind='watch' requires expression" in breakpoint["description"]
    assert "kind='catch' requires event" in breakpoint["description"]

    inspect_description = schemas["gdb_inspect_query"]["description"]
    assert "action='evaluate' requires query, query.expression" in inspect_description
    assert "action='memory' requires query, query.address, query.count" in inspect_description

    assert "Conditional requirements" not in schemas["gdb_execution_manage"]["description"]


def test_nested_location_union_merges_every_selector_kind() -> None:
    query = _tool_schemas()["gdb_inspect_query"]["properties"]["query"]
    location = query["properties"]["location"]

    assert location["properties"]["kind"]["enum"] == list(LOCATION_KINDS)
    assert location["required"] == ["kind"]
    assert {"function", "address", "start_address", "file", "start_line"} <= set(
        location["properties"]
    )
    assert "kind='file_line' requires file, line" in location["description"]


def test_optional_fields_lose_null_wrappers_but_stay_optional() -> None:
    schema = _tool_schemas()["gdb_session_start"]

    core = schema["properties"]["core"]
    assert core["type"] == "string"
    assert "default" not in core
    assert "required" not in schema


def test_heterogeneous_unions_keep_the_primary_declared_shape() -> None:
    schemas = _tool_schemas()

    query = schemas["gdb_inspect_query"]["properties"]["query"]
    assert query["properties"]["register_numbers"]["items"]["type"] == "integer"
    assert query["properties"]["max_registers"]["type"] == "integer"

    memory_range = schemas["gdb_capture_bundle"]["properties"]["memory_ranges"]["items"]
    assert memory_range["type"] == "object"
    assert memory_range["required"] == ["address", "count"]

    startup_args = schemas["gdb_run_until_failure"]["properties"]["startup"]["properties"]["args"]
    assert startup_args["type"] == "array"
    assert startup_args["items"]["type"] == "string"


def test_batch_step_items_keep_the_tool_enum_and_shorthand_docs() -> None:
    steps = _tool_schemas()["gdb_workflow_batch"]["properties"]["steps"]

    assert steps["items"]["properties"]["tool"]["enum"] == list(BATCH_STEP_TOOL_NAMES)
    assert "shorthand tool-name string" in steps["description"]


def test_alternative_descriptions_merge_instead_of_dropping() -> None:
    action = _tool_schemas()["gdb_inferior_manage"]["properties"]["action"]

    assert "Create a new inferior" in action["description"]
    assert "Remove one inferior" in action["description"]
    assert "Select the active inferior" in action["description"]


def test_single_variant_actions_collapse_cleanly() -> None:
    schema = _tool_schemas()["gdb_session_manage"]

    assert schema["properties"]["action"]["enum"] == ["stop"]
    assert schema["required"] == ["session_id", "action"]
