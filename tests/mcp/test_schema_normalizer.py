"""Tests for the published tool schemas and their normalization.

A published schema may use ``anyOf``, ``$ref``, and ``$defs``, but not ``oneOf``,
``allOf``, or ``discriminator``. These tests pin that policy and the parts of the
published shape a client relies on: one alternative per action, each stating its
own requirements, and optional fields that keep their ``null`` alternative.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gdb_mcp.contracts import BATCH_STEP_TOOL_NAMES, LOCATION_KINDS
from gdb_mcp.mcp.schema_normalizer import normalize_schema, public_input_schema
from gdb_mcp.mcp.schemas import SessionQueryArgs, StartSessionArgs, build_tool_definitions
from tests.support import dig, mapping, mapping_list, sequence, unsupported_schema_keys


@pytest.fixture(scope="module")
def tool_schemas() -> dict[str, dict[str, Any]]:
    """Return each published tool's input schema, keyed by tool name."""

    return {tool.name: tool.inputSchema for tool in build_tool_definitions()}


def _resolve(schema: dict[str, Any], node: object) -> dict[str, Any]:
    """Follow one ``$ref`` inside a schema document."""

    target = mapping(node)
    ref = target.get("$ref")
    if not isinstance(ref, str):
        return target
    return mapping(mapping(schema["$defs"])[ref.rsplit("/", 1)[-1]])


def test_every_published_tool_schema_has_no_unsupported_construct(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """No published schema may contain ``oneOf``, ``allOf``, or ``discriminator``."""

    for tool_name, schema in tool_schemas.items():
        assert unsupported_schema_keys(schema) == [], tool_name


def test_every_published_tool_schema_is_json_serializable(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Every published schema must survive a JSON round trip unchanged."""

    assert json.loads(json.dumps(tool_schemas)) == tool_schemas


def test_normalization_only_affects_the_published_copy() -> None:
    """Normalization is a published-surface concern, not a Pydantic change."""

    raw = SessionQueryArgs.model_json_schema()

    assert unsupported_schema_keys(raw) != []
    assert unsupported_schema_keys(public_input_schema(SessionQueryArgs)) == []


def test_public_input_schema_returns_independent_copies() -> None:
    """Mutating one published schema must not leak into the next call."""

    first = public_input_schema(StartSessionArgs)
    second = public_input_schema(StartSessionArgs)

    assert first == second
    assert first is not second

    mapping(dig(first, "properties", "program"))["title"] = "Mutated"
    assert (
        mapping(dig(public_input_schema(StartSessionArgs), "properties", "program"))["title"]
        != "Mutated"
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


def test_a_tagged_union_publishes_one_alternative_per_action(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """Every action keeps its own alternative, with its own requirements."""

    schema = tool_schemas["gdb_session_query"]
    variants = [_resolve(schema, item) for item in sequence(schema["anyOf"])]

    assert [dig(variant, "properties", "action", "enum") for variant in variants] == [
        ["list"],
        ["status"],
    ]
    assert [variant["required"] for variant in variants] == [
        ["action"],
        ["session_id", "action"],
    ]


def test_a_single_action_union_publishes_one_alternative(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """A one-action tool is still a union, so a client sees the same shape."""

    schema = tool_schemas["gdb_session_manage"]
    variants = [_resolve(schema, item) for item in sequence(schema["anyOf"])]

    assert len(variants) == 1
    assert dig(variants[0], "properties", "action", "enum") == ["stop"]
    assert variants[0]["required"] == ["session_id", "action"]


def test_an_optional_field_publishes_its_null_alternative(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """A caller may send an explicit null, so the schema allows it."""

    core = mapping(dig(tool_schemas["gdb_session_start"], "properties", "core"))

    assert [variant["type"] for variant in mapping_list(core["anyOf"])] == ["string", "null"]
    assert "required" not in tool_schemas["gdb_session_start"]


def test_a_nested_union_keeps_every_alternative(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """A nested selector union publishes each selector, not just the first."""

    schema = tool_schemas["gdb_inspect_query"]
    variants = [_resolve(schema, item) for item in sequence(schema["anyOf"])]
    disassembly = next(
        variant
        for variant in variants
        if dig(variant, "properties", "action", "enum") == ["disassembly"]
    )
    query = _resolve(schema, dig(disassembly, "properties", "query"))
    location = _resolve(schema, dig(query, "properties", "location"))
    selectors = [_resolve(schema, item) for item in sequence(location["anyOf"])]

    kinds = {sequence(dig(selector, "properties", "kind", "enum"))[0] for selector in selectors}
    assert kinds == set(LOCATION_KINDS)


def test_batch_step_items_keep_the_tool_enum(tool_schemas: dict[str, dict[str, Any]]) -> None:
    """A batch step names one of the supported tools."""

    schema = tool_schemas["gdb_workflow_batch"]
    steps = mapping(dig(schema, "properties", "steps"))
    items = mapping(steps["items"])
    step = next(
        _resolve(schema, item) for item in sequence(items["anyOf"]) if "$ref" in mapping(item)
    )

    assert dig(step, "properties", "tool", "enum") == list(BATCH_STEP_TOOL_NAMES)


def test_every_published_input_schema_describes_an_object(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    """MCP tool schemas are object schemas, so the root has to say so."""

    for tool_name, schema in tool_schemas.items():
        assert schema.get("type") == "object", tool_name


def test_a_union_root_still_declares_an_object() -> None:
    """A tagged union of payload objects publishes as one object schema."""

    published = normalize_schema(
        {
            "anyOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
            ]
        }
    )

    assert published["type"] == "object"
    assert len(sequence(published["anyOf"])) == 2


def test_a_recursive_reference_publishes_its_definitions() -> None:
    """``$ref`` and ``$defs`` are allowed, so a recursive schema needs no rewrite."""

    recursive: dict[str, object] = {
        "type": "object",
        "properties": {"child": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
            }
        },
    }

    published = normalize_schema(recursive)

    assert unsupported_schema_keys(published) == []
    assert published["properties"] == {"child": {"$ref": "#/$defs/Node"}}
    assert "$defs" in published


def test_a_const_becomes_a_single_value_enum() -> None:
    """``const`` is spelled as an enum, which every client understands."""

    published = normalize_schema({"type": "object", "properties": {"action": {"const": "list"}}})

    assert mapping(published["properties"])["action"] == {"enum": ["list"]}
