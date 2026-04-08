# Public Action And Workflow Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. If the user has expressed a strong preference for one of these execution styles, keep using that style unless they explicitly ask to switch. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Codex subagent-driven execution:** Subagents cannot stream partial progress back to the controller while still running. The controller should assign each subagent a unique shared progress file and inspect that file during execution when visibility is needed.

**Goal:** Centralize the remaining duplicated public action discriminator/result strings and the shared workflow-step validation rules without changing public MCP or CLI behavior.

**Architecture:** Extend `src/gdb_mcp/contracts.py` so it owns the remaining single-value public action/kind/location vocabulary plus a pure workflow-step admissibility helper. Then update `src/gdb_mcp/mcp/schemas.py`, `src/gdb_mcp/mcp/handlers.py`, and `src/gdb_mcp/client/input_parsers.py` to consume those shared contracts while preserving the existing request shapes, response envelopes, and user-facing error behavior.

**Tech Stack:** Python 3.10+, Pydantic v2, `argparse`, pytest, ruff, mypy.

---

### Task 1: Extend Shared Public Contracts For Actions And Workflow Rules

**Files:**
- Modify: `src/gdb_mcp/contracts.py`
- Modify: `tests/mcp/test_contracts.py`

**Testing approach:** `TDD`
Reason: `contracts.py` is a clean seam for introducing the missing single-value aliases and the pure workflow-step rule helper. A focused failing test first makes the new shared public contract explicit before any caller starts consuming it.

- [ ] **Step 1: Write failing contract tests for the new shared action aliases and workflow-step rule helper**

```python
# tests/mcp/test_contracts.py

from gdb_mcp.contracts import (
    ACTION_LIST,
    ACTION_STATUS,
    ACTION_STOP,
    ACTION_THREADS,
    ACTION_EVALUATE,
    BREAKPOINT_KIND_CODE,
    LOCATION_KIND_CURRENT,
    ActionEvaluateName,
    ActionListName,
    ActionStatusName,
    ActionStopName,
    ActionThreadsName,
    BreakpointKindCodeName,
    LocationKindCurrentName,
    TOOL_CONTEXT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
    WorkflowStepValidationIssue,
    validate_workflow_step_contract,
)


def test_shared_single_value_public_contract_aliases_match_runtime_values() -> None:
    assert get_args(ActionListName) == (ACTION_LIST,)
    assert get_args(ActionStatusName) == (ACTION_STATUS,)
    assert get_args(ActionStopName) == (ACTION_STOP,)
    assert get_args(ActionThreadsName) == (ACTION_THREADS,)
    assert get_args(ActionEvaluateName) == (ACTION_EVALUATE,)
    assert get_args(BreakpointKindCodeName) == (BREAKPOINT_KIND_CODE,)
    assert get_args(LocationKindCurrentName) == (LOCATION_KIND_CURRENT,)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        (
            TOOL_SESSION_QUERY,
            {"session_id": 9},
            WorkflowStepValidationIssue(
                kind="session_id_not_allowed",
                code="validation_error",
            ),
        ),
        (
            TOOL_SESSION_QUERY,
            {"action": ACTION_LIST},
            WorkflowStepValidationIssue(
                kind="session_query_list_not_allowed",
                code="unsupported_combination",
            ),
        ),
        (
            TOOL_SESSION_MANAGE,
            {"action": ACTION_STOP},
            WorkflowStepValidationIssue(
                kind="session_manage_not_allowed",
                code="unsupported_combination",
            ),
        ),
        (
            TOOL_WORKFLOW_BATCH,
            {},
            WorkflowStepValidationIssue(
                kind="nested_workflow_tool_not_allowed",
                code="unknown_tool",
            ),
        ),
        (
            TOOL_RUN_UNTIL_FAILURE,
            {},
            WorkflowStepValidationIssue(
                kind="nested_workflow_tool_not_allowed",
                code="unknown_tool",
            ),
        ),
        (TOOL_CONTEXT_QUERY, {"action": ACTION_THREADS}, None),
        (
            "gdb_not_a_real_tool",
            {},
            WorkflowStepValidationIssue(
                kind="unknown_tool",
                code="unknown_tool",
            ),
        ),
    ],
)
def test_validate_workflow_step_contract_matches_shared_public_rules(
    tool_name: str,
    arguments: dict[str, object],
    expected: WorkflowStepValidationIssue | None,
) -> None:
    assert validate_workflow_step_contract(tool_name, arguments) == expected
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `PYTHONPATH=src uv run pytest -q tests/mcp/test_contracts.py -k 'single_value_public_contract_aliases or workflow_step_contract_matches_shared_public_rules'`
Expected: FAIL because the single-value aliases and workflow-step helper do not exist yet in `src/gdb_mcp/contracts.py`.

- [ ] **Step 3: Implement the shared action aliases and workflow-step validation helper**

```python
# src/gdb_mcp/contracts.py

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

ACTION_LIST = "list"
ActionListName: TypeAlias = Literal["list"]
ACTION_STATUS = "status"
ActionStatusName: TypeAlias = Literal["status"]
ACTION_STOP = "stop"
ActionStopName: TypeAlias = Literal["stop"]
ACTION_CURRENT = "current"
ActionCurrentName: TypeAlias = Literal["current"]
ACTION_CREATE = "create"
ActionCreateName: TypeAlias = Literal["create"]
ACTION_REMOVE = "remove"
ActionRemoveName: TypeAlias = Literal["remove"]
ACTION_SELECT = "select"
ActionSelectName: TypeAlias = Literal["select"]
ACTION_SET_FOLLOW_FORK_MODE = "set_follow_fork_mode"
ActionSetFollowForkModeName: TypeAlias = Literal["set_follow_fork_mode"]
ACTION_SET_DETACH_ON_FORK = "set_detach_on_fork"
ActionSetDetachOnForkName: TypeAlias = Literal["set_detach_on_fork"]
ACTION_RUN = "run"
ActionRunName: TypeAlias = Literal["run"]
ACTION_CONTINUE = "continue"
ActionContinueName: TypeAlias = Literal["continue"]
ACTION_INTERRUPT = "interrupt"
ActionInterruptName: TypeAlias = Literal["interrupt"]
ACTION_STEP = "step"
ActionStepName: TypeAlias = Literal["step"]
ACTION_NEXT = "next"
ActionNextName: TypeAlias = Literal["next"]
ACTION_FINISH = "finish"
ActionFinishName: TypeAlias = Literal["finish"]
ACTION_WAIT_FOR_STOP = "wait_for_stop"
ActionWaitForStopName: TypeAlias = Literal["wait_for_stop"]
ACTION_UPDATE = "update"
ActionUpdateName: TypeAlias = Literal["update"]
ACTION_GET = "get"
ActionGetName: TypeAlias = Literal["get"]
ACTION_THREADS = "threads"
ActionThreadsName: TypeAlias = Literal["threads"]
ACTION_BACKTRACE = "backtrace"
ActionBacktraceName: TypeAlias = Literal["backtrace"]
ACTION_FRAME = "frame"
ActionFrameName: TypeAlias = Literal["frame"]
ACTION_SELECT_THREAD = "select_thread"
ActionSelectThreadName: TypeAlias = Literal["select_thread"]
ACTION_SELECT_FRAME = "select_frame"
ActionSelectFrameName: TypeAlias = Literal["select_frame"]
ACTION_EVALUATE = "evaluate"
ActionEvaluateName: TypeAlias = Literal["evaluate"]
ACTION_VARIABLES = "variables"
ActionVariablesName: TypeAlias = Literal["variables"]
ACTION_REGISTERS = "registers"
ActionRegistersName: TypeAlias = Literal["registers"]
ACTION_MEMORY = "memory"
ActionMemoryName: TypeAlias = Literal["memory"]
ACTION_DISASSEMBLY = "disassembly"
ActionDisassemblyName: TypeAlias = Literal["disassembly"]
ACTION_SOURCE = "source"
ActionSourceName: TypeAlias = Literal["source"]

BREAKPOINT_KIND_CODE = "code"
BreakpointKindCodeName: TypeAlias = Literal["code"]
BREAKPOINT_KIND_WATCH = "watch"
BreakpointKindWatchName: TypeAlias = Literal["watch"]
BREAKPOINT_KIND_CATCH = "catch"
BreakpointKindCatchName: TypeAlias = Literal["catch"]

LOCATION_KIND_CURRENT = "current"
LocationKindCurrentName: TypeAlias = Literal["current"]
LOCATION_KIND_FUNCTION = "function"
LocationKindFunctionName: TypeAlias = Literal["function"]
LOCATION_KIND_ADDRESS = "address"
LocationKindAddressName: TypeAlias = Literal["address"]
LOCATION_KIND_ADDRESS_RANGE = "address_range"
LocationKindAddressRangeName: TypeAlias = Literal["address_range"]
LOCATION_KIND_FILE_LINE = "file_line"
LocationKindFileLineName: TypeAlias = Literal["file_line"]
LOCATION_KIND_FILE_RANGE = "file_range"
LocationKindFileRangeName: TypeAlias = Literal["file_range"]

SESSION_QUERY_ACTIONS = (ACTION_LIST, ACTION_STATUS)
SESSION_MANAGE_ACTIONS = (ACTION_STOP,)
INFERIOR_QUERY_ACTIONS = (ACTION_LIST, ACTION_CURRENT)
INFERIOR_MANAGE_ACTIONS = (
    ACTION_CREATE,
    ACTION_REMOVE,
    ACTION_SELECT,
    ACTION_SET_FOLLOW_FORK_MODE,
    ACTION_SET_DETACH_ON_FORK,
)
EXECUTION_MANAGE_ACTIONS = (
    ACTION_RUN,
    ACTION_CONTINUE,
    ACTION_INTERRUPT,
    ACTION_STEP,
    ACTION_NEXT,
    ACTION_FINISH,
    ACTION_WAIT_FOR_STOP,
)
CONTEXT_QUERY_ACTIONS = (ACTION_THREADS, ACTION_BACKTRACE, ACTION_FRAME)
CONTEXT_MANAGE_ACTIONS = (ACTION_SELECT_THREAD, ACTION_SELECT_FRAME)
BREAKPOINT_KINDS = (BREAKPOINT_KIND_CODE, BREAKPOINT_KIND_WATCH, BREAKPOINT_KIND_CATCH)
BREAKPOINT_QUERY_ACTIONS = (ACTION_LIST, ACTION_GET)
BREAKPOINT_MANAGE_ACTIONS = (ACTION_CREATE, ACTION_UPDATE, "delete", "enable", "disable")
LOCATION_KINDS = (
    LOCATION_KIND_CURRENT,
    LOCATION_KIND_FUNCTION,
    LOCATION_KIND_ADDRESS,
    LOCATION_KIND_ADDRESS_RANGE,
    LOCATION_KIND_FILE_LINE,
    LOCATION_KIND_FILE_RANGE,
)
INSPECT_QUERY_ACTIONS = (
    ACTION_EVALUATE,
    ACTION_VARIABLES,
    ACTION_REGISTERS,
    ACTION_MEMORY,
    ACTION_DISASSEMBLY,
    ACTION_SOURCE,
)

WorkflowStepValidationKind: TypeAlias = Literal[
    "session_id_not_allowed",
    "session_query_list_not_allowed",
    "session_manage_not_allowed",
    "nested_workflow_tool_not_allowed",
    "unknown_tool",
]
WorkflowStepValidationCode: TypeAlias = Literal[
    "validation_error",
    "unsupported_combination",
    "unknown_tool",
]


@dataclass(frozen=True, slots=True)
class WorkflowStepValidationIssue:
    kind: WorkflowStepValidationKind
    code: WorkflowStepValidationCode


def validate_workflow_step_contract(
    tool_name: str,
    arguments: Mapping[str, object],
) -> WorkflowStepValidationIssue | None:
    if "session_id" in arguments:
        return WorkflowStepValidationIssue(
            kind="session_id_not_allowed",
            code="validation_error",
        )
    if tool_name == TOOL_SESSION_QUERY and arguments.get("action") == ACTION_LIST:
        return WorkflowStepValidationIssue(
            kind="session_query_list_not_allowed",
            code="unsupported_combination",
        )
    if tool_name == TOOL_SESSION_MANAGE:
        return WorkflowStepValidationIssue(
            kind="session_manage_not_allowed",
            code="unsupported_combination",
        )
    if tool_name in {TOOL_WORKFLOW_BATCH, TOOL_RUN_UNTIL_FAILURE}:
        return WorkflowStepValidationIssue(
            kind="nested_workflow_tool_not_allowed",
            code="unknown_tool",
        )
    if tool_name not in BATCH_STEP_TOOL_NAMES:
        return WorkflowStepValidationIssue(
            kind="unknown_tool",
            code="unknown_tool",
        )
    return None
```

- [ ] **Step 4: Run the post-change verification**

Run: `PYTHONPATH=src uv run pytest -q tests/mcp/test_contracts.py -k 'single_value_public_contract_aliases or workflow_step_contract_matches_shared_public_rules'`
Expected: PASS with the new shared aliases and helper exported from `src/gdb_mcp/contracts.py`.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/contracts.py tests/mcp/test_contracts.py
git commit -m "refactor: add shared action and workflow contracts"
```

### Task 2: Adopt Shared Contracts Across Schemas, Handlers, And CLI Workflow Parsing

**Files:**
- Modify: `src/gdb_mcp/mcp/schemas.py`
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/client/input_parsers.py`
- Modify: `tests/mcp/test_contracts.py`
- Modify: `tests/mcp/test_client_cli.py`
- Modify: `tests/mcp/test_handlers.py`

**Testing approach:** `existing tests + targeted verification`
Reason: This task is structural cleanup over behavior that already exists. The safest proof is to preserve the current focused CLI/server behavior tests while extending the contract drift tests around the shared source.

- [ ] **Step 1: Capture the current focused behavior before rewiring the callers**

```bash
PYTHONPATH=src uv run pytest -q \
  tests/mcp/test_contracts.py \
  tests/mcp/test_client_cli.py -k 'workflow_batch_step_session_id_argument or run_until_failure_invalid_setup_step' \
  tests/mcp/test_handlers.py -k 'workflow_batch_rejects_step_level_session_id or tool_definitions_match_dispatch_registry'
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `PYTHONPATH=src uv run pytest -q tests/mcp/test_contracts.py tests/mcp/test_client_cli.py -k 'workflow_batch_step_session_id_argument or run_until_failure_invalid_setup_step' tests/mcp/test_handlers.py -k 'workflow_batch_rejects_step_level_session_id or tool_definitions_match_dispatch_registry'`
Expected: PASS on the current tree, establishing the baseline behavior that must be preserved.

- [ ] **Step 3: Rewire schemas, handlers, and the CLI parser to consume the shared contracts**

```python
# src/gdb_mcp/mcp/schemas.py

from ..contracts import (
    ActionBacktraceName,
    ActionContinueName,
    ActionCreateName,
    ActionCurrentName,
    ActionDisassemblyName,
    ActionEvaluateName,
    ActionFinishName,
    ActionFrameName,
    ActionGetName,
    ActionInterruptName,
    ActionListName,
    ActionMemoryName,
    ActionNextName,
    ActionRegistersName,
    ActionRemoveName,
    ActionRunName,
    ActionSelectFrameName,
    ActionSelectName,
    ActionSelectThreadName,
    ActionSetDetachOnForkName,
    ActionSetFollowForkModeName,
    ActionSourceName,
    ActionStatusName,
    ActionStepName,
    ActionStopName,
    ActionThreadsName,
    ActionUpdateName,
    ActionVariablesName,
    ActionWaitForStopName,
    BreakpointKindCatchName,
    BreakpointKindCodeName,
    BreakpointKindWatchName,
    LocationKindAddressName,
    LocationKindAddressRangeName,
    LocationKindCurrentName,
    LocationKindFileLineName,
    LocationKindFileRangeName,
    LocationKindFunctionName,
)


class SessionQueryListAction(StrictArgsModel):
    action: ActionListName = Field(..., description="List all active sessions")


class SessionQueryStatusAction(StrictArgsModel):
    action: ActionStatusName = Field(..., description="Query one live session")


class ExecutionRunAction(StrictArgsModel):
    action: ActionRunName = Field(..., description="Start the inferior")


class BreakpointCodeCreateArgs(StrictArgsModel):
    kind: BreakpointKindCodeName = Field(..., description="Create a code breakpoint")


class LocationCurrentArgs(StrictArgsModel):
    kind: LocationKindCurrentName = Field(..., description="Use the current selected location")
```

```python
# src/gdb_mcp/mcp/handlers.py

from ..contracts import validate_workflow_step_contract


def _workflow_step_validation_error(
    tool_name: str,
    issue: WorkflowStepValidationIssue,
    *,
    index: int,
) -> OperationError:
    if issue.kind == "session_id_not_allowed":
        return OperationError(
            message=(
                f"Batch step {index} ({tool_name}) must not include session_id. "
                f"It is inherited from {TOOL_WORKFLOW_BATCH}."
            ),
            code=issue.code,
        )
    if issue.kind == "session_query_list_not_allowed":
        return OperationError(
            message=f"{TOOL_SESSION_QUERY}(action=list) is not valid inside {TOOL_WORKFLOW_BATCH}",
            code=issue.code,
        )
    if issue.kind == "session_manage_not_allowed":
        return OperationError(
            message=f"{TOOL_SESSION_MANAGE} is not valid inside {TOOL_WORKFLOW_BATCH}",
            code=issue.code,
        )
    return OperationError(
        message=f"Unsupported batch step tool: {tool_name}",
        code=issue.code,
    )


if isinstance(action_args, InferiorQueryListAction):
    return _wrap_action_result(action_args.action, session.list_inferiors())

if isinstance(action_args, ContextQueryThreadsAction):
    return _wrap_action_result(action_args.action, session.get_threads())

issue = validate_workflow_step_contract(step.tool, step.arguments)
if issue is not None:
    return _workflow_step_validation_error(step.tool, issue, index=index)
```

```python
# src/gdb_mcp/client/input_parsers.py

from gdb_mcp.contracts import validate_workflow_step_contract


def _workflow_step_cli_error(
    tool_name: str,
    issue: WorkflowStepValidationIssue,
    *,
    index: int,
) -> CliUsageError:
    if issue.kind == "session_id_not_allowed":
        return CliUsageError(
            f"Workflow step {index} ({tool_name}) must not include session_id; "
            "it is inherited from the enclosing command"
        )
    if issue.kind == "session_query_list_not_allowed":
        return CliUsageError(f"{TOOL_SESSION_QUERY}(action=list) is not valid inside workflow steps")
    if issue.kind == "session_manage_not_allowed":
        return CliUsageError(f"{TOOL_SESSION_MANAGE} is not valid inside workflow steps")
    if issue.kind == "nested_workflow_tool_not_allowed":
        return CliUsageError(f"{tool_name} is not valid inside workflow steps")
    return CliUsageError(f"Unsupported workflow step tool: {tool_name}")


def _validate_workflow_step(
    tool_name: str,
    arguments: dict[str, object],
    *,
    index: int,
) -> dict[str, object]:
    issue = validate_workflow_step_contract(tool_name, arguments)
    if issue is not None:
        raise _workflow_step_cli_error(tool_name, issue, index=index)
    model = BATCH_STEP_TOOL_MODELS[tool_name]
    validated = validate_model_payload_with_list_coercion(
        model,
        {"session_id": 1, **arguments},
    )
    validated.pop("session_id", None)
    return validated
```

```python
# tests/mcp/test_contracts.py

def _const_value(model: type[BaseModel], field_name: str) -> str:
    return str(model.model_json_schema()["properties"][field_name]["const"])


def test_shared_schema_const_fields_match_public_contract_values() -> None:
    assert _const_value(SessionQueryListAction, "action") == ACTION_LIST
    assert _const_value(SessionQueryStatusAction, "action") == ACTION_STATUS
    assert _const_value(ExecutionRunAction, "action") == ACTION_RUN
    assert _const_value(ContextQueryThreadsAction, "action") == ACTION_THREADS
    assert _const_value(InspectEvaluateAction, "action") == ACTION_EVALUATE
    assert _const_value(BreakpointCodeCreateArgs, "kind") == BREAKPOINT_KIND_CODE
    assert _const_value(LocationCurrentArgs, "kind") == LOCATION_KIND_CURRENT
```

- [ ] **Step 4: Run the post-change verification**

Run: `PYTHONPATH=src uv run pytest -q tests/mcp/test_contracts.py tests/mcp/test_client_cli.py -k 'workflow_batch_step_session_id_argument or run_until_failure_invalid_setup_step' tests/mcp/test_handlers.py -k 'workflow_batch_rejects_step_level_session_id or tool_definitions_match_dispatch_registry'`
Expected: PASS with the same user-visible behavior, plus the new contract drift checks around shared action/kind constants.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/mcp/schemas.py src/gdb_mcp/mcp/handlers.py src/gdb_mcp/client/input_parsers.py tests/mcp/test_contracts.py tests/mcp/test_client_cli.py tests/mcp/test_handlers.py
git commit -m "refactor: centralize public action and workflow validation contracts"
```

### Final Verification

After both tasks are complete, run the repository validation required by `AGENTS.md` from the worktree root:

```bash
PYTHONPATH=src uv run ruff check src tests
PYTHONPATH=src uv run mypy src
PYTHONPATH=src uv run pytest -q
git diff --check
```

If any command fails, fix the failures in the current branch before requesting the final whole-implementation review.
