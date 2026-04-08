"""Shared contract synchronization tests for CLI and MCP surfaces."""

from __future__ import annotations

from typing import get_args, get_type_hints

import pytest
from pydantic import BaseModel

import gdb_mcp.contracts as shared_contracts
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
    BATCH_STEP_TOOL_NAMES as SCHEMA_BATCH_STEP_TOOL_NAMES,
    BATCH_STEP_TOOL_MODELS,
    BreakpointCodeCreateArgs,
    BreakpointCatchCreateArgs,
    BreakpointListQueryArgs,
    BreakpointManageNumberAction,
    BreakpointWatchCreateArgs,
    ContextQueryThreadsAction,
    DisassembleArgs,
    ExecutionRunAction,
    ExecutionWaitArgs,
    FollowForkModeArgs,
    GetRegistersArgs,
    InferiorFollowForkPayload,
    InspectEvaluateAction,
    InspectDisassemblyQueryArgs,
    InspectRegistersQueryArgs,
    LocationCurrentArgs,
    SetCatchpointArgs,
    SetWatchpointArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    build_tool_definitions,
)


def _enum_values(model: type[BaseModel], field_name: str) -> tuple[str, ...]:
    return tuple(model.model_json_schema()["properties"][field_name]["enum"])


def _const_value(model: type[BaseModel], field_name: str) -> str:
    return str(model.model_json_schema()["properties"][field_name]["const"])


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


@pytest.mark.parametrize(
    ("alias_name", "value_name"),
    [
        ("ActionListName", "ACTION_LIST"),
        ("ActionStatusName", "ACTION_STATUS"),
        ("ActionStopName", "ACTION_STOP"),
        ("ActionCurrentName", "ACTION_CURRENT"),
        ("ActionCreateName", "ACTION_CREATE"),
        ("ActionRemoveName", "ACTION_REMOVE"),
        ("ActionSelectName", "ACTION_SELECT"),
        ("ActionSetFollowForkModeName", "ACTION_SET_FOLLOW_FORK_MODE"),
        ("ActionSetDetachOnForkName", "ACTION_SET_DETACH_ON_FORK"),
        ("ActionRunName", "ACTION_RUN"),
        ("ActionContinueName", "ACTION_CONTINUE"),
        ("ActionInterruptName", "ACTION_INTERRUPT"),
        ("ActionStepName", "ACTION_STEP"),
        ("ActionNextName", "ACTION_NEXT"),
        ("ActionFinishName", "ACTION_FINISH"),
        ("ActionWaitForStopName", "ACTION_WAIT_FOR_STOP"),
        ("ActionUpdateName", "ACTION_UPDATE"),
        ("ActionDeleteName", "ACTION_DELETE"),
        ("ActionEnableName", "ACTION_ENABLE"),
        ("ActionDisableName", "ACTION_DISABLE"),
        ("ActionGetName", "ACTION_GET"),
        ("ActionThreadsName", "ACTION_THREADS"),
        ("ActionBacktraceName", "ACTION_BACKTRACE"),
        ("ActionFrameName", "ACTION_FRAME"),
        ("ActionSelectThreadName", "ACTION_SELECT_THREAD"),
        ("ActionSelectFrameName", "ACTION_SELECT_FRAME"),
        ("ActionEvaluateName", "ACTION_EVALUATE"),
        ("ActionVariablesName", "ACTION_VARIABLES"),
        ("ActionRegistersName", "ACTION_REGISTERS"),
        ("ActionMemoryName", "ACTION_MEMORY"),
        ("ActionDisassemblyName", "ACTION_DISASSEMBLY"),
        ("ActionSourceName", "ACTION_SOURCE"),
        ("BreakpointKindCodeName", "BREAKPOINT_KIND_CODE"),
        ("BreakpointKindWatchName", "BREAKPOINT_KIND_WATCH"),
        ("BreakpointKindCatchName", "BREAKPOINT_KIND_CATCH"),
        ("LocationKindCurrentName", "LOCATION_KIND_CURRENT"),
        ("LocationKindFunctionName", "LOCATION_KIND_FUNCTION"),
        ("LocationKindAddressName", "LOCATION_KIND_ADDRESS"),
        ("LocationKindAddressRangeName", "LOCATION_KIND_ADDRESS_RANGE"),
        ("LocationKindFileLineName", "LOCATION_KIND_FILE_LINE"),
        ("LocationKindFileRangeName", "LOCATION_KIND_FILE_RANGE"),
    ],
)
def test_shared_single_value_public_contract_aliases_match_runtime_values(
    alias_name: str,
    value_name: str,
) -> None:
    assert get_args(getattr(shared_contracts, alias_name)) == (
        getattr(shared_contracts, value_name),
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        (
            shared_contracts.TOOL_SESSION_QUERY,
            {"session_id": 9},
            shared_contracts.WorkflowStepValidationIssue(
                kind="session_id_not_allowed",
            ),
        ),
        (
            shared_contracts.TOOL_SESSION_QUERY,
            {"action": shared_contracts.ACTION_LIST},
            shared_contracts.WorkflowStepValidationIssue(
                kind="session_query_list_not_allowed",
            ),
        ),
        (
            shared_contracts.TOOL_SESSION_MANAGE,
            {"action": shared_contracts.ACTION_STOP},
            shared_contracts.WorkflowStepValidationIssue(
                kind="session_manage_not_allowed",
            ),
        ),
        (
            shared_contracts.TOOL_WORKFLOW_BATCH,
            {},
            shared_contracts.WorkflowStepValidationIssue(
                kind="nested_workflow_tool_not_allowed",
            ),
        ),
        (
            shared_contracts.TOOL_RUN_UNTIL_FAILURE,
            {},
            shared_contracts.WorkflowStepValidationIssue(
                kind="nested_workflow_tool_not_allowed",
            ),
        ),
        (
            shared_contracts.TOOL_CONTEXT_QUERY,
            {"action": shared_contracts.ACTION_THREADS},
            None,
        ),
        (
            "gdb_not_a_real_tool",
            {},
            shared_contracts.WorkflowStepValidationIssue(
                kind="unknown_tool",
            ),
        ),
    ],
)
def test_validate_workflow_step_contract_matches_shared_public_rules(
    tool_name: str,
    arguments: dict[str, object],
    expected: shared_contracts.WorkflowStepValidationIssue | None,
) -> None:
    assert shared_contracts.validate_workflow_step_contract(tool_name, arguments) == expected


def test_public_tool_names_match_all_runtime_registries() -> None:
    assert set(CLIENT_TOOL_SPECS) == set(PUBLIC_TOOL_NAMES)
    assert tuple(tool.name for tool in build_tool_definitions()) == PUBLIC_TOOL_NAMES


def test_batch_step_tool_names_match_workflow_allowlists() -> None:
    expected_tools = set(SESSION_TOOL_SPECS) - {TOOL_WORKFLOW_BATCH}
    assert SCHEMA_BATCH_STEP_TOOL_NAMES == BATCH_STEP_TOOL_NAMES
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


def test_shared_schema_const_fields_match_public_contract_values() -> None:
    assert _const_value(SessionQueryListAction, "action") == shared_contracts.ACTION_LIST
    assert _const_value(SessionQueryStatusAction, "action") == shared_contracts.ACTION_STATUS
    assert _const_value(ExecutionRunAction, "action") == shared_contracts.ACTION_RUN
    assert _const_value(ContextQueryThreadsAction, "action") == shared_contracts.ACTION_THREADS
    assert _const_value(InspectEvaluateAction, "action") == shared_contracts.ACTION_EVALUATE
    assert _const_value(BreakpointCodeCreateArgs, "kind") == shared_contracts.BREAKPOINT_KIND_CODE
    assert _const_value(LocationCurrentArgs, "kind") == shared_contracts.LOCATION_KIND_CURRENT


def test_cli_location_kind_choices_keep_public_hyphenated_form() -> None:
    assert CLI_LOCATION_KIND_CHOICES == (
        "current",
        "function",
        "address",
        "address-range",
        "file-line",
        "file-range",
    )
