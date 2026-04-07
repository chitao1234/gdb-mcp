"""Shared contract synchronization tests for CLI and MCP surfaces."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import get_args, get_type_hints

import pytest

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
from gdb_mcp.mcp.schemas import build_tool_definitions


def _load_contracts() -> ModuleType:
    try:
        return importlib.import_module("gdb_mcp.contracts")
    except ModuleNotFoundError:
        pytest.fail(
            "Expected gdb_mcp.contracts to define shared public tool and value contracts"
        )


def _contract_values(name: str) -> tuple[str, ...]:
    value = getattr(_load_contracts(), name)
    assert isinstance(value, tuple)
    assert all(isinstance(item, str) for item in value)
    return value


def _contract_alias(name: str) -> object:
    return getattr(_load_contracts(), name)


@pytest.mark.parametrize(
    ("alias", "contract_name"),
    [
        (SessionQueryAction, "SESSION_QUERY_ACTIONS"),
        (InferiorQueryAction, "INFERIOR_QUERY_ACTIONS"),
        (InferiorManageAction, "INFERIOR_MANAGE_ACTIONS"),
        (InferiorFollowForkMode, "INFERIOR_FOLLOW_FORK_MODES"),
        (ExecutionManageAction, "EXECUTION_MANAGE_ACTIONS"),
        (ExecutionWaitUntil, "EXECUTION_WAIT_UNTIL_VALUES"),
        (ContextQueryAction, "CONTEXT_QUERY_ACTIONS"),
        (ContextManageAction, "CONTEXT_MANAGE_ACTIONS"),
        (BreakpointKind, "BREAKPOINT_KINDS"),
        (BreakpointAccess, "BREAKPOINT_ACCESS_VALUES"),
        (BreakpointEvent, "BREAKPOINT_EVENTS"),
        (BreakpointQueryAction, "BREAKPOINT_QUERY_ACTIONS"),
        (BreakpointManageAction, "BREAKPOINT_MANAGE_ACTIONS"),
        (LocationKind, "LOCATION_KINDS"),
        (InspectQueryAction, "INSPECT_QUERY_ACTIONS"),
        (RegisterValueFormat, "REGISTER_VALUE_FORMATS"),
        (DisassemblyMode, "DISASSEMBLY_MODES"),
    ],
)
def test_client_input_aliases_match_shared_contract_values(
    alias: object,
    contract_name: str,
) -> None:
    assert get_args(alias) == _contract_values(contract_name)


def test_public_tool_names_match_client_and_mcp_inventory() -> None:
    public_tool_names = _contract_values("PUBLIC_TOOL_NAMES")

    assert tuple(CLIENT_TOOL_SPECS) == public_tool_names
    assert tuple(tool.name for tool in build_tool_definitions()) == public_tool_names


def test_batch_step_tool_names_are_subset_of_public_tool_names() -> None:
    batch_step_tool_names = set(_contract_values("BATCH_STEP_TOOL_NAMES"))
    public_tool_names = set(_contract_values("PUBLIC_TOOL_NAMES"))

    assert batch_step_tool_names < public_tool_names


def test_batch_step_tool_alias_and_client_field_match_shared_contract() -> None:
    batch_step_tool_names = _contract_values("BATCH_STEP_TOOL_NAMES")
    session_step_hints = get_type_hints(SessionStepInput)

    assert get_args(_contract_alias("BatchStepToolName")) == batch_step_tool_names
    assert get_args(session_step_hints["tool"]) == batch_step_tool_names


def test_cli_location_kind_choices_keep_public_hyphenated_form() -> None:
    assert _contract_values("CLI_LOCATION_KIND_CHOICES") == (
        "current",
        "function",
        "address",
        "address-range",
        "file-line",
        "file-range",
    )
