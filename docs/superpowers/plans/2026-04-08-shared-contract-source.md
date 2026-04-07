# Shared Contract Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. If the user has expressed a strong preference for one of these execution styles, keep using that style unless they explicitly ask to switch. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Codex subagent-driven execution:** Subagents cannot stream partial progress back to the controller while still running. The controller should assign each subagent a unique shared progress file and inspect that file during execution when visibility is needed.

**Goal:** Introduce one shared contract source for public tool names and shared public value sets, then make the client and MCP schema layers consume it without changing public behavior.

**Architecture:** Add `src/gdb_mcp/contracts.py` as the canonical owner of tool-name constants, batch-step tool names, parser choice tuples, and shared typing aliases for public value sets. Migrate `src/gdb_mcp/client/inputs.py`, `src/gdb_mcp/client/specs.py`, `src/gdb_mcp/client/input_parsers.py`, `src/gdb_mcp/mcp/schemas.py`, and `src/gdb_mcp/mcp/handlers.py` to consume that shared source while leaving the Pydantic schema tree explicit and hand-written.

**Tech Stack:** Python 3.10+, `argparse`, Pydantic v2, pytest, ruff, mypy.

---

### Task 1: Add The Shared Contract Module And Move Client-Facing Value Sets

**Files:**
- Create: `src/gdb_mcp/contracts.py`
- Modify: `src/gdb_mcp/client/inputs.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Create: `tests/mcp/test_contracts.py`

**Testing approach:** `TDD`
Reason: The new contract module is a clear isolated seam. A failing test first is the cleanest way to prove the shared source exists and becomes the canonical owner of the repeated client-facing values.

- [ ] **Step 1: Write failing contract-sync tests for the new shared module**

```python
# tests/mcp/test_contracts.py

from __future__ import annotations

from typing import get_args

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
    SessionQueryAction,
)
from gdb_mcp.client.specs import CLIENT_TOOL_SPECS
from gdb_mcp.contracts import (
    BATCH_STEP_TOOL_NAMES,
    BREAKPOINT_ACCESS_VALUES,
    BREAKPOINT_EVENTS,
    BREAKPOINT_KINDS,
    BREAKPOINT_MANAGE_ACTIONS,
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
)
from gdb_mcp.mcp.schemas import build_tool_definitions


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


def test_public_tool_names_match_client_and_mcp_inventory() -> None:
    assert set(PUBLIC_TOOL_NAMES) == set(CLIENT_TOOL_SPECS)
    assert set(PUBLIC_TOOL_NAMES) == {tool.name for tool in build_tool_definitions()}


def test_batch_step_tool_names_are_subset_of_public_tool_names() -> None:
    assert set(BATCH_STEP_TOOL_NAMES) < set(PUBLIC_TOOL_NAMES)


def test_cli_location_kind_choices_keep_public_hyphenated_form() -> None:
    assert CLI_LOCATION_KIND_CHOICES == (
        "current",
        "function",
        "address",
        "address-range",
        "file-line",
        "file-range",
    )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_contracts.py`
Expected: FAIL because `src/gdb_mcp/contracts.py` does not exist yet.

- [ ] **Step 3: Implement the shared contract module and switch the client layer to consume it**

```python
# src/gdb_mcp/contracts.py

"""Shared public contract values for the CLI and MCP schema layers."""

from __future__ import annotations

from typing import Literal, TypeAlias

TOOL_SESSION_START = "gdb_session_start"
TOOL_SESSION_QUERY = "gdb_session_query"
TOOL_SESSION_MANAGE = "gdb_session_manage"
TOOL_INFERIOR_QUERY = "gdb_inferior_query"
TOOL_INFERIOR_MANAGE = "gdb_inferior_manage"
TOOL_EXECUTION_MANAGE = "gdb_execution_manage"
TOOL_BREAKPOINT_QUERY = "gdb_breakpoint_query"
TOOL_BREAKPOINT_MANAGE = "gdb_breakpoint_manage"
TOOL_CONTEXT_QUERY = "gdb_context_query"
TOOL_CONTEXT_MANAGE = "gdb_context_manage"
TOOL_INSPECT_QUERY = "gdb_inspect_query"
TOOL_WORKFLOW_BATCH = "gdb_workflow_batch"
TOOL_CAPTURE_BUNDLE = "gdb_capture_bundle"
TOOL_RUN_UNTIL_FAILURE = "gdb_run_until_failure"
TOOL_EXECUTE_COMMAND = "gdb_execute_command"
TOOL_ATTACH_PROCESS = "gdb_attach_process"
TOOL_CALL_FUNCTION = "gdb_call_function"

PUBLIC_TOOL_NAMES = (
    TOOL_SESSION_START,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INFERIOR_MANAGE,
    TOOL_EXECUTION_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_CONTEXT_MANAGE,
    TOOL_INSPECT_QUERY,
    TOOL_WORKFLOW_BATCH,
    TOOL_CAPTURE_BUNDLE,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_EXECUTE_COMMAND,
    TOOL_ATTACH_PROCESS,
    TOOL_CALL_FUNCTION,
)

BATCH_STEP_TOOL_NAMES = (
    TOOL_SESSION_QUERY,
    TOOL_INFERIOR_QUERY,
    TOOL_INFERIOR_MANAGE,
    TOOL_EXECUTION_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_CONTEXT_MANAGE,
    TOOL_INSPECT_QUERY,
    TOOL_CAPTURE_BUNDLE,
    TOOL_EXECUTE_COMMAND,
    TOOL_ATTACH_PROCESS,
    TOOL_CALL_FUNCTION,
)
BatchStepToolName: TypeAlias = Literal[
    "gdb_session_query",
    "gdb_inferior_query",
    "gdb_inferior_manage",
    "gdb_execution_manage",
    "gdb_breakpoint_query",
    "gdb_breakpoint_manage",
    "gdb_context_query",
    "gdb_context_manage",
    "gdb_inspect_query",
    "gdb_capture_bundle",
    "gdb_execute_command",
    "gdb_attach_process",
    "gdb_call_function",
]

SESSION_QUERY_ACTIONS = ("list", "status")
SessionQueryAction: TypeAlias = Literal["list", "status"]

INFERIOR_QUERY_ACTIONS = ("list", "current")
InferiorQueryAction: TypeAlias = Literal["list", "current"]

INFERIOR_MANAGE_ACTIONS = (
    "create",
    "remove",
    "select",
    "set_follow_fork_mode",
    "set_detach_on_fork",
)
InferiorManageAction: TypeAlias = Literal[
    "create",
    "remove",
    "select",
    "set_follow_fork_mode",
    "set_detach_on_fork",
]
INFERIOR_FOLLOW_FORK_MODES = ("parent", "child")
InferiorFollowForkMode: TypeAlias = Literal["parent", "child"]

EXECUTION_WAIT_UNTIL_VALUES = ("acknowledged", "stop")
ExecutionWaitUntil: TypeAlias = Literal["acknowledged", "stop"]
EXECUTION_MANAGE_ACTIONS = ("run", "continue", "interrupt", "step", "next", "finish", "wait_for_stop")
ExecutionManageAction: TypeAlias = Literal[
    "run",
    "continue",
    "interrupt",
    "step",
    "next",
    "finish",
    "wait_for_stop",
]

CONTEXT_QUERY_ACTIONS = ("threads", "backtrace", "frame")
ContextQueryAction: TypeAlias = Literal["threads", "backtrace", "frame"]
CONTEXT_MANAGE_ACTIONS = ("select_thread", "select_frame")
ContextManageAction: TypeAlias = Literal["select_thread", "select_frame"]

BREAKPOINT_KINDS = ("code", "watch", "catch")
BreakpointKind: TypeAlias = Literal["code", "watch", "catch"]
BREAKPOINT_ACCESS_VALUES = ("write", "read", "access")
BreakpointAccess: TypeAlias = Literal["write", "read", "access"]
BREAKPOINT_EVENTS = (
    "throw",
    "rethrow",
    "catch",
    "exec",
    "fork",
    "vfork",
    "load",
    "unload",
    "signal",
    "syscall",
)
BreakpointEvent: TypeAlias = Literal[
    "throw",
    "rethrow",
    "catch",
    "exec",
    "fork",
    "vfork",
    "load",
    "unload",
    "signal",
    "syscall",
]
BREAKPOINT_QUERY_ACTIONS = ("list", "get")
BreakpointQueryAction: TypeAlias = Literal["list", "get"]
BREAKPOINT_MANAGE_ACTIONS = ("create", "update", "delete", "enable", "disable")
BreakpointManageAction: TypeAlias = Literal["create", "update", "delete", "enable", "disable"]
BREAKPOINT_MANAGE_NUMBER_ACTIONS = ("delete", "enable", "disable")
BreakpointManageNumberActionName: TypeAlias = Literal["delete", "enable", "disable"]

LOCATION_KINDS = ("current", "function", "address", "address_range", "file_line", "file_range")
LocationKind: TypeAlias = Literal[
    "current",
    "function",
    "address",
    "address_range",
    "file_line",
    "file_range",
]
CLI_LOCATION_KIND_CHOICES = ("current", "function", "address", "address-range", "file-line", "file-range")

INSPECT_QUERY_ACTIONS = ("evaluate", "variables", "registers", "memory", "disassembly", "source")
InspectQueryAction: TypeAlias = Literal[
    "evaluate",
    "variables",
    "registers",
    "memory",
    "disassembly",
    "source",
]
REGISTER_VALUE_FORMATS = ("hex", "natural")
RegisterValueFormat: TypeAlias = Literal["hex", "natural"]
DISASSEMBLY_MODES = ("assembly", "mixed")
DisassemblyMode: TypeAlias = Literal["assembly", "mixed"]
```

```python
# src/gdb_mcp/client/inputs.py

from gdb_mcp.contracts import (
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
    SessionQueryAction,
)
```

```python
# src/gdb_mcp/client/specs.py

from gdb_mcp.contracts import (
    BREAKPOINT_ACCESS_VALUES,
    BREAKPOINT_EVENTS,
    BREAKPOINT_KINDS,
    BREAKPOINT_MANAGE_ACTIONS,
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
    PUBLIC_TOOL_NAMES,
    REGISTER_VALUE_FORMATS,
    SESSION_QUERY_ACTIONS,
)


def _add_action(parser: argparse.ArgumentParser, *, choices: tuple[str, ...]) -> None:
    parser.add_argument("--action", required=True, choices=choices)


def _configure_session_query(parser: argparse.ArgumentParser) -> None:
    _add_action(parser, choices=SESSION_QUERY_ACTIONS)
    _add_session_id(parser, required=False)


def _configure_inferior_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INFERIOR_MANAGE_ACTIONS)
    parser.add_argument("--mode", choices=INFERIOR_FOLLOW_FORK_MODES, default=argparse.SUPPRESS)


def _configure_execution_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=EXECUTION_MANAGE_ACTIONS)
    parser.add_argument("--wait-until", choices=EXECUTION_WAIT_UNTIL_VALUES, default=argparse.SUPPRESS)


def _configure_breakpoint_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=BREAKPOINT_QUERY_ACTIONS)
    parser.add_argument("--kind", dest="kinds", action="append", choices=BREAKPOINT_KINDS, default=argparse.SUPPRESS)


def _configure_breakpoint_manage(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=BREAKPOINT_MANAGE_ACTIONS)
    parser.add_argument("--breakpoint-kind", choices=BREAKPOINT_KINDS, default=argparse.SUPPRESS)
    parser.add_argument("--access", choices=BREAKPOINT_ACCESS_VALUES, default=argparse.SUPPRESS)
    parser.add_argument("--event", choices=BREAKPOINT_EVENTS, default=argparse.SUPPRESS)


def _configure_inspect_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INSPECT_QUERY_ACTIONS)
    parser.add_argument("--value-format", choices=REGISTER_VALUE_FORMATS, default=argparse.SUPPRESS)
    parser.add_argument("--location-kind", choices=CLI_LOCATION_KIND_CHOICES, default=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=DISASSEMBLY_MODES, default=argparse.SUPPRESS)

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_contracts.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/contracts.py src/gdb_mcp/client/inputs.py src/gdb_mcp/client/specs.py tests/mcp/test_contracts.py
git commit -m "refactor: add shared contract module"
```

### Task 2: Adopt Shared Contracts In MCP Schemas, Workflow Validation, And Tool Registries

**Files:**
- Modify: `src/gdb_mcp/mcp/schemas.py`
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/client/input_parsers.py`
- Modify: `tests/mcp/test_contracts.py`
- Modify: `tests/mcp/test_client_cli.py`

**Testing approach:** `existing tests + targeted verification`
Reason: This stage is a behavior-preserving structural refactor. The public validation and routing behavior is already covered; the goal is to replace local copies with shared imports while keeping the suite green.

- [ ] **Step 1: Capture the remaining duplicated contract sites before the refactor**

```bash
rg -n "gdb_session_query|gdb_inferior_query|gdb_execution_manage|gdb_breakpoint_query|gdb_context_query|gdb_inspect_query|gdb_capture_bundle|gdb_execute_command|gdb_attach_process|gdb_call_function" src/gdb_mcp/mcp src/gdb_mcp/client/input_parsers.py
rg -n 'Literal\\["write", "read", "access"\\]|Literal\\["hex", "natural"\\]|Literal\\["assembly", "mixed"\\]|Literal\\["parent", "child"\\]' src/gdb_mcp/mcp/schemas.py
```

Expected: the tool-name allowlists and shared enum-like literals are still defined locally in `mcp/schemas.py`, `mcp/handlers.py`, and `client/input_parsers.py`.

- [ ] **Step 2: Run the focused verification for the pre-change baseline**

Run: `uv run pytest -q tests/mcp/test_contracts.py tests/mcp/test_client_cli.py tests/mcp/test_schemas.py tests/mcp/test_handlers.py`
Expected: PASS

- [ ] **Step 3: Replace local tool-name and shared-value copies with imports from `gdb_mcp.contracts`**

```python
# src/gdb_mcp/mcp/schemas.py

from ..contracts import (
    BATCH_STEP_TOOL_NAMES,
    BatchStepToolName,
    BreakpointAccess,
    BreakpointEvent,
    BreakpointKind,
    BreakpointManageNumberActionName,
    DisassemblyMode,
    ExecutionWaitUntil,
    InferiorFollowForkMode,
    RegisterValueFormat,
    TOOL_ATTACH_PROCESS,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_CALL_FUNCTION,
    TOOL_CAPTURE_BUNDLE,
    TOOL_CONTEXT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_EXECUTE_COMMAND,
    TOOL_EXECUTION_MANAGE,
    TOOL_INFERIOR_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INSPECT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)


class InferiorFollowForkPayload(StrictArgsModel):
    mode: InferiorFollowForkMode = Field(
        ...,
        description="Whether GDB should follow the parent or child after fork/vfork.",
    )


class ExecutionWaitArgs(StrictArgsModel):
    until: ExecutionWaitUntil = Field(
        "stop",
        description="Whether to return when GDB acknowledges running or when a stop is observed.",
    )


class BreakpointWatchCreateArgs(StrictArgsModel):
    access: BreakpointAccess = Field(
        "write",
        description="Whether to stop on writes only, reads only, or any access",
    )


class BreakpointCatchCreateArgs(StrictArgsModel):
    event: BreakpointEvent = Field(..., description="Catchpoint event kind")


class BreakpointListQueryArgs(StrictArgsModel):
    kinds: list[BreakpointKind] = Field(
        default_factory=list,
        description="Optional breakpoint kinds to include",
    )


class BreakpointManageNumberAction(StrictArgsModel):
    action: BreakpointManageNumberActionName = Field(
        ...,
        description="Mutate one existing breakpoint",
    )


class InspectRegistersQueryArgs(StrictArgsModel):
    value_format: RegisterValueFormat = Field("hex", description="Value rendering mode")


class InspectDisassemblyQueryArgs(StrictArgsModel):
    mode: DisassemblyMode = Field(
        "mixed",
        description="Whether to request assembly only or mixed source/assembly output",
    )


BATCH_STEP_TOOL_MODELS: dict[str, type[BaseModel]] = {
    TOOL_EXECUTE_COMMAND: ExecuteCommandArgs,
    TOOL_SESSION_QUERY: SessionQueryArgs,
    TOOL_INFERIOR_QUERY: InferiorQueryArgs,
    TOOL_INFERIOR_MANAGE: InferiorManageArgs,
    TOOL_EXECUTION_MANAGE: ExecutionManageArgs,
    TOOL_BREAKPOINT_QUERY: BreakpointQueryArgs,
    TOOL_BREAKPOINT_MANAGE: BreakpointManageArgs,
    TOOL_CONTEXT_QUERY: ContextQueryArgs,
    TOOL_CONTEXT_MANAGE: ContextManageArgs,
    TOOL_INSPECT_QUERY: InspectQueryArgs,
    TOOL_ATTACH_PROCESS: AttachProcessArgs,
    TOOL_CAPTURE_BUNDLE: CaptureBundleArgs,
    TOOL_CALL_FUNCTION: CallFunctionArgs,
}

# Update the existing Tool(...) entries to use imported tool-name constants.
Tool(
    name=TOOL_SESSION_START,
    description=(
        "Start a new GDB debugging session. Can load an executable, core dump, "
        "or run custom initialization commands. "
        "Automatically detects and reports important warnings such as: "
        "missing debug symbols (not compiled with -g), file not found, or invalid executable. "
        "Check the 'warnings' field in the response for critical issues that may affect debugging. "
        "Available parameters: program (executable path), args (program arguments), "
        "core (core dump path - uses --core flag for proper symbol resolution), "
        "init_commands (GDB commands to run after environment setup), "
        "env (environment variables applied before init_commands), gdb_path (GDB binary path), "
        "working_dir (directory to run program from). "
        "NOTE: 'args' and 'core' are mutually exclusive in one startup request. "
        "The success response includes 'target_loaded' so callers can distinguish "
        "between 'GDB started' and 'requested target actually loaded'. "
        "IMPORTANT for core dump debugging: Set 'sysroot' and 'solib-search-path' AFTER "
        "loading the core (either via 'core' parameter or 'core-file' init_command) "
        "for symbols to resolve correctly. "
        "Returns a session_id integer that must be passed to all other GDB tools."
    ),
    inputSchema=StartSessionArgs.model_json_schema(),
)
Tool(
    name=TOOL_SESSION_QUERY,
    description=(
        "Query session inventory or inspect one live session. "
        "Use action='list' to enumerate active sessions or action='status' to inspect one session."
    ),
    inputSchema=SessionQueryArgs.model_json_schema(),
)
```

```python
# src/gdb_mcp/mcp/handlers.py

from ..contracts import (
    TOOL_ATTACH_PROCESS,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_CALL_FUNCTION,
    TOOL_CAPTURE_BUNDLE,
    TOOL_CONTEXT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_EXECUTE_COMMAND,
    TOOL_EXECUTION_MANAGE,
    TOOL_INFERIOR_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INSPECT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)


SESSION_TOOL_SPECS: dict[str, SessionToolSpec] = {
    TOOL_EXECUTE_COMMAND: session_tool_spec(ExecuteCommandArgs, _handle_execute_command),
    TOOL_SESSION_QUERY: session_tool_spec(SessionQueryArgs, _handle_session_query_for_session),
    TOOL_INFERIOR_QUERY: session_tool_spec(InferiorQueryArgs, _handle_inferior_query),
    TOOL_INFERIOR_MANAGE: session_tool_spec(InferiorManageArgs, _handle_inferior_manage),
    TOOL_EXECUTION_MANAGE: session_tool_spec(ExecutionManageArgs, _handle_execution_manage),
    TOOL_BREAKPOINT_QUERY: session_tool_spec(BreakpointQueryArgs, _handle_breakpoint_query),
    TOOL_BREAKPOINT_MANAGE: session_tool_spec(BreakpointManageArgs, _handle_breakpoint_manage),
    TOOL_CONTEXT_QUERY: session_tool_spec(ContextQueryArgs, _handle_context_query),
    TOOL_CONTEXT_MANAGE: session_tool_spec(ContextManageArgs, _handle_context_manage),
    TOOL_INSPECT_QUERY: session_tool_spec(InspectQueryArgs, _handle_inspect_query),
    TOOL_WORKFLOW_BATCH: session_tool_spec(BatchArgs, _handle_batch),
    TOOL_ATTACH_PROCESS: session_tool_spec(AttachProcessArgs, _handle_attach_process),
    TOOL_CAPTURE_BUNDLE: session_tool_spec(CaptureBundleArgs, _handle_capture_bundle),
    TOOL_CALL_FUNCTION: session_tool_spec(CallFunctionArgs, _handle_call_function),
}


if name == TOOL_SESSION_START:
    return serialize_result(_handle_start_session(normalized_args, session_manager))
if name == TOOL_SESSION_QUERY:
    return serialize_result(_handle_session_query(normalized_args, session_manager))
if name == TOOL_SESSION_MANAGE:
    return serialize_result(_handle_session_manage(normalized_args, session_manager))
if name == TOOL_RUN_UNTIL_FAILURE:
    return serialize_result(_handle_run_until_failure(normalized_args, session_manager))
```

```python
# src/gdb_mcp/client/input_parsers.py

from gdb_mcp.contracts import (
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
)


if tool_name == TOOL_SESSION_QUERY and arguments.get("action") == "list":
    raise CliUsageError("gdb_session_query(action=list) is not valid inside workflow steps")

if tool_name == TOOL_SESSION_MANAGE:
    raise CliUsageError("gdb_session_manage is not valid inside workflow steps")

if tool_name in {TOOL_WORKFLOW_BATCH, TOOL_RUN_UNTIL_FAILURE}:
    raise CliUsageError(f"{tool_name} is not valid inside workflow steps")
```

```python
# tests/mcp/test_contracts.py

from __future__ import annotations

from pydantic import BaseModel

from gdb_mcp.client.specs import CLIENT_TOOL_SPECS
from gdb_mcp.contracts import (
    BATCH_STEP_TOOL_NAMES,
    BREAKPOINT_ACCESS_VALUES,
    DISASSEMBLY_MODES,
    EXECUTION_WAIT_UNTIL_VALUES,
    INFERIOR_FOLLOW_FORK_MODES,
    PUBLIC_TOOL_NAMES,
    REGISTER_VALUE_FORMATS,
    TOOL_WORKFLOW_BATCH,
)
from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS
from gdb_mcp.mcp.schemas import (
    BATCH_STEP_TOOL_MODELS,
    BreakpointWatchCreateArgs,
    ExecutionWaitArgs,
    InferiorFollowForkPayload,
    InspectDisassemblyQueryArgs,
    InspectRegistersQueryArgs,
    build_tool_definitions,
)


def _enum_values(model: type[BaseModel], field_name: str) -> tuple[str, ...]:
    return tuple(model.model_json_schema()["properties"][field_name]["enum"])


def test_public_tool_names_match_all_runtime_registries() -> None:
    assert set(PUBLIC_TOOL_NAMES) == set(CLIENT_TOOL_SPECS)
    assert set(PUBLIC_TOOL_NAMES) == {tool.name for tool in build_tool_definitions()}


def test_batch_step_tool_names_match_workflow_allowlists() -> None:
    expected_tools = set(SESSION_TOOL_SPECS) - {TOOL_WORKFLOW_BATCH}
    assert set(BATCH_STEP_TOOL_NAMES) == expected_tools
    assert set(BATCH_STEP_TOOL_MODELS) == expected_tools


def test_shared_schema_enums_match_contract_values() -> None:
    assert _enum_values(InferiorFollowForkPayload, "mode") == INFERIOR_FOLLOW_FORK_MODES
    assert _enum_values(ExecutionWaitArgs, "until") == EXECUTION_WAIT_UNTIL_VALUES
    assert _enum_values(BreakpointWatchCreateArgs, "access") == BREAKPOINT_ACCESS_VALUES
    assert _enum_values(InspectRegistersQueryArgs, "value_format") == REGISTER_VALUE_FORMATS
    assert _enum_values(InspectDisassemblyQueryArgs, "mode") == DISASSEMBLY_MODES
```

```python
# tests/mcp/test_client_cli.py

# Delete these two moved inventory-sync tests from TestClientCli:

def test_client_tool_specs_cover_public_tool_inventory(self):
    from gdb_mcp.client.specs import CLIENT_TOOL_SPECS
    from gdb_mcp.mcp.schemas import build_tool_definitions

    assert set(CLIENT_TOOL_SPECS) == {tool.name for tool in build_tool_definitions()}


def test_batch_step_tool_models_match_server_workflow_allowlist(self):
    from gdb_mcp.mcp.handlers import SESSION_TOOL_SPECS
    from gdb_mcp.mcp.schemas import BATCH_STEP_TOOL_MODELS

    expected_tools = set(SESSION_TOOL_SPECS) - {"gdb_workflow_batch"}
    assert set(BATCH_STEP_TOOL_MODELS) == expected_tools
    for tool_name in expected_tools:
        assert BATCH_STEP_TOOL_MODELS[tool_name] is SESSION_TOOL_SPECS[tool_name].model
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_contracts.py tests/mcp/test_client_cli.py tests/mcp/test_schemas.py tests/mcp/test_handlers.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/mcp/schemas.py src/gdb_mcp/mcp/handlers.py src/gdb_mcp/client/input_parsers.py tests/mcp/test_contracts.py tests/mcp/test_client_cli.py
git commit -m "refactor: adopt shared contract values across MCP layers"
```

### Task 3: Run Full Verification And Final Contract Drift Review

**Files:**
- Verify only: full repository

**Testing approach:** `existing tests + targeted verification`
Reason: The earlier tasks carry the structural changes. This task proves the repo still behaves the same and confirms the obvious contract-drift hotspots are gone.

- [ ] **Step 1: Run the full repository verification**

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
git diff --check
```

- [ ] **Step 2: Inspect the remaining shared-value and tool-name duplication**

```bash
rg -n 'Literal\\["write", "read", "access"\\]|Literal\\["hex", "natural"\\]|Literal\\["assembly", "mixed"\\]|Literal\\["parent", "child"\\]' src
rg -n '"gdb_session_query"|"gdb_inferior_query"|"gdb_execution_manage"|"gdb_breakpoint_query"|"gdb_context_query"|"gdb_inspect_query"' src
```

Expected: remaining hits are limited to schema-local single-value discriminators, docs, or intentional user-facing examples rather than duplicated shared value sets.

- [ ] **Step 3: Capture the final changed surface for review**

```bash
git diff -- src/gdb_mcp/contracts.py src/gdb_mcp/client src/gdb_mcp/mcp tests/mcp
git show --stat --oneline HEAD~2..HEAD
```

- [ ] **Step 4: Run the post-change verification one more time**

Run: `uv run ruff check src tests && uv run mypy src && uv run pytest -q && git diff --check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "refactor: finalize shared contract deduplication"
```
