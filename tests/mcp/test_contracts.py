"""Shared contract synchronization tests for CLI and MCP surfaces."""

from __future__ import annotations

from typing import get_args, get_type_hints

import pytest
from pydantic import BaseModel

from gdb_mcp.client.inputs import (
    BreakpointAccess,
    BreakpointEvent,
    BreakpointKind,
    BreakpointManageAction,
    BreakpointQueryAction,
    ContextManageAction,
    ContextQueryAction,
    DisassemblyMode,
    ExecutionManageAction,
    ExecutionWaitUntil,
    InferiorFollowForkMode,
    InferiorManageAction,
    InferiorQueryAction,
    InspectQueryAction,
    LocationKind,
    RegisterValueFormat,
    SessionStepInput,
    SessionQueryAction,
)
from gdb_mcp.client.specs import CLIENT_TOOL_SPECS
from gdb_mcp.contracts import (
    BatchStepToolName,
    BATCH_STEP_TOOL_NAMES,
    BREAKPOINT_ACCESS_VALUES,
    BREAKPOINT_EVENTS,
    BREAKPOINT_KINDS,
    BREAKPOINT_MANAGE_ACTIONS,
    BREAKPOINT_MANAGE_NUMBER_ACTIONS,
    BREAKPOINT_QUERY_ACTIONS,
    CLI_LOCATION_KIND_CHOICES,
    CONTEXT_MANAGE_ACTIONS,
    CONTEXT_QUERY_ACTIONS,
    DISASSEMBLY_MODES,
    EXECUTION_MANAGE_ACTIONS,
    EXECUTION_WAIT_UNTIL_VALUES,
    INFERIOR_FOLLOW_FORK_MODES,
    INFERIOR_MANAGE_ACTIONS,
    INFERIOR_QUERY_ACTIONS,
    INSPECT_QUERY_ACTIONS,
    LOCATION_KINDS,
    PUBLIC_TOOL_NAMES,
    REGISTER_VALUE_FORMATS,
    SESSION_QUERY_ACTIONS,
    TOOL_WORKFLOW_BATCH,
)
from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS
from gdb_mcp.mcp.schemas import (
    BATCH_STEP_TOOL_MODELS,
    BreakpointCatchCreateArgs,
    BreakpointListQueryArgs,
    BreakpointManageNumberAction,
    BreakpointWatchCreateArgs,
    DisassembleArgs,
    ExecutionWaitArgs,
    FollowForkModeArgs,
    GetRegistersArgs,
    InferiorFollowForkPayload,
    InspectDisassemblyQueryArgs,
    InspectRegistersQueryArgs,
    SetCatchpointArgs,
    SetWatchpointArgs,
    build_tool_definitions,
)


def _enum_values(model: type[BaseModel], field_name: str) -> tuple[str, ...]:
    return tuple(model.model_json_schema()["properties"][field_name]["enum"])


def _array_item_enum_values(model: type[BaseModel], field_name: str) -> tuple[str, ...]:
    items = model.model_json_schema()["properties"][field_name]["items"]
    return tuple(items["enum"])


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        (SessionQueryAction, SESSION_QUERY_ACTIONS),
        (InferiorQueryAction, INFERIOR_QUERY_ACTIONS),
        (InferiorManageAction, INFERIOR_MANAGE_ACTIONS),
        (InferiorFollowForkMode, INFERIOR_FOLLOW_FORK_MODES),
        (ExecutionManageAction, EXECUTION_MANAGE_ACTIONS),
        (ExecutionWaitUntil, EXECUTION_WAIT_UNTIL_VALUES),
        (ContextQueryAction, CONTEXT_QUERY_ACTIONS),
        (ContextManageAction, CONTEXT_MANAGE_ACTIONS),
        (BreakpointKind, BREAKPOINT_KINDS),
        (BreakpointAccess, BREAKPOINT_ACCESS_VALUES),
        (BreakpointEvent, BREAKPOINT_EVENTS),
        (BreakpointQueryAction, BREAKPOINT_QUERY_ACTIONS),
        (BreakpointManageAction, BREAKPOINT_MANAGE_ACTIONS),
        (LocationKind, LOCATION_KINDS),
        (InspectQueryAction, INSPECT_QUERY_ACTIONS),
        (RegisterValueFormat, REGISTER_VALUE_FORMATS),
        (DisassemblyMode, DISASSEMBLY_MODES),
    ],
)
def test_client_input_aliases_match_shared_contract_values(
    alias: object,
    expected: tuple[str, ...],
) -> None:
    assert get_args(alias) == expected


def test_public_tool_names_match_all_runtime_registries() -> None:
    assert tuple(CLIENT_TOOL_SPECS) == PUBLIC_TOOL_NAMES
    assert tuple(tool.name for tool in build_tool_definitions()) == PUBLIC_TOOL_NAMES


def test_batch_step_tool_names_match_workflow_allowlists() -> None:
    expected_tools = set(SESSION_TOOL_SPECS) - {TOOL_WORKFLOW_BATCH}
    assert set(BATCH_STEP_TOOL_NAMES) == expected_tools
    assert set(BATCH_STEP_TOOL_MODELS) == expected_tools
    for tool_name in expected_tools:
        assert BATCH_STEP_TOOL_MODELS[tool_name] is SESSION_TOOL_SPECS[tool_name].model


def test_batch_step_tool_alias_and_client_field_match_shared_contract() -> None:
    session_step_hints = get_type_hints(SessionStepInput)

    assert get_args(BatchStepToolName) == BATCH_STEP_TOOL_NAMES
    assert get_args(session_step_hints["tool"]) == BATCH_STEP_TOOL_NAMES


def test_shared_schema_enums_match_contract_values() -> None:
    assert _enum_values(SetWatchpointArgs, "access") == BREAKPOINT_ACCESS_VALUES
    assert _enum_values(SetCatchpointArgs, "kind") == BREAKPOINT_EVENTS
    assert _enum_values(DisassembleArgs, "mode") == DISASSEMBLY_MODES
    assert _enum_values(FollowForkModeArgs, "mode") == INFERIOR_FOLLOW_FORK_MODES
    assert _enum_values(InferiorFollowForkPayload, "mode") == INFERIOR_FOLLOW_FORK_MODES
    assert _enum_values(ExecutionWaitArgs, "until") == EXECUTION_WAIT_UNTIL_VALUES
    assert _enum_values(BreakpointWatchCreateArgs, "access") == BREAKPOINT_ACCESS_VALUES
    assert _enum_values(BreakpointCatchCreateArgs, "event") == BREAKPOINT_EVENTS
    assert _enum_values(GetRegistersArgs, "value_format") == REGISTER_VALUE_FORMATS
    assert _array_item_enum_values(BreakpointListQueryArgs, "kinds") == BREAKPOINT_KINDS
    assert _enum_values(BreakpointManageNumberAction, "action") == BREAKPOINT_MANAGE_NUMBER_ACTIONS
    assert _enum_values(InspectRegistersQueryArgs, "value_format") == REGISTER_VALUE_FORMATS
    assert _enum_values(InspectDisassemblyQueryArgs, "mode") == DISASSEMBLY_MODES


def test_cli_location_kind_choices_keep_public_hyphenated_form() -> None:
    assert CLI_LOCATION_KIND_CHOICES == (
        "current",
        "function",
        "address",
        "address-range",
        "file-line",
        "file-range",
    )
