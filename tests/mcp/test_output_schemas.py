"""Tests for the published tool output schemas.

The schemas are what a client validates ``structuredContent`` against, so they
have to accept every shape this server can return: both envelope outcomes, the
``null`` values a result really carries, and any field a debugger added beyond
the declared ones.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from gdb_mcp.contracts import (
    ACTION_BACKTRACE,
    ACTION_DISASSEMBLY,
    ACTION_LIST,
    ACTION_MEMORY,
    ACTION_SOURCE,
    ACTION_THREADS,
    PUBLIC_TOOL_NAMES,
    TOOL_BREAKPOINT_QUERY,
    TOOL_CAPTURE_BUNDLE,
    TOOL_CONTEXT_QUERY,
    TOOL_INFERIOR_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INSPECT_QUERY,
    TOOL_SESSION_QUERY,
)
from gdb_mcp.mcp.schemas import build_tool_definitions
from gdb_mcp.mcp.schemas.outputs import TOOL_OUTPUT_PAYLOADS, public_output_schema
from tests.support import dig, json_object, mapping, sequence, unsupported_schema_keys


@pytest.fixture(scope="module")
def output_schemas() -> dict[str, dict[str, object]]:
    """Return each published tool's output schema, keyed by tool name."""

    schemas: dict[str, dict[str, object]] = {}
    for tool in build_tool_definitions():
        assert tool.outputSchema is not None, tool.name
        schemas[tool.name] = tool.outputSchema
    return schemas


def _variants(schema: dict[str, object]) -> list[dict[str, object]]:
    """Return the alternatives one published schema offers."""

    return [mapping(item) for item in sequence(schema["anyOf"])]


def _resolve(schema: dict[str, object], node: object) -> dict[str, object]:
    """Follow one ``$ref`` inside a schema document."""

    target = mapping(node)
    ref = target.get("$ref")
    if not isinstance(ref, str):
        return target
    return mapping(mapping(schema["$defs"])[ref.rsplit("/", 1)[-1]])


def test_every_public_tool_publishes_an_output_schema(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A tool without an output schema would leave its payload undocumented."""

    assert set(output_schemas) == set(PUBLIC_TOOL_NAMES)
    assert set(output_schemas) == set(TOOL_OUTPUT_PAYLOADS)


def test_every_output_schema_has_no_unsupported_construct(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """Output schemas follow the same publication policy as input schemas."""

    for tool_name, schema in output_schemas.items():
        assert unsupported_schema_keys(schema) == [], tool_name


def test_every_output_schema_is_json_serializable(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """Every published schema must survive a JSON round trip unchanged."""

    assert json_object(json.dumps(output_schemas)) == output_schemas


def test_every_output_schema_describes_an_object(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """MCP output schemas are object schemas, so the root has to say so."""

    for tool_name, schema in output_schemas.items():
        assert schema.get("type") == "object", tool_name


def test_every_output_schema_offers_success_and_error_alternatives(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A client needs to validate both outcomes of every tool."""

    for tool_name, schema in output_schemas.items():
        statuses = [
            sequence(dig(variant, "properties", "status", "enum"))[0]
            for variant in _variants(schema)
        ]
        assert statuses.count("success") >= 1, tool_name
        assert statuses.count("error") == 1, tool_name


def test_every_success_alternative_accepts_extra_fields(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A debugger-specific field must not be rejected by the published schema."""

    for tool_name, schema in output_schemas.items():
        for variant in _variants(schema):
            if dig(variant, "properties", "status", "enum") != ["success"]:
                continue
            assert variant.get("additionalProperties") is True, tool_name


def test_every_output_schema_accepts_the_error_envelope(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """An error result has to validate against every tool it can come from."""

    error = {
        "status": "error",
        "code": "validation_error",
        "message": "breakpoint.location is required for kind=code",
        "fatal": False,
        "tool": "gdb_breakpoint_manage",
        "details": {
            "field_errors": [
                {
                    "field": "breakpoint.location",
                    "issue": "missing",
                    "message": "breakpoint.location is required for kind=code",
                }
            ]
        },
    }

    for schema in output_schemas.values():
        jsonschema.validate(error, schema)


def test_success_warnings_are_an_optional_array(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """Warnings are omitted when empty and otherwise always an array."""

    schema = output_schemas["gdb_session_start"]
    jsonschema.validate(
        {
            "status": "success",
            "result": {
                "session_id": 1,
                "diagnostics": {
                    "environment_results": [],
                    "initialization_results": [],
                    "backend_data": {
                        "debugger": "gdb",
                        "console_output": "",
                        "data": {},
                    },
                },
            },
            "warnings": [],
        },
        schema,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"status": "success", "result": {"session_id": 1}, "warnings": None}, schema
        )


def test_an_action_tool_offers_one_alternative_per_action(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """Each action keeps its own alternative, with its own requirements."""

    variants = _variants(output_schemas["gdb_breakpoint_query"])
    successes = [
        variant
        for variant in variants
        if dig(variant, "properties", "status", "enum") == ["success"]
    ]

    assert [dig(variant, "properties", "action", "enum") for variant in successes] == [
        ["list"],
        ["get"],
    ]
    assert [variant["required"] for variant in successes] == [
        ["status", "action", "result"],
        ["status", "action", "result"],
    ]


def test_a_dedicated_tool_nests_its_payload_under_result(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A dedicated tool publishes its payload under the uniform result field."""

    success = _variants(output_schemas["gdb_session_start"])[0]

    assert success["required"] == ["status", "result"]
    result = _resolve(output_schemas["gdb_session_start"], mapping(success["properties"])["result"])
    assert {"session_id", "message", "target_loaded", "execution_state"} <= set(
        mapping(result["properties"])
    )


@pytest.mark.parametrize(
    ("tool_name", "action", "collection", "removed_field"),
    [
        (TOOL_SESSION_QUERY, ACTION_LIST, "sessions", "count"),
        (TOOL_INFERIOR_QUERY, ACTION_LIST, "inferiors", "count"),
        (TOOL_BREAKPOINT_QUERY, ACTION_LIST, "breakpoints", "count"),
        (TOOL_CONTEXT_QUERY, ACTION_THREADS, "threads", "count"),
        (TOOL_CONTEXT_QUERY, ACTION_BACKTRACE, "frames", "count"),
        (TOOL_INSPECT_QUERY, ACTION_DISASSEMBLY, "instructions", "count"),
        (TOOL_INSPECT_QUERY, ACTION_SOURCE, "lines", "count"),
        (TOOL_INSPECT_QUERY, ACTION_MEMORY, "blocks", "block_count"),
        (TOOL_CAPTURE_BUNDLE, None, "artifacts", "artifact_count"),
    ],
)
def test_collection_results_publish_arrays_without_duplicate_counts(
    output_schemas: dict[str, dict[str, object]],
    tool_name: str,
    action: str | None,
    collection: str,
    removed_field: str,
) -> None:
    """Clients count returned items from the array itself."""

    schema = output_schemas[tool_name]
    success = next(
        variant
        for variant in _variants(schema)
        if dig(variant, "properties", "status", "enum") == ["success"]
        and (action is None or dig(variant, "properties", "action", "enum") == [action])
    )
    properties = mapping(_resolve(schema, dig(success, "properties", "result"))["properties"])

    assert _resolve(schema, properties[collection])["type"] == "array"
    assert removed_field not in properties


def test_memory_result_distinguishes_requested_and_captured_bytes(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A partial read must preserve the request length independently of the data."""

    schema = output_schemas[TOOL_INSPECT_QUERY]
    success = next(
        variant
        for variant in _variants(schema)
        if dig(variant, "properties", "action", "enum") == [ACTION_MEMORY]
    )
    result = _resolve(schema, dig(success, "properties", "result"))
    properties = mapping(result["properties"])

    assert "requested_bytes" in sequence(result["required"])
    assert "count" not in properties
    jsonschema.validate(
        {
            "status": "success",
            "action": ACTION_MEMORY,
            "result": {"address": "buffer", "requested_bytes": 64, "captured_bytes": 4},
        },
        schema,
    )


def test_a_nullable_result_field_keeps_its_null_alternative(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A result publishes the null it really carries, unlike a request."""

    success = _variants(output_schemas["gdb_workflow_batch"])[0]
    result = _resolve(
        output_schemas["gdb_workflow_batch"], mapping(success["properties"])["result"]
    )
    last_stop_event = mapping(mapping(result["properties"])["last_stop_event"])

    alternatives = [mapping(item) for item in sequence(last_stop_event["anyOf"])]
    assert alternatives == [{"$ref": "#/$defs/StopEvent"}, {"type": "null"}]

    jsonschema.validate(
        {
            "status": "success",
            "result": {
                "steps": [],
                "requested_steps": 0,
                "final_execution_state": None,
                "final_stop_reason": None,
                "final_stop_kind": None,
                "last_stop_event": None,
            },
        },
        output_schemas["gdb_workflow_batch"],
    )


def test_a_nullable_enum_keeps_its_null_alternative(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """A closed vocabulary still allows a result that has no value yet."""

    schema = output_schemas["gdb_session_query"]
    status = next(
        variant
        for variant in _variants(schema)
        if dig(variant, "properties", "action", "enum") == ["status"]
    )
    result = _resolve(schema, dig(status, "properties", "result"))
    stop_kind = mapping(mapping(result["properties"])["stop_kind"])

    assert [mapping(item) for item in sequence(stop_kind["anyOf"])][1] == {"type": "null"}


def test_backend_data_is_tagged_and_keeps_a_recursive_native_payload(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """Opaque debugger replies are explicitly tagged and recursively described."""

    schema = output_schemas["gdb_execute_command"]
    success = _variants(schema)[0]

    payload = _resolve(schema, mapping(success["properties"])["result"])
    backend_data = _resolve(schema, dig(payload, "properties", "backend_data"))
    assert dig(backend_data, "properties", "debugger", "enum") == ["gdb", "lldb"]
    assert dig(backend_data, "properties", "data", "$ref") == "#/$defs/JsonObject"
    assert "backend_data" in sequence(payload["required"])
    assert {"JsonObject", "JsonValue"} <= set(mapping(schema["$defs"]))

    jsonschema.validate(
        {
            "status": "success",
            "result": {
                "command": "-break-list",
                "backend_data": {
                    "debugger": "gdb",
                    "console_output": "",
                    "data": {"x": [1]},
                },
            },
        },
        schema,
    )


def test_output_schemas_exclude_removed_duplicate_and_ambiguous_fields(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """Published result models do not restore fields removed from the contract."""

    serialized = json.dumps(output_schemas)
    for removed in (
        "debugger_result",
        "startup_output",
        "env_output",
        "init_output",
        "is_running",
        "has_controller",
        "lifecycle_state",
        "iterations_completed",
        "last_result",
        "artifact_count",
        "block_count",
    ):
        assert f'"{removed}"' not in serialized
    assert '"create_output"' not in serialized

    session_status = next(
        variant
        for variant in _variants(output_schemas[TOOL_SESSION_QUERY])
        if dig(variant, "properties", "action", "enum") == ["status"]
    )
    status_result = _resolve(
        output_schemas[TOOL_SESSION_QUERY], dig(session_status, "properties", "result")
    )
    assert {"debugger_alive", "session_state"} <= set(mapping(status_result["properties"]))

    stop_event = mapping(mapping(output_schemas["gdb_workflow_batch"]["$defs"])["StopEvent"])
    stop_properties = mapping(stop_event["properties"])
    assert {"signal_name", "signal_meaning", "signal_code", "fault_address"} <= set(stop_properties)
    assert "backend_data" in stop_properties
    assert not {"crash", "details"} & set(stop_properties)

    start = _variants(output_schemas["gdb_session_start"])[0]
    start_result = _resolve(output_schemas["gdb_session_start"], dig(start, "properties", "result"))
    diagnostics = _resolve(
        output_schemas["gdb_session_start"], dig(start_result, "properties", "diagnostics")
    )
    assert {"environment_results", "initialization_results", "backend_data"} <= set(
        mapping(diagnostics["properties"])
    )
    assert {"environment_results", "initialization_results", "backend_data"} <= set(
        sequence(diagnostics["required"])
    )

    inferior_schema = output_schemas[TOOL_INFERIOR_QUERY]
    inferior_list = next(
        variant
        for variant in _variants(inferior_schema)
        if dig(variant, "properties", "action", "enum") == [ACTION_LIST]
    )
    inferiors = _resolve(
        inferior_schema,
        dig(
            _resolve(inferior_schema, dig(inferior_list, "properties", "result")),
            "properties",
            "inferiors",
        ),
    )
    inferior_item = _resolve(inferior_schema, inferiors["items"])
    assert "is_current" not in mapping(inferior_item["properties"])
    for variant in _variants(output_schemas[TOOL_INFERIOR_MANAGE]):
        if dig(variant, "properties", "status", "enum") != ["success"]:
            continue
        result = _resolve(
            output_schemas[TOOL_INFERIOR_MANAGE], dig(variant, "properties", "result")
        )
        assert "is_current" not in mapping(result["properties"])

    batch = _resolve(
        output_schemas["gdb_workflow_batch"],
        dig(_variants(output_schemas["gdb_workflow_batch"])[0], "properties", "result"),
    )
    step = _resolve(output_schemas["gdb_workflow_batch"], dig(batch, "properties", "steps"))
    step_payload = _resolve(output_schemas["gdb_workflow_batch"], step["items"])
    assert not {"status", "action", "code"} & set(mapping(step_payload["properties"]))

    campaign_schema = output_schemas["gdb_run_until_failure"]
    campaign = _resolve(campaign_schema, dig(_variants(campaign_schema)[0], "properties", "result"))
    campaign_properties = mapping(campaign["properties"])
    assert not {
        "iterations_completed",
        "execution_state",
        "stop_reason",
        "stop_kind",
        "exit_code",
        "matched_failure",
        "trigger",
        "last_result",
    } & set(campaign_properties)
    assert "result" in mapping(
        _resolve(campaign_schema, mapping(campaign_properties["iterations"])["items"])["properties"]
    )


def test_the_start_payload_documents_the_session_id(
    output_schemas: dict[str, dict[str, object]],
) -> None:
    """The id the registry allocates is part of the published payload."""

    success = _variants(output_schemas["gdb_session_start"])[0]
    payload = _resolve(
        output_schemas["gdb_session_start"], mapping(success["properties"])["result"]
    )
    session_id = mapping(mapping(payload["properties"])["session_id"])

    assert session_id["type"] == "integer"
    assert "session_id" in sequence(payload["required"])
    for invalid in ({}, {"session_id": None}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {"status": "success", "result": invalid}, output_schemas["gdb_session_start"]
            )

    jsonschema.validate(
        {
            "status": "success",
            "result": {
                "session_id": 7,
                "message": "GDB session started successfully",
                "target_loaded": True,
                "execution_state": "not_started",
                "diagnostics": {
                    "environment_results": [],
                    "initialization_results": [],
                    "backend_data": {
                        "debugger": "gdb",
                        "console_output": "",
                        "data": {},
                    },
                },
            },
        },
        output_schemas["gdb_session_start"],
    )


def test_public_output_schema_returns_independent_copies() -> None:
    """Callers may mutate the schema they receive without affecting the next call."""

    first = public_output_schema("gdb_session_start")
    second = public_output_schema("gdb_session_start")

    assert first == second
    assert first is not second

    success = mapping(sequence(first["anyOf"])[0])
    mapping(success["properties"])["injected"] = {"type": "string"}

    published = public_output_schema("gdb_session_start")
    assert "injected" not in mapping(mapping(sequence(published["anyOf"])[0])["properties"])
