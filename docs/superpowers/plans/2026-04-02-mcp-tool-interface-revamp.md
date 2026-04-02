# MCP Tool Interface Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy 42-tool MCP surface with the approved 17-tool domain API, strict action-scoped payloads, uniform success/error envelopes, and updated workflow, docs, and tests.

**Architecture:** Keep `SessionService` and the existing session collaborators as the behavioral backend, but rebuild the MCP boundary around action-based schemas and per-domain handlers. Wrap existing domain results in v2 envelopes, extend only the service seams that need new context or update actions, and migrate workflow tools and docs to the new inventory in the same change.

**Tech Stack:** Python 3.10+, Pydantic v2, MCP runtime in `src/gdb_mcp/mcp/`, typed domain models in `src/gdb_mcp/domain/`, session services in `src/gdb_mcp/session/`, pytest, ruff, mypy.

---

### File Structure

**Core files and responsibilities**

- Modify: `src/gdb_mcp/domain/results.py`
  Change serialized error behavior to emit stable `code` and nested `details`, while keeping success payloads compatible with explicit v2 envelopes.
- Modify: `src/gdb_mcp/domain/models.py`
  Extend workflow result records so batch steps can report `action` and `code` alongside the wrapped result payload.
- Modify: `src/gdb_mcp/mcp/serializer.py`
  Serialize unexpected exceptions as v2 `internal_error` responses and preserve the new error envelope contract.
- Modify: `src/gdb_mcp/mcp/schemas.py`
  Replace the per-operation public tool inventory with consolidated action-scoped request models, batch-step allowlists, and v2 tool definitions.
- Modify: `src/gdb_mcp/mcp/handlers.py`
  Replace the legacy one-name-per-operation dispatch map with per-domain action dispatch, response wrapping, and v2 batch-step validation.
- Modify: `src/gdb_mcp/mcp/__init__.py`
  Export the consolidated schema classes and remove exports that only make sense for the legacy tool surface.
- Modify: `src/gdb_mcp/session/service.py`
  Extend the service facade with any new methods or optional parameters needed by consolidated actions, especially frame-context lookup and breakpoint update/get.
- Modify: `src/gdb_mcp/session/execution.py`
  Add optional wait-policy parameters for execution commands and keep campaign callers aligned with the new signature.
- Modify: `src/gdb_mcp/session/inspection.py`
  Extend current-frame inspection so `gdb_context_query(action="frame")` can operate on optional thread/frame overrides without mutating selection.
- Modify: `src/gdb_mcp/session/breakpoints.py`
  Add `get_breakpoint()` and `update_breakpoint()` so the MCP layer does not reimplement breakpoint-specific GDB command behavior.
- Modify: `src/gdb_mcp/session/workflow.py`
  Preserve `action` and `code` in batch step summaries produced from wrapped v2 results.
- Modify: `src/gdb_mcp/session/campaign.py`
  Update run invocation if execution-control signatures change and keep setup-step execution aligned with the new batch-step validation rules.
- Modify: `tests/mcp/test_serializer.py`
  Lock in the v2 error envelope and exception serialization behavior.
- Modify: `tests/mcp/test_schemas.py`
  Cover the consolidated action models, root-model validation, location unions, and batch-step allowlists.
- Modify: `tests/mcp/test_handlers.py`
  Verify per-tool/per-action routing, wrapped response shapes, and workflow-step validation.
- Modify: `tests/mcp/test_runtime.py`
  Update the runtime smoke test to call a v2 tool name.
- Modify: `tests/integration/conftest.py`
  Rename fixture cleanup and any helper calls that still use legacy tool names.
- Modify: `tests/integration/test_gdb_integration.py`
  Rewrite end-to-end flows around the new session, execution, breakpoint, context, inspect, and workflow tool names.
- Modify: `tests/integration/test_multi_session.py`
  Rewrite session inventory and per-session status assertions to use `gdb_session_query` and `gdb_session_manage`.
- Modify: `README.md`
  Rewrite the tool inventory and examples for the new domain-oriented surface.
- Modify: `TOOLS.md`
  Replace the old per-tool sections with v2 action tables, request examples, response examples, and the migration appendix.

### Task 1: Add V2 Result Envelopes

**Files:**
- Modify: `src/gdb_mcp/domain/results.py`
- Modify: `src/gdb_mcp/mcp/serializer.py`
- Test/Verify: `tests/mcp/test_serializer.py`

**Testing approach:** `TDD`
Reason: The envelope contract is small, deterministic, and independent of the larger schema rewrite. Locking it in first gives later handler work a stable target.

- [ ] **Step 1: Write failing serializer tests for the new success and error envelopes**

```python
# tests/mcp/test_serializer.py

class TestMcpSerializer:
    def test_result_to_payload_preserves_v2_success_envelope(self):
        payload = result_to_payload(
            OperationSuccess(
                {
                    "action": "status",
                    "result": {
                        "session": {
                            "execution_state": "paused",
                        }
                    },
                }
            )
        )

        assert payload == {
            "status": "success",
            "action": "status",
            "result": {
                "session": {
                    "execution_state": "paused",
                }
            },
        }

    def test_result_to_payload_includes_error_code_and_nested_details(self):
        payload = result_to_payload(
            OperationError(
                message="breakpoint.location is required for kind=code",
                code="validation_error",
                details={
                    "action": "create",
                    "field_errors": [
                        {
                            "field": "breakpoint.location",
                            "issue": "missing",
                        }
                    ],
                },
            )
        )

        assert payload == {
            "status": "error",
            "code": "validation_error",
            "message": "breakpoint.location is required for kind=code",
            "action": "create",
            "details": {
                "field_errors": [
                    {
                        "field": "breakpoint.location",
                        "issue": "missing",
                    }
                ]
            },
        }

    def test_serialize_exception_uses_internal_error_code(self):
        contents = serialize_exception("gdb_session_query", RuntimeError("bad"))
        payload = json.loads(contents[0].text)

        assert payload == {
            "status": "error",
            "code": "internal_error",
            "message": "bad",
            "tool": "gdb_session_query",
        }
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_serializer.py -k 'v2_success_envelope or error_code_and_nested_details or internal_error_code'`
Expected: FAIL because `result_to_mapping()` still flattens error details into the top level and `serialize_exception()` does not emit `code="internal_error"`.

- [ ] **Step 3: Implement the result-envelope changes**

```python
# src/gdb_mcp/domain/results.py

_RESERVED_TOP_LEVEL_ERROR_KEYS = {"action", "tool"}


def result_to_mapping(result: OperationResult[object]) -> StructuredPayload:
    if isinstance(result, OperationSuccess):
        serialized_value = payload_to_mapping(result.value)
        if isinstance(serialized_value, dict):
            payload: StructuredPayload = dict(serialized_value)
        else:
            payload = {"result": serialized_value}
        payload.setdefault("status", "success")
        if result.warnings and "warnings" not in payload:
            payload["warnings"] = list(result.warnings)
        return payload

    error_payload: StructuredPayload = {
        "status": "error",
        "code": result.code,
        "message": result.message,
    }
    if result.fatal:
        error_payload["fatal"] = True

    details_payload = payload_to_mapping(result.details)
    if isinstance(details_payload, dict):
        details_mapping: StructuredPayload = dict(details_payload)
        for key in _RESERVED_TOP_LEVEL_ERROR_KEYS:
            if key in details_mapping:
                error_payload[key] = details_mapping.pop(key)
        if details_mapping:
            error_payload["details"] = details_mapping

    return error_payload
```

```python
# src/gdb_mcp/mcp/serializer.py

def serialize_exception(tool_name: str, exc: Exception) -> list[TextContent]:
    error_result = OperationError(
        message=str(exc),
        code="internal_error",
        details={"tool": tool_name},
    )
    return serialize_result(error_result)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_serializer.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_serializer.py src/gdb_mcp/domain/results.py src/gdb_mcp/mcp/serializer.py
git commit -m "refactor: add v2 MCP result envelopes"
```

### Task 2: Replace The Public Schema Inventory With V2 Action Models

**Files:**
- Modify: `src/gdb_mcp/mcp/schemas.py`
- Modify: `src/gdb_mcp/mcp/__init__.py`
- Test/Verify: `tests/mcp/test_schemas.py`

**Testing approach:** `TDD`
Reason: The clean-break tool surface is mostly a schema rewrite. The safest way to drive it is with validation tests for the new inventory, root-model action unions, and batch-step allowlists before touching handlers.

- [ ] **Step 1: Write failing schema tests for the consolidated tool models**

```python
# tests/mcp/test_schemas.py

class TestV2ToolDefinitions:
    def test_build_tool_definitions_exports_v2_inventory(self):
        tool_names = {tool.name for tool in build_tool_definitions()}

        assert tool_names == {
            "gdb_session_start",
            "gdb_session_query",
            "gdb_session_manage",
            "gdb_inferior_query",
            "gdb_inferior_manage",
            "gdb_execution_manage",
            "gdb_breakpoint_query",
            "gdb_breakpoint_manage",
            "gdb_context_query",
            "gdb_context_manage",
            "gdb_inspect_query",
            "gdb_workflow_batch",
            "gdb_capture_bundle",
            "gdb_run_until_failure",
            "gdb_execute_command",
            "gdb_attach_process",
            "gdb_call_function",
        }


class TestSessionQueryArgs:
    def test_list_and_status_have_different_shapes(self):
        list_args = SessionQueryArgs.model_validate({"action": "list", "query": {}})
        status_args = SessionQueryArgs.model_validate(
            {"session_id": 3, "action": "status", "query": {}}
        )

        assert list_args.root.action == "list"
        assert status_args.root.action == "status"
        assert status_args.root.session_id == 3

        with pytest.raises(ValidationError):
            SessionQueryArgs.model_validate({"action": "status", "query": {}})


class TestBreakpointManageArgs:
    def test_create_accepts_watchpoint_shape(self):
        args = BreakpointManageArgs.model_validate(
            {
                "session_id": 4,
                "action": "create",
                "breakpoint": {
                    "kind": "watch",
                    "expression": "state->ready",
                    "access": "read",
                },
            }
        )

        assert args.root.action == "create"
        assert args.root.breakpoint.kind == "watch"
        assert args.root.breakpoint.access == "read"

    def test_update_rejects_missing_changes(self):
        with pytest.raises(ValidationError):
            BreakpointManageArgs.model_validate(
                {
                    "session_id": 4,
                    "action": "update",
                    "breakpoint": {
                        "number": 2,
                    },
                }
            )


class TestInspectQueryArgs:
    def test_source_accepts_file_range_location(self):
        args = InspectQueryArgs.model_validate(
            {
                "session_id": 9,
                "action": "source",
                "query": {
                    "location": {
                        "kind": "file_range",
                        "file": "main.c",
                        "start_line": 10,
                        "end_line": 12,
                    },
                    "context_before": 0,
                    "context_after": 0,
                },
            }
        )

        assert args.root.action == "source"
        assert args.root.query.location.kind == "file_range"


class TestWorkflowBatchArgs:
    def test_batch_steps_accept_new_tool_names(self):
        step = BatchStepArgs(
            tool="gdb_breakpoint_manage",
            arguments={
                "action": "disable",
                "breakpoint": {"number": 3},
            },
        )

        assert step.tool == "gdb_breakpoint_manage"

    def test_batch_steps_reject_legacy_tool_names(self):
        with pytest.raises(ValidationError):
            BatchStepArgs(tool="gdb_get_status", arguments={})
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_schemas.py -k 'V2ToolDefinitions or SessionQueryArgs or BreakpointManageArgs or InspectQueryArgs or WorkflowBatchArgs'`
Expected: FAIL because the new root-model schemas, tool names, and batch-step allowlist do not exist yet.

- [ ] **Step 3: Implement the consolidated schema layer**

```python
# src/gdb_mcp/mcp/schemas.py

from typing import Annotated, Literal

from pydantic import RootModel


class EmptyQuery(StrictArgsModel):
    pass


class SessionQueryListAction(StrictArgsModel):
    action: Literal["list"]
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionQueryStatusAction(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_session_start")
    action: Literal["status"]
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionQueryArgs(
    RootModel[
        Annotated[
            SessionQueryListAction | SessionQueryStatusAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class SessionManageStopAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["stop"]
    session: EmptyQuery = Field(default_factory=EmptyQuery)


class SessionManageArgs(
    RootModel[
        Annotated[
            SessionManageStopAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class ExecutionWait(StrictArgsModel):
    until: Literal["acknowledged", "stop"] = Field("stop")
    timeout_sec: int | None = Field(None, gt=0)


class ExecutionRunPayload(StrictArgsModel):
    args: list[str] | str | None = Field(None)
    wait: ExecutionWait | None = Field(None)


class ExecutionControlPayload(StrictArgsModel):
    wait: ExecutionWait | None = Field(None)


class ExecutionWaitForStopPayload(StrictArgsModel):
    timeout_sec: int = Field(30, gt=0)
    stop_reasons: list[str] = Field(default_factory=list)


class ExecutionRunAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["run"]
    execution: ExecutionRunPayload


class ExecutionContinueAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["continue"]
    execution: ExecutionControlPayload


class ExecutionInterruptAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["interrupt"]
    execution: EmptyQuery = Field(default_factory=EmptyQuery)


class ExecutionStepAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["step"]
    execution: ExecutionControlPayload


class ExecutionNextAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["next"]
    execution: ExecutionControlPayload


class ExecutionFinishAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["finish"]
    execution: ExecutionControlPayload


class ExecutionWaitForStopAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["wait_for_stop"]
    execution: ExecutionWaitForStopPayload


class ExecutionManageArgs(
    RootModel[
        Annotated[
            ExecutionRunAction
            | ExecutionContinueAction
            | ExecutionInterruptAction
            | ExecutionStepAction
            | ExecutionNextAction
            | ExecutionFinishAction
            | ExecutionWaitForStopAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class BreakpointCodeCreate(StrictArgsModel):
    kind: Literal["code"]
    location: str = Field(..., description="Function, file:line, or *address")
    condition: str | None = Field(None, description="Optional breakpoint condition")
    temporary: bool = Field(False, description="Temporary breakpoint flag")


class BreakpointWatchCreate(StrictArgsModel):
    kind: Literal["watch"]
    expression: str = Field(..., description="Expression to watch")
    access: Literal["write", "read", "access"] = Field("write")


class BreakpointCatchCreate(StrictArgsModel):
    kind: Literal["catch"]
    event: Literal["throw", "rethrow", "catch", "exec", "fork", "vfork", "load", "unload", "signal", "syscall"]
    argument: str | None = Field(None, description="Optional catchpoint filter")
    temporary: bool = Field(False, description="Temporary catchpoint flag")


BreakpointCreatePayload = Annotated[
    BreakpointCodeCreate | BreakpointWatchCreate | BreakpointCatchCreate,
    Field(discriminator="kind"),
]


class BreakpointManageCreateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["create"]
    breakpoint: BreakpointCreatePayload


class BreakpointUpdateChanges(StrictArgsModel):
    condition: str | None = Field(None)
    clear_condition: bool = Field(False)

    @model_validator(mode="after")
    def validate_requested_change(self) -> "BreakpointUpdateChanges":
        if self.condition is None and self.clear_condition is False:
            raise ValueError("At least one breakpoint change is required")
        if self.condition is not None and self.clear_condition:
            raise ValueError("condition and clear_condition are mutually exclusive")
        return self


class BreakpointManageUpdateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["update"]
    breakpoint: BreakpointNumberArgs = Field(..., description="Breakpoint number selector")
    changes: BreakpointUpdateChanges


class BreakpointManageNumberAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["delete", "enable", "disable"]
    breakpoint: BreakpointNumberArgs


class BreakpointManageArgs(
    RootModel[
        Annotated[
            BreakpointManageCreateAction
            | BreakpointManageUpdateAction
            | BreakpointManageNumberAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class BreakpointQueryListAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["list"]
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class BreakpointGetQuery(StrictArgsModel):
    number: int = Field(..., gt=0)


class BreakpointQueryGetAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["get"]
    query: BreakpointGetQuery


class BreakpointQueryArgs(
    RootModel[
        Annotated[
            BreakpointQueryListAction | BreakpointQueryGetAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class LocationCurrent(StrictArgsModel):
    kind: Literal["current"]


class LocationFunction(StrictArgsModel):
    kind: Literal["function"]
    function: str


class LocationAddress(StrictArgsModel):
    kind: Literal["address"]
    address: str


class LocationAddressRange(StrictArgsModel):
    kind: Literal["address_range"]
    start_address: str
    end_address: str


class LocationFileLine(StrictArgsModel):
    kind: Literal["file_line"]
    file: str
    line: int = Field(..., gt=0)


class LocationFileRange(StrictArgsModel):
    kind: Literal["file_range"]
    file: str
    start_line: int = Field(..., gt=0)
    end_line: int = Field(..., gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "LocationFileRange":
        if self.start_line > self.end_line:
            raise ValueError("start_line must be <= end_line")
        return self


LocationSelector = Annotated[
    LocationCurrent
    | LocationFunction
    | LocationAddress
    | LocationAddressRange
    | LocationFileLine
    | LocationFileRange,
    Field(discriminator="kind"),
]


class InferiorQueryListAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["list"]
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class InferiorQueryCurrentAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["current"]
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class InferiorQueryArgs(
    RootModel[
        Annotated[
            InferiorQueryListAction | InferiorQueryCurrentAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class InferiorCreatePayload(StrictArgsModel):
    executable: str | None = Field(None)
    make_current: bool = Field(False)


class InferiorSelectPayload(StrictArgsModel):
    inferior_id: int = Field(..., gt=0)


class FollowForkPayload(StrictArgsModel):
    mode: Literal["parent", "child"]


class DetachOnForkPayload(StrictArgsModel):
    enabled: bool


class InferiorManageCreateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["create"]
    inferior: InferiorCreatePayload


class InferiorManageRemoveAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["remove"]
    inferior: InferiorSelectPayload


class InferiorManageSelectAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["select"]
    inferior: InferiorSelectPayload


class InferiorManageFollowForkAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["set_follow_fork_mode"]
    inferior: FollowForkPayload


class InferiorManageDetachOnForkAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["set_detach_on_fork"]
    inferior: DetachOnForkPayload


class InferiorManageArgs(
    RootModel[
        Annotated[
            InferiorManageCreateAction
            | InferiorManageRemoveAction
            | InferiorManageSelectAction
            | InferiorManageFollowForkAction
            | InferiorManageDetachOnForkAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class ThreadFrameContext(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0)
    frame: int | None = Field(None, ge=0)


class ContextBacktraceQuery(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0)
    max_frames: int = Field(100, gt=0)


class ContextFrameQuery(StrictArgsModel):
    thread_id: int | None = Field(None, gt=0)
    frame: int | None = Field(None, ge=0)


class ContextQueryThreadsAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["threads"]
    query: EmptyQuery = Field(default_factory=EmptyQuery)


class ContextQueryBacktraceAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["backtrace"]
    query: ContextBacktraceQuery


class ContextQueryFrameAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["frame"]
    query: ContextFrameQuery


class ContextQueryArgs(
    RootModel[
        Annotated[
            ContextQueryThreadsAction
            | ContextQueryBacktraceAction
            | ContextQueryFrameAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class ContextManageSelectThreadAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["select_thread"]
    context: ThreadSelectArgs


class ContextManageSelectFrameAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["select_frame"]
    context: FrameSelectArgs


class ContextManageArgs(
    RootModel[
        Annotated[
            ContextManageSelectThreadAction | ContextManageSelectFrameAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class InspectEvaluateQuery(StrictArgsModel):
    context: ThreadFrameContext | None = Field(None)
    expression: str


class InspectVariablesQuery(StrictArgsModel):
    context: ThreadFrameContext | None = Field(None)


class InspectRegistersQuery(StrictArgsModel):
    context: ThreadFrameContext | None = Field(None)
    register_numbers: list[int] = Field(default_factory=list)
    register_names: list[str] = Field(default_factory=list)
    include_vector_registers: bool = Field(True)
    max_registers: int | None = Field(None, gt=0)
    value_format: Literal["hex", "natural"] = Field("hex")


class InspectMemoryQuery(StrictArgsModel):
    address: str
    count: int = Field(..., gt=0)
    offset: int = Field(0, ge=0)


class InspectDisassemblyQuery(StrictArgsModel):
    context: ThreadFrameContext | None = Field(None)
    location: LocationSelector
    instruction_count: int = Field(32, gt=0)
    mode: Literal["assembly", "mixed"] = Field("mixed")


class InspectSourceQuery(StrictArgsModel):
    context: ThreadFrameContext | None = Field(None)
    location: LocationSelector
    context_before: int = Field(5, ge=0)
    context_after: int = Field(5, ge=0)


class InspectEvaluateAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["evaluate"]
    query: InspectEvaluateQuery


class InspectVariablesAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["variables"]
    query: InspectVariablesQuery


class InspectRegistersAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["registers"]
    query: InspectRegistersQuery


class InspectMemoryAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["memory"]
    query: InspectMemoryQuery


class InspectDisassemblyAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["disassembly"]
    query: InspectDisassemblyQuery


class InspectSourceAction(StrictArgsModel):
    session_id: int = Field(..., gt=0)
    action: Literal["source"]
    query: InspectSourceQuery


class InspectQueryArgs(
    RootModel[
        Annotated[
            InspectEvaluateAction
            | InspectVariablesAction
            | InspectRegistersAction
            | InspectMemoryAction
            | InspectDisassemblyAction
            | InspectSourceAction,
            Field(discriminator="action"),
        ]
    ]
):
    pass


BATCH_STEP_TOOL_NAMES = (
    "gdb_execution_manage",
    "gdb_inferior_query",
    "gdb_inferior_manage",
    "gdb_breakpoint_query",
    "gdb_breakpoint_manage",
    "gdb_context_query",
    "gdb_context_manage",
    "gdb_inspect_query",
    "gdb_execute_command",
    "gdb_attach_process",
    "gdb_call_function",
    "gdb_capture_bundle",
    "gdb_session_query",
)


def build_tool_definitions() -> list[Tool]:
    return [
        Tool(name="gdb_session_start", description="Start a new debugger session.", inputSchema=StartSessionArgs.model_json_schema()),
        Tool(name="gdb_session_query", description="Query sessions or one live session.", inputSchema=SessionQueryArgs.model_json_schema()),
        Tool(name="gdb_session_manage", description="Mutate session lifecycle state.", inputSchema=SessionManageArgs.model_json_schema()),
        Tool(name="gdb_inferior_query", description="Query inferiors in one live session.", inputSchema=InferiorQueryArgs.model_json_schema()),
        Tool(name="gdb_inferior_manage", description="Mutate inferiors and fork-follow settings.", inputSchema=InferiorManageArgs.model_json_schema()),
        Tool(name="gdb_execution_manage", description="Run, continue, interrupt, step, finish, or wait for stop.", inputSchema=ExecutionManageArgs.model_json_schema()),
        Tool(name="gdb_breakpoint_query", description="List or fetch one breakpoint record.", inputSchema=BreakpointQueryArgs.model_json_schema()),
        Tool(name="gdb_breakpoint_manage", description="Create, delete, enable, disable, or update breakpoints.", inputSchema=BreakpointManageArgs.model_json_schema()),
        Tool(name="gdb_context_query", description="Query threads, backtraces, or frame info.", inputSchema=ContextQueryArgs.model_json_schema()),
        Tool(name="gdb_context_manage", description="Select the current thread or frame.", inputSchema=ContextManageArgs.model_json_schema()),
        Tool(name="gdb_inspect_query", description="Evaluate expressions and inspect memory, source, disassembly, variables, or registers.", inputSchema=InspectQueryArgs.model_json_schema()),
        Tool(name="gdb_workflow_batch", description="Execute a structured batch of session-scoped v2 tools.", inputSchema=BatchArgs.model_json_schema()),
        Tool(name="gdb_capture_bundle", description="Capture forensic debugger artifacts to disk.", inputSchema=CaptureBundleArgs.model_json_schema()),
        Tool(name="gdb_run_until_failure", description="Run fresh sessions until a failure predicate matches.", inputSchema=RunUntilFailureArgs.model_json_schema()),
        Tool(name="gdb_execute_command", description="Execute a raw GDB command as an escape hatch.", inputSchema=ExecuteCommandArgs.model_json_schema()),
        Tool(name="gdb_attach_process", description="Attach to a running process by PID.", inputSchema=AttachProcessArgs.model_json_schema()),
        Tool(name="gdb_call_function", description="Call a function inside the target process.", inputSchema=CallFunctionArgs.model_json_schema()),
    ]
```

```python
# src/gdb_mcp/mcp/__init__.py

from .schemas import (
    BatchArgs,
    BatchStepArgs,
    BreakpointManageArgs,
    BreakpointQueryArgs,
    ContextManageArgs,
    ContextQueryArgs,
    ExecutionManageArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    InspectQueryArgs,
    SessionManageArgs,
    SessionQueryArgs,
    build_tool_definitions,
)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_schemas.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_schemas.py src/gdb_mcp/mcp/schemas.py src/gdb_mcp/mcp/__init__.py
git commit -m "refactor: define v2 MCP schema inventory"
```

### Task 3: Route Session And Execution Tools Through V2 Actions

**Files:**
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/session/service.py`
- Modify: `src/gdb_mcp/session/execution.py`
- Modify: `tests/mcp/test_handlers.py`
- Modify: `tests/mcp/test_runtime.py`

**Testing approach:** `TDD`
Reason: Session start/query/manage and execution control are the highest-traffic entry points. Replacing them first creates the v2 dispatch pattern the other domains can follow.

- [ ] **Step 1: Write failing handler and runtime tests for v2 session and execution routing**

```python
# tests/mcp/test_handlers.py

class TestHandlerDispatch:
    def test_session_query_list_routes_to_registry(self):
        manager = create_session_manager_mock()
        manager.list_sessions.return_value = OperationSuccess(
            SessionListInfo(sessions=[], count=0)
        )

        result_data = dispatch(
            "gdb_session_query",
            {"action": "list", "query": {}},
            manager,
        )

        manager.list_sessions.assert_called_once()
        assert result_data["status"] == "success"
        assert result_data["action"] == "list"

    def test_session_manage_stop_routes_to_registry_close(self):
        manager = create_session_manager_mock()
        manager.close_session.return_value = OperationSuccess(SessionMessage(message="closed"))

        result_data = dispatch(
            "gdb_session_manage",
            {"session_id": 1, "action": "stop", "session": {}},
            manager,
        )

        manager.close_session.assert_called_once_with(1)
        assert result_data["action"] == "stop"

    def test_execution_manage_run_routes_wait_policy(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.run.return_value = OperationSuccess(SessionMessage(message="started"))

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 3,
                "action": "run",
                "execution": {
                    "args": ["--mode", "fast"],
                    "wait": {"until": "acknowledged", "timeout_sec": 5},
                },
            },
            manager,
        )

        session.run.assert_called_once_with(
            args=["--mode", "fast"],
            timeout_sec=5,
            wait_for_stop=False,
        )

    def test_execution_manage_continue_routes_ack_mode(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.continue_execution.return_value = OperationSuccess(SessionMessage(message="continued"))

        dispatch(
            "gdb_execution_manage",
            {
                "session_id": 3,
                "action": "continue",
                "execution": {"wait": {"until": "acknowledged", "timeout_sec": 2}},
            },
            manager,
        )

        session.continue_execution.assert_called_once_with(
            wait_for_stop=False,
            timeout_sec=2,
        )
```

```python
# tests/mcp/test_runtime.py

def test_runtime_dispatches_v2_status_query():
    manager = Mock()
    manager.get_session.return_value = Mock(
        get_status=Mock(return_value=OperationSuccess(SessionStatusSnapshot(is_running=True, target_loaded=True, has_controller=True)))
    )

    runtime = ServerRuntime(session_manager=manager, logger=logging.getLogger("test"))
    result = asyncio.run(
        runtime.call_tool(
            "gdb_session_query",
            {"session_id": 7, "action": "status", "query": {}},
        )
    )

    payload = json.loads(result[0].text)
    assert payload["status"] == "success"
    assert payload["action"] == "status"
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'session_query_list_routes_to_registry or session_manage_stop_routes_to_registry_close or execution_manage_run_routes_wait_policy or execution_manage_continue_routes_ack_mode' tests/mcp/test_runtime.py`
Expected: FAIL because `dispatch_tool_call()` still expects legacy tool names and `SessionExecutionService` does not accept wait-policy parameters for continue/run parity.

- [ ] **Step 3: Implement v2 dispatch and execution wait handling**

```python
# src/gdb_mcp/mcp/handlers.py

def _unwrap_action_args(args: BaseModel) -> BaseModel:
    root = getattr(args, "root", None)
    return cast(BaseModel, root if root is not None else args)


def _wrap_action_result(action: str, result: ToolResult) -> ToolResult:
    if isinstance(result, OperationError):
        details = dict(result.details)
        details.setdefault("action", action)
        return OperationError(
            message=result.message,
            code=result.code,
            fatal=result.fatal,
            details=details,
        )

    wrapped_payload = {
        "action": action,
        "result": payload_to_mapping(result.value),
    }
    return OperationSuccess(wrapped_payload, warnings=result.warnings)


def _handle_execution_manage(session: SessionService, args: ExecutionManageArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, ExecutionRunAction):
        wait = action_args.execution.wait
        timeout_sec = wait.timeout_sec if wait is not None and wait.timeout_sec is not None else 30
        wait_for_stop = wait is None or wait.until == "stop"
        run_args = _normalize_run_args(action_args.execution.args)
        if isinstance(run_args, OperationError):
            return run_args
        return _wrap_action_result(
            "run",
            session.run(args=run_args, timeout_sec=timeout_sec, wait_for_stop=wait_for_stop),
        )

    if isinstance(action_args, ExecutionContinueAction):
        wait = action_args.execution.wait
        timeout_sec = wait.timeout_sec if wait is not None and wait.timeout_sec is not None else 30
        wait_for_stop = wait is None or wait.until == "stop"
        return _wrap_action_result(
            "continue",
            session.continue_execution(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec),
        )

    if isinstance(action_args, ExecutionInterruptAction):
        return _wrap_action_result("interrupt", session.interrupt())

    if isinstance(action_args, ExecutionStepAction):
        wait = action_args.execution.wait
        timeout_sec = wait.timeout_sec if wait is not None and wait.timeout_sec is not None else 30
        wait_for_stop = wait is None or wait.until == "stop"
        return _wrap_action_result(
            "step",
            session.step(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec),
        )

    if isinstance(action_args, ExecutionNextAction):
        wait = action_args.execution.wait
        timeout_sec = wait.timeout_sec if wait is not None and wait.timeout_sec is not None else 30
        wait_for_stop = wait is None or wait.until == "stop"
        return _wrap_action_result(
            "next",
            session.next(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec),
        )

    if isinstance(action_args, ExecutionFinishAction):
        wait = action_args.execution.wait
        timeout_sec = wait.timeout_sec if wait is not None and wait.timeout_sec is not None else 30
        wait_for_stop = wait is None or wait.until == "stop"
        return _wrap_action_result(
            "finish",
            session.finish(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec),
        )

    if isinstance(action_args, ExecutionWaitForStopAction):
        return _wrap_action_result(
            "wait_for_stop",
            session.wait_for_stop(
                timeout_sec=action_args.execution.timeout_sec,
                stop_reasons=tuple(action_args.execution.stop_reasons),
            ),
        )

    raise AssertionError(f"Unhandled execution action: {action_args}")


async def dispatch_tool_call(name: str, arguments: object, session_manager: SessionRegistry, *, logger: logging.Logger) -> list[TextContent]:
    try:
        normalized_args = _normalize_arguments(arguments)

        if name == "gdb_session_start":
            return serialize_result(_handle_start_session(normalized_args, session_manager))

        if name == "gdb_session_query":
            action_args = SessionQueryArgs.model_validate(normalized_args).root
            if isinstance(action_args, SessionQueryListAction):
                return serialize_result(_wrap_action_result("list", session_manager.list_sessions()))
            session = session_manager.get_session(action_args.session_id)
            return serialize_result(_wrap_action_result("status", session.get_status()))

        if name == "gdb_session_manage":
            action_args = SessionManageArgs.model_validate(normalized_args).root
            return serialize_result(
                _wrap_action_result("stop", session_manager.close_session(action_args.session_id))
            )

        if name == "gdb_run_until_failure":
            return serialize_result(_handle_run_until_failure(normalized_args, session_manager))

        tool_spec = SESSION_TOOL_SPECS.get(name)
        if tool_spec is None:
            return serialize_result(
                OperationError(message=f"Unknown tool: {name}", code="unknown_tool")
            )

        return serialize_result(_dispatch_session_tool(normalized_args, session_manager, tool_spec))

    except Exception as exc:
        logger.error("Error executing tool %s: %s", name, exc, exc_info=True)
        return serialize_exception(name, exc)


SESSION_TOOL_SPECS = {
    "gdb_execution_manage": session_tool_spec(ExecutionManageArgs, _handle_execution_manage),
    "gdb_inferior_query": session_tool_spec(InferiorQueryArgs, _handle_inferior_query),
    "gdb_inferior_manage": session_tool_spec(InferiorManageArgs, _handle_inferior_manage),
    "gdb_breakpoint_query": session_tool_spec(BreakpointQueryArgs, _handle_breakpoint_query),
    "gdb_breakpoint_manage": session_tool_spec(BreakpointManageArgs, _handle_breakpoint_manage),
    "gdb_context_query": session_tool_spec(ContextQueryArgs, _handle_context_query),
    "gdb_context_manage": session_tool_spec(ContextManageArgs, _handle_context_manage),
    "gdb_inspect_query": session_tool_spec(InspectQueryArgs, _handle_inspect_query),
    "gdb_workflow_batch": session_tool_spec(BatchArgs, _handle_batch),
    "gdb_capture_bundle": session_tool_spec(CaptureBundleArgs, _handle_capture_bundle),
    "gdb_execute_command": session_tool_spec(ExecuteCommandArgs, _handle_execute_command),
    "gdb_attach_process": session_tool_spec(AttachProcessArgs, _handle_attach_process),
    "gdb_call_function": session_tool_spec(CallFunctionArgs, _handle_call_function),
}
```

```python
# src/gdb_mcp/session/service.py

def continue_execution(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    return self._execution.continue_execution(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)


def step(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    return self._execution.step(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)


def next(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    return self._execution.next(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)


def finish(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[FinishInfo] | OperationError:
    return self._execution.finish(wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)
```

```python
# src/gdb_mcp/session/execution.py

def continue_execution(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    if not self._runtime.has_controller:
        return OperationError(message="No active GDB session", code="invalid_state")

    return self._command_runner.execute_command_result(
        "-exec-continue",
        timeout_sec=timeout_sec,
        allow_running_timeout=not wait_for_stop,
    )


def step(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    return self._command_runner.execute_command_result(
        "-exec-step",
        timeout_sec=timeout_sec,
        allow_running_timeout=not wait_for_stop,
    )


def next(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    return self._command_runner.execute_command_result(
        "-exec-next",
        timeout_sec=timeout_sec,
        allow_running_timeout=not wait_for_stop,
    )


def finish(
    self,
    *,
    wait_for_stop: bool = True,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> OperationSuccess[FinishInfo] | OperationError:
    result = self._command_runner.execute_command_result(
        "-exec-finish",
        timeout_sec=timeout_sec,
        allow_running_timeout=not wait_for_stop,
    )
    if isinstance(result, OperationError):
        return result

    raw_payload = extract_mi_result_payload(command_result_payload(result))
    payload_mapping = raw_payload if isinstance(raw_payload, dict) else {}
    if not payload_mapping and self._runtime.last_stop_event is not None:
        payload_mapping = self._runtime.last_stop_event.details
    frame_payload = payload_mapping.get("frame")
    frame = cast(FrameRecord | None, frame_payload) if isinstance(frame_payload, dict) else None
    if frame is None and self._runtime.last_stop_event is not None:
        frame = self._runtime.last_stop_event.frame

    return OperationSuccess(
        FinishInfo(
            message="Frame finished",
            return_value=payload_mapping.get("return-value") if isinstance(payload_mapping.get("return-value"), str) else None,
            gdb_result_var=payload_mapping.get("gdb-result-var") if isinstance(payload_mapping.get("gdb-result-var"), str) else None,
            frame=frame,
            execution_state=self._runtime.execution_state,
            stop_reason=self._runtime.stop_reason,
            last_stop_event=self._runtime.last_stop_event,
        )
    )
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'session_query_list_routes_to_registry or session_manage_stop_routes_to_registry_close or execution_manage_run_routes_wait_policy or execution_manage_continue_routes_ack_mode' tests/mcp/test_runtime.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_handlers.py tests/mcp/test_runtime.py src/gdb_mcp/mcp/handlers.py src/gdb_mcp/session/service.py src/gdb_mcp/session/execution.py
git commit -m "refactor: route session and execution tools through v2 actions"
```

### Task 4: Route Context And Inspection Through V2 Queries

**Files:**
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/session/service.py`
- Modify: `src/gdb_mcp/session/inspection.py`
- Modify: `tests/mcp/test_handlers.py`

**Testing approach:** `TDD`
Reason: Context and inspect actions depend on careful selection-restore behavior. Handler-level tests should lock the routing down before changing service signatures.

- [ ] **Step 1: Write failing handler tests for v2 context and inspect queries**

```python
# tests/mcp/test_handlers.py

class TestHandlerDispatch:
    def test_context_manage_select_thread_routes_to_service(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.select_thread.return_value = OperationSuccess(ThreadSelectionInfo(thread_id=7))

        dispatch(
            "gdb_context_manage",
            {
                "session_id": 2,
                "action": "select_thread",
                "context": {"thread_id": 7},
            },
            manager,
        )

        session.select_thread.assert_called_once_with(thread_id=7)

    def test_context_query_frame_routes_with_thread_and_frame_override(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.get_frame_info.return_value = OperationSuccess(FrameInfo(frame={"level": "1", "func": "worker"}))

        dispatch(
            "gdb_context_query",
            {
                "session_id": 2,
                "action": "frame",
                "query": {"thread_id": 7, "frame": 1},
            },
            manager,
        )

        session.get_frame_info.assert_called_once_with(thread_id=7, frame=1)

    def test_inspect_query_disassembly_routes_location_union(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.disassemble.return_value = OperationSuccess(
            DisassemblyInfo(
                scope="function",
                thread_id=None,
                frame=None,
                function="main",
                file=None,
                fullname=None,
                line=None,
                start_address=None,
                end_address=None,
                mode="mixed",
                instructions=[],
                count=0,
            )
        )

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 2,
                "action": "disassembly",
                "query": {
                    "location": {"kind": "function", "function": "main"},
                    "instruction_count": 8,
                    "mode": "mixed",
                },
            },
            manager,
        )

        session.disassemble.assert_called_once_with(
            thread_id=None,
            frame=None,
            function="main",
            address=None,
            start_address=None,
            end_address=None,
            file=None,
            line=None,
            instruction_count=8,
            mode="mixed",
        )

    def test_inspect_query_variables_routes_context_selector(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.get_variables.return_value = OperationSuccess(
            VariablesInfo(thread_id=3, frame=1, variables=[])
        )

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 2,
                "action": "variables",
                "query": {"context": {"thread_id": 3, "frame": 1}},
            },
            manager,
        )

        session.get_variables.assert_called_once_with(thread_id=3, frame=1)

    def test_inspect_query_source_routes_file_range(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.get_source_context.return_value = OperationSuccess(
            SourceContextInfo(
                scope="file_range",
                thread_id=None,
                frame=None,
                function=None,
                address=None,
                file="main.c",
                fullname=None,
                line=None,
                start_line=10,
                end_line=12,
                lines=[],
                count=0,
            )
        )

        dispatch(
            "gdb_inspect_query",
            {
                "session_id": 2,
                "action": "source",
                "query": {
                    "location": {
                        "kind": "file_range",
                        "file": "main.c",
                        "start_line": 10,
                        "end_line": 12,
                    },
                    "context_before": 0,
                    "context_after": 0,
                },
            },
            manager,
        )

        session.get_source_context.assert_called_once_with(
            thread_id=None,
            frame=None,
            function=None,
            address=None,
            file="main.c",
            line=None,
            start_line=10,
            end_line=12,
            context_before=0,
            context_after=0,
        )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'context_manage_select_thread_routes_to_service or context_query_frame_routes_with_thread_and_frame_override or inspect_query_disassembly_routes_location_union or inspect_query_variables_routes_context_selector or inspect_query_source_routes_file_range'`
Expected: FAIL because `gdb_context_manage`, `gdb_context_query`, and `gdb_inspect_query` do not exist yet and `SessionInspectionService.get_frame_info()` does not accept override parameters.

- [ ] **Step 3: Implement v2 context and inspect routing**

```python
# src/gdb_mcp/session/inspection.py

def get_frame_info(
    self,
    *,
    thread_id: int | None = None,
    frame: int | None = None,
) -> OperationSuccess[FrameInfo] | OperationError:
    selection = self._capture_selection() if thread_id is not None or frame is not None else None
    if isinstance(selection, OperationError):
        return selection

    selection_error = self._select_for_inspection(
        selection,
        thread_id=thread_id,
        frame=frame,
    )
    if selection_error is not None:
        return self._selection_error_with_restore(selection, selection_error)

    result = self._command_runner.execute_command_result(
        "-stack-info-frame",
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )
    if isinstance(result, OperationError):
        return self._selection_error_with_restore(selection, result)

    frame_info = frame_info_from_payload(
        extract_mi_result_payload(command_result_payload(result))
    )

    if selection is not None:
        restore_error = self._restore_selection(selection)
        if restore_error is not None:
            return restore_error

    return OperationSuccess(frame_info)
```

```python
# src/gdb_mcp/session/service.py

def get_frame_info(
    self,
    *,
    thread_id: int | None = None,
    frame: int | None = None,
) -> OperationSuccess[FrameInfo] | OperationError:
    return self._inspection.get_frame_info(thread_id=thread_id, frame=frame)
```

```python
# src/gdb_mcp/mcp/handlers.py

def _location_kwargs(location: BaseModel) -> dict[str, object]:
    if isinstance(location, LocationCurrent):
        return {
            "function": None,
            "address": None,
            "start_address": None,
            "end_address": None,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if isinstance(location, LocationFunction):
        return {
            "function": location.function,
            "address": None,
            "start_address": None,
            "end_address": None,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if isinstance(location, LocationAddress):
        return {
            "function": None,
            "address": location.address,
            "start_address": None,
            "end_address": None,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if isinstance(location, LocationAddressRange):
        return {
            "function": None,
            "address": None,
            "start_address": location.start_address,
            "end_address": location.end_address,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if isinstance(location, LocationFileLine):
        return {
            "function": None,
            "address": None,
            "start_address": None,
            "end_address": None,
            "file": location.file,
            "line": location.line,
            "start_line": None,
            "end_line": None,
        }
    return {
        "function": None,
        "address": None,
        "start_address": None,
        "end_address": None,
        "file": location.file,
        "line": None,
        "start_line": location.start_line,
        "end_line": location.end_line,
    }


def _handle_context_query(session: SessionService, args: ContextQueryArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, ContextQueryThreadsAction):
        return _wrap_action_result("threads", session.get_threads())
    if isinstance(action_args, ContextQueryBacktraceAction):
        return _wrap_action_result(
            "backtrace",
            session.get_backtrace(
                thread_id=action_args.query.thread_id,
                max_frames=action_args.query.max_frames,
            ),
        )
    if isinstance(action_args, ContextQueryFrameAction):
        return _wrap_action_result(
            "frame",
            session.get_frame_info(
                thread_id=action_args.query.thread_id,
                frame=action_args.query.frame,
            ),
        )
    raise AssertionError(f"Unhandled context query action: {action_args}")


def _handle_context_manage(session: SessionService, args: ContextManageArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, ContextManageSelectThreadAction):
        return _wrap_action_result(
            "select_thread",
            session.select_thread(thread_id=action_args.context.thread_id),
        )
    if isinstance(action_args, ContextManageSelectFrameAction):
        return _wrap_action_result(
            "select_frame",
            session.select_frame(frame_number=action_args.context.frame_number),
        )
    raise AssertionError(f"Unhandled context manage action: {action_args}")


def _handle_inspect_query(session: SessionService, args: InspectQueryArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, InspectEvaluateAction):
        context = action_args.query.context
        return _wrap_action_result(
            "evaluate",
            session.evaluate_expression(
                action_args.query.expression,
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else None,
            ),
        )

    if isinstance(action_args, InspectVariablesAction):
        context = action_args.query.context
        return _wrap_action_result(
            "variables",
            session.get_variables(
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else 0,
            ),
        )

    if isinstance(action_args, InspectRegistersAction):
        context = action_args.query.context
        return _wrap_action_result(
            "registers",
            session.get_registers(
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else None,
                register_numbers=action_args.query.register_numbers,
                register_names=action_args.query.register_names,
                include_vector_registers=action_args.query.include_vector_registers,
                max_registers=action_args.query.max_registers,
                value_format=action_args.query.value_format,
            ),
        )

    if isinstance(action_args, InspectMemoryAction):
        return _wrap_action_result(
            "memory",
            session.read_memory(
                address=action_args.query.address,
                count=action_args.query.count,
                offset=action_args.query.offset,
            ),
        )

    if isinstance(action_args, InspectDisassemblyAction):
        location = _location_kwargs(action_args.query.location)
        context = action_args.query.context
        return _wrap_action_result(
            "disassembly",
            session.disassemble(
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else None,
                function=cast(str | None, location["function"]),
                address=cast(str | None, location["address"]),
                start_address=cast(str | None, location["start_address"]),
                end_address=cast(str | None, location["end_address"]),
                file=cast(str | None, location["file"]),
                line=cast(int | None, location["line"]),
                instruction_count=action_args.query.instruction_count,
                mode=action_args.query.mode,
            ),
        )

    if isinstance(action_args, InspectSourceAction):
        location = _location_kwargs(action_args.query.location)
        context = action_args.query.context
        return _wrap_action_result(
            "source",
            session.get_source_context(
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else None,
                function=cast(str | None, location["function"]),
                address=cast(str | None, location["address"]),
                file=cast(str | None, location["file"]),
                line=cast(int | None, location["line"]),
                start_line=cast(int | None, location["start_line"]),
                end_line=cast(int | None, location["end_line"]),
                context_before=action_args.query.context_before,
                context_after=action_args.query.context_after,
            ),
        )

    raise AssertionError(f"Unhandled inspect action: {action_args}")
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'context_manage_select_thread_routes_to_service or context_query_frame_routes_with_thread_and_frame_override or inspect_query_disassembly_routes_location_union or inspect_query_variables_routes_context_selector or inspect_query_source_routes_file_range'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_handlers.py src/gdb_mcp/mcp/handlers.py src/gdb_mcp/session/service.py src/gdb_mcp/session/inspection.py
git commit -m "refactor: route context and inspection through v2 queries"
```

### Task 5: Consolidate Breakpoint And Inferior Tool Families

**Files:**
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/session/service.py`
- Modify: `src/gdb_mcp/session/breakpoints.py`
- Modify: `tests/mcp/test_handlers.py`
- Modify: `tests/integration/test_gdb_integration.py`

**Testing approach:** `TDD`
Reason: Breakpoints are the most visible consolidation in the redesign. Driving them from handler tests first keeps the `create/get/update/delete/enable/disable` behavior explicit before updating end-to-end flows.

- [ ] **Step 1: Write failing tests for breakpoint and inferior v2 actions**

```python
# tests/mcp/test_handlers.py

class TestHandlerDispatch:
    def test_breakpoint_query_get_routes_to_service(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.get_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "4", "type": "breakpoint"})
        )

        dispatch(
            "gdb_breakpoint_query",
            {"session_id": 3, "action": "get", "query": {"number": 4}},
            manager,
        )

        session.get_breakpoint.assert_called_once_with(4)

    def test_breakpoint_manage_update_routes_changes(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.update_breakpoint.return_value = OperationSuccess(
            BreakpointInfo(breakpoint={"number": "4", "type": "breakpoint", "exp": "count > 100"})
        )

        dispatch(
            "gdb_breakpoint_manage",
            {
                "session_id": 3,
                "action": "update",
                "breakpoint": {"number": 4},
                "changes": {"condition": "count > 100", "clear_condition": False},
            },
            manager,
        )

        session.update_breakpoint.assert_called_once_with(
            4,
            condition="count > 100",
            clear_condition=False,
        )

    def test_inferior_query_current_returns_current_inferior(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.list_inferiors.return_value = OperationSuccess(
            InferiorListInfo(
                inferiors=[
                    {"inferior_id": 1, "is_current": True, "display": "i1"},
                    {"inferior_id": 2, "is_current": False, "display": "i2"},
                ],
                count=2,
                current_inferior_id=1,
            )
        )

        result_data = dispatch(
            "gdb_inferior_query",
            {"session_id": 8, "action": "current", "query": {}},
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["action"] == "current"
        assert result_data["result"]["inferior"]["inferior_id"] == 1

    def test_inferior_manage_select_routes_to_service(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.select_inferior.return_value = OperationSuccess(
            InferiorSelectionInfo(inferior_id=2, is_current=True)
        )

        dispatch(
            "gdb_inferior_manage",
            {
                "session_id": 8,
                "action": "select",
                "inferior": {"inferior_id": 2},
            },
            manager,
        )

        session.select_inferior.assert_called_once_with(inferior_id=2)
```

```python
# tests/integration/test_gdb_integration.py

def test_v2_breakpoint_create_list_disable_delete(compile_program, start_session, stop_session):
    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="breakpoint_v2.cpp",
        compiler="g++",
    )
    session_id = start_session(program)

    create_result = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {
                "kind": "code",
                "location": "main",
                "temporary": False,
            },
        },
    )
    assert create_result["status"] == "success"

    list_result = call_gdb_tool(
        "gdb_breakpoint_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    breakpoint_number = int(list_result["result"]["breakpoints"][0]["number"])

    disable_result = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "disable",
            "breakpoint": {"number": breakpoint_number},
        },
    )
    assert disable_result["status"] == "success"

    delete_result = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "delete",
            "breakpoint": {"number": breakpoint_number},
        },
    )
    assert delete_result["status"] == "success"
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'breakpoint_query_get_routes_to_service or breakpoint_manage_update_routes_changes or inferior_query_current_returns_current_inferior or inferior_manage_select_routes_to_service' tests/integration/test_gdb_integration.py -k 'v2_breakpoint_create_list_disable_delete'`
Expected: FAIL because the breakpoint and inferior v2 handlers do not exist and `SessionBreakpointService` has no `get_breakpoint()` or `update_breakpoint()`.

- [ ] **Step 3: Implement breakpoint and inferior consolidation**

```python
# src/gdb_mcp/session/breakpoints.py

def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
    list_result = self.list_breakpoints()
    if isinstance(list_result, OperationError):
        return list_result

    breakpoint_info = self._find_breakpoint_record(list_result.value.breakpoints, number)
    if breakpoint_info is None:
        return OperationError(
            message=f"Breakpoint {number} not found",
            code="not_found",
            details={"breakpoint_number": number},
        )

    return OperationSuccess(BreakpointInfo(breakpoint=breakpoint_info))


def update_breakpoint(
    self,
    number: int,
    *,
    condition: str | None = None,
    clear_condition: bool = False,
) -> OperationSuccess[BreakpointInfo] | OperationError:
    if condition is not None and clear_condition:
        return OperationError(
            message="condition and clear_condition are mutually exclusive",
            code="unsupported_combination",
        )
    if condition is None and clear_condition is False:
        return OperationError(
            message="At least one breakpoint change is required",
            code="validation_error",
        )

    command = f"-break-condition {number}"
    if condition is not None:
        command = f"{command} {quote_mi_string(condition)}"

    result = self._command_runner.execute_command_result(
        command,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )
    if isinstance(result, OperationError):
        return result

    return self._breakpoint_info_for_number(
        number,
        create_details={"updated_breakpoint_number": number},
    )
```

```python
# src/gdb_mcp/session/service.py

def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
    return self._breakpoints.get_breakpoint(number)


def update_breakpoint(
    self,
    number: int,
    *,
    condition: str | None = None,
    clear_condition: bool = False,
) -> OperationSuccess[BreakpointInfo] | OperationError:
    return self._breakpoints.update_breakpoint(
        number,
        condition=condition,
        clear_condition=clear_condition,
    )
```

```python
# src/gdb_mcp/mcp/handlers.py

def _handle_breakpoint_query(session: SessionService, args: BreakpointQueryArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, BreakpointQueryListAction):
        return _wrap_action_result("list", session.list_breakpoints())
    if isinstance(action_args, BreakpointQueryGetAction):
        return _wrap_action_result("get", session.get_breakpoint(action_args.query.number))
    raise AssertionError(f"Unhandled breakpoint query action: {action_args}")


def _handle_breakpoint_manage(session: SessionService, args: BreakpointManageArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, BreakpointManageCreateAction):
        breakpoint_payload = action_args.breakpoint
        if isinstance(breakpoint_payload, BreakpointCodeCreate):
            result = session.set_breakpoint(
                location=breakpoint_payload.location,
                condition=breakpoint_payload.condition,
                temporary=breakpoint_payload.temporary,
            )
        elif isinstance(breakpoint_payload, BreakpointWatchCreate):
            result = session.set_watchpoint(
                expression=breakpoint_payload.expression,
                access=breakpoint_payload.access,
            )
        else:
            result = session.set_catchpoint(
                breakpoint_payload.event,
                argument=breakpoint_payload.argument,
                temporary=breakpoint_payload.temporary,
            )
        return _wrap_action_result("create", result)

    if isinstance(action_args, BreakpointManageUpdateAction):
        return _wrap_action_result(
            "update",
            session.update_breakpoint(
                action_args.breakpoint.number,
                condition=action_args.changes.condition,
                clear_condition=action_args.changes.clear_condition,
            ),
        )

    if isinstance(action_args, BreakpointManageNumberAction):
        number = action_args.breakpoint.number
        if action_args.action == "delete":
            return _wrap_action_result("delete", session.delete_breakpoint(number))
        if action_args.action == "enable":
            return _wrap_action_result("enable", session.enable_breakpoint(number))
        return _wrap_action_result("disable", session.disable_breakpoint(number))

    raise AssertionError(f"Unhandled breakpoint manage action: {action_args}")


def _handle_inferior_query(session: SessionService, args: InferiorQueryArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, InferiorQueryListAction):
        return _wrap_action_result("list", session.list_inferiors())

    inferiors_result = session.list_inferiors()
    if isinstance(inferiors_result, OperationError):
        return inferiors_result

    current_record = next(
        (inferior for inferior in inferiors_result.value.inferiors if inferior.get("is_current")),
        None,
    )
    if current_record is None:
        return OperationError(message="No current inferior", code="not_found", details={"action": "current"})

    return _wrap_action_result(
        "current",
        OperationSuccess(
            {
                "inferior": current_record,
                "count": inferiors_result.value.count,
                "current_inferior_id": inferiors_result.value.current_inferior_id,
            }
        ),
    )


def _handle_inferior_manage(session: SessionService, args: InferiorManageArgs) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, InferiorManageCreateAction):
        return _wrap_action_result(
            "create",
            session.add_inferior(
                executable=action_args.inferior.executable,
                make_current=action_args.inferior.make_current,
            ),
        )
    if isinstance(action_args, InferiorManageRemoveAction):
        return _wrap_action_result(
            "remove",
            session.remove_inferior(action_args.inferior.inferior_id),
        )
    if isinstance(action_args, InferiorManageSelectAction):
        return _wrap_action_result(
            "select",
            session.select_inferior(inferior_id=action_args.inferior.inferior_id),
        )
    if isinstance(action_args, InferiorManageFollowForkAction):
        return _wrap_action_result(
            "set_follow_fork_mode",
            session.set_follow_fork_mode(action_args.inferior.mode),
        )
    if isinstance(action_args, InferiorManageDetachOnForkAction):
        return _wrap_action_result(
            "set_detach_on_fork",
            session.set_detach_on_fork(action_args.inferior.enabled),
        )
    raise AssertionError(f"Unhandled inferior manage action: {action_args}")
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'breakpoint_query_get_routes_to_service or breakpoint_manage_update_routes_changes or inferior_query_current_returns_current_inferior or inferior_manage_select_routes_to_service' tests/integration/test_gdb_integration.py -k 'v2_breakpoint_create_list_disable_delete'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_handlers.py tests/integration/test_gdb_integration.py src/gdb_mcp/mcp/handlers.py src/gdb_mcp/session/service.py src/gdb_mcp/session/breakpoints.py
git commit -m "refactor: consolidate breakpoint and inferior MCP tools"
```

### Task 6: Migrate Workflow Tools To V2 Step Shapes

**Files:**
- Modify: `src/gdb_mcp/domain/models.py`
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/session/workflow.py`
- Modify: `src/gdb_mcp/session/campaign.py`
- Modify: `tests/mcp/test_schemas.py`
- Modify: `tests/mcp/test_handlers.py`

**Testing approach:** `characterization/integration test`
Reason: `gdb_workflow_batch` and `gdb_run_until_failure` are orchestration layers over other tools. The important behavior is step validation and returned step metadata, not a deep reimplementation of each action.

- [ ] **Step 1: Write failing tests for v2 workflow-step validation and metadata**

```python
# tests/mcp/test_handlers.py

class TestHandlerDispatch:
    def test_workflow_batch_reports_step_action_and_code(self):
        manager = create_session_manager_mock()
        session = create_session_mock()
        manager.get_session.return_value = session
        session.execute_batch_templates.return_value = OperationSuccess(
            BatchExecutionInfo(
                steps=[
                    BatchStepResult(
                        index=0,
                        tool="gdb_breakpoint_manage",
                        action="disable",
                        status="error",
                        code="not_found",
                        result={
                            "status": "error",
                            "action": "disable",
                            "code": "not_found",
                            "message": "Breakpoint 99 not found",
                        },
                    )
                ],
                count=1,
                completed_steps=1,
                error_count=1,
            )
        )

        result_data = dispatch(
            "gdb_workflow_batch",
            {
                "session_id": 2,
                "steps": [
                    {
                        "tool": "gdb_breakpoint_manage",
                        "arguments": {
                            "action": "disable",
                            "breakpoint": {"number": 99},
                        },
                    }
                ],
            },
            manager,
        )

        assert result_data["status"] == "success"
        assert result_data["result"]["steps"][0]["action"] == "disable"
        assert result_data["result"]["steps"][0]["code"] == "not_found"

    def test_workflow_batch_rejects_session_query_list_step(self):
        manager = create_session_manager_mock()

        result_data = dispatch(
            "gdb_workflow_batch",
            {
                "session_id": 2,
                "steps": [
                    {
                        "tool": "gdb_session_query",
                        "arguments": {"action": "list", "query": {}},
                    }
                ],
            },
            manager,
        )

        assert result_data["status"] == "error"
        assert result_data["code"] == "unsupported_combination"
```

```python
# tests/mcp/test_schemas.py

class TestWorkflowBatchArgs:
    def test_run_until_failure_accepts_v2_setup_steps(self):
        args = RunUntilFailureArgs(
            startup=StartSessionArgs(program="/tmp/app"),
            setup_steps=[
                {
                    "tool": "gdb_breakpoint_manage",
                    "arguments": {
                        "action": "create",
                        "breakpoint": {"kind": "code", "location": "main"},
                    },
                }
            ],
        )

        assert args.setup_steps[0].tool == "gdb_breakpoint_manage"
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'workflow_batch_reports_step_action_and_code or workflow_batch_rejects_session_query_list_step' tests/mcp/test_schemas.py -k 'run_until_failure_accepts_v2_setup_steps'`
Expected: FAIL because the workflow tool still uses `gdb_batch`, the step metadata does not include `action` or `code`, and batch-step validation still operates on the old tool inventory.

- [ ] **Step 3: Update workflow models, batch validation, and campaign callers**

```python
# src/gdb_mcp/domain/models.py

@dataclass(slots=True, frozen=True)
class BatchStepResult:
    index: int
    tool: str
    status: str
    result: StructuredPayload
    label: str | None = None
    action: str | None = None
    code: str | None = None
    stop_event: StopEvent | None = None
```

```python
# src/gdb_mcp/session/workflow.py

results.append(
    BatchStepResult(
        index=index,
        tool=step.tool,
        label=step.label,
        status=step_status,
        action=cast(str | None, serialized_result.get("action")),
        code=cast(str | None, serialized_result.get("code")),
        result=serialized_result,
        stop_event=stop_event,
    )
)
```

```python
# src/gdb_mcp/mcp/handlers.py

def _resolve_batch_steps(
    steps: Sequence[BatchStepArgs | str],
) -> OperationSuccess[list[BatchStepTemplate]] | OperationError:
    templates: list[BatchStepTemplate] = []

    for index, raw_step in enumerate(steps):
        step = raw_step if isinstance(raw_step, BatchStepArgs) else BatchStepArgs.model_validate({"tool": raw_step})

        if "session_id" in step.arguments:
            return OperationError(
                message=f"Batch step {index} ({step.tool}) must not include session_id. It is inherited from gdb_workflow_batch.",
                code="validation_error",
            )

        if step.tool == "gdb_session_query" and step.arguments.get("action") == "list":
            return OperationError(
                message="gdb_session_query(action=list) is not valid inside gdb_workflow_batch",
                code="unsupported_combination",
            )
        if step.tool == "gdb_session_manage":
            return OperationError(
                message="gdb_session_manage is not valid inside gdb_workflow_batch",
                code="unsupported_combination",
            )

        tool_spec = SESSION_TOOL_SPECS.get(step.tool)
        if tool_spec is None or step.tool in {"gdb_workflow_batch", "gdb_run_until_failure"}:
            return OperationError(
                message=f"Unsupported batch step tool: {step.tool}",
                code="unknown_tool",
            )

        validated_args = tool_spec.model.model_validate(step.arguments)
        templates.append(
            BatchStepTemplate(
                tool=step.tool,
                label=step.label,
                execute=lambda session, validated_args=validated_args, tool_spec=tool_spec: tool_spec.handler(session, validated_args),
            )
        )

    return OperationSuccess(templates)
```

```python
# src/gdb_mcp/session/campaign.py

run_result = session.run(
    args=list(request.run_args) or None,
    timeout_sec=request.run_timeout_sec,
    wait_for_stop=True,
)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'workflow_batch_reports_step_action_and_code or workflow_batch_rejects_session_query_list_step' tests/mcp/test_schemas.py -k 'run_until_failure_accepts_v2_setup_steps'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_handlers.py tests/mcp/test_schemas.py src/gdb_mcp/domain/models.py src/gdb_mcp/mcp/handlers.py src/gdb_mcp/session/workflow.py src/gdb_mcp/session/campaign.py
git commit -m "refactor: migrate workflow tools to v2 step shapes"
```

### Task 7: Rewrite Docs, Integration Tests, And Run Full Verification

**Files:**
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_gdb_integration.py`
- Modify: `tests/integration/test_multi_session.py`
- Modify: `README.md`
- Modify: `TOOLS.md`
- Test/Verify: `uv run ruff check src tests`, `uv run mypy src`, `uv run pytest -q`, `git diff --check`

**Testing approach:** `existing tests + targeted verification`
Reason: By this point the core routing and schema surface should already be in place. The remaining work is a coordinated rename/rewrite pass plus full repository verification.

- [ ] **Step 1: Rewrite the integration tests around the v2 tool names**

```python
# tests/integration/conftest.py

result = call_gdb_tool(
    "gdb_session_manage",
    {"session_id": session_id, "action": "stop", "session": {}},
)
```

```python
# tests/integration/test_multi_session.py

status1 = call_gdb_tool(
    "gdb_session_query",
    {"session_id": session_id_1, "action": "status", "query": {}},
)

sessions = call_gdb_tool(
    "gdb_session_query",
    {"action": "list", "query": {}},
)
```

```python
# tests/integration/test_gdb_integration.py

run_result = call_gdb_tool(
    "gdb_execution_manage",
    {
        "session_id": session_id,
        "action": "run",
        "execution": {"wait": {"until": "stop", "timeout_sec": 30}},
    },
)

threads_result = call_gdb_tool(
    "gdb_context_query",
    {"session_id": session_id, "action": "threads", "query": {}},
)

variables_result = call_gdb_tool(
    "gdb_inspect_query",
    {
        "session_id": session_id,
        "action": "variables",
        "query": {"context": {"thread_id": 1, "frame": 0}},
    },
)
```

- [ ] **Step 2: Run targeted integration tests before rewriting docs**

Run: `uv run pytest -q tests/integration/test_multi_session.py tests/integration/test_gdb_integration.py -k 'session_query or breakpoint_manage or execution_manage or context_query or inspect_query'`
Expected: FAIL until every integration call site and expected payload shape is updated.

- [ ] **Step 3: Rewrite README and TOOLS.md for the v2 public surface**

```markdown
# README.md

## Available Tools

The GDB MCP Server provides 17 tools organized by debugger domain:

- `gdb_session_start`
- `gdb_session_query`
- `gdb_session_manage`
- `gdb_inferior_query`
- `gdb_inferior_manage`
- `gdb_execution_manage`
- `gdb_breakpoint_query`
- `gdb_breakpoint_manage`
- `gdb_context_query`
- `gdb_context_manage`
- `gdb_inspect_query`
- `gdb_workflow_batch`
- `gdb_capture_bundle`
- `gdb_run_until_failure`
- `gdb_execute_command`
- `gdb_attach_process`
- `gdb_call_function`
```

````markdown
# TOOLS.md

### `gdb_breakpoint_manage`

Actions:
- `create`
- `delete`
- `enable`
- `disable`
- `update`

Example request:

`{"session_id": 7, "action": "create", "breakpoint": {"kind": "code", "location": "main", "condition": "count > 100", "temporary": false}}`

### Migration Appendix

- `gdb_get_status` -> `gdb_session_query(action="status")`
- `gdb_list_sessions` -> `gdb_session_query(action="list")`
- `gdb_set_breakpoint` -> `gdb_breakpoint_manage(action="create", breakpoint.kind="code")`
- `gdb_get_threads` -> `gdb_context_query(action="threads")`
- `gdb_evaluate_expression` -> `gdb_inspect_query(action="evaluate")`
````

- [ ] **Step 4: Run the full repository verification**

Run: `uv run ruff check src tests`
Expected: PASS

Run: `uv run mypy src`
Expected: PASS

Run: `uv run pytest -q`
Expected: PASS

Run: `git diff --check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_gdb_integration.py tests/integration/test_multi_session.py README.md TOOLS.md
git commit -m "docs: rewrite MCP interface docs and integration coverage for v2"
```
