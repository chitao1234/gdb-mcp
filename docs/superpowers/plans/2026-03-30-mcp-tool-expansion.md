# MCP Tool Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class structured MCP tools for inferior add/remove, non-blocking run, finish, disassembly, and source-context retrieval so common workflows stop depending on `gdb_execute_command`.

**Architecture:** Extend the existing MCP/session split rather than inventing a parallel abstraction. Inferior lifecycle and source/disassembly stay close to current inspection/navigation code, while run/finish remain execution-state operations in the execution service. Use MI where GDB provides structured data, and local source-file reads plus narrow resolution helpers where MI is weak.

**Tech Stack:** Python 3.10+, Pydantic models in `src/gdb_mcp/mcp/schemas.py`, MCP handlers in `src/gdb_mcp/mcp/handlers.py`, session services under `src/gdb_mcp/session/`, typed domain dataclasses in `src/gdb_mcp/domain/models.py`, pytest, ruff, mypy.

---

### File Structure

**Core files and responsibilities**

- Modify: `src/gdb_mcp/domain/models.py`
  Add typed records/dataclasses for inferior add/remove, finish, disassembly, and source-context payloads.
- Modify: `src/gdb_mcp/mcp/schemas.py`
  Add new tool argument schemas, extend `RunArgs`, register new tools, and update batch-allowed tool names.
- Modify: `src/gdb_mcp/mcp/handlers.py`
  Route new tools and normalize the new selector-heavy arguments before service dispatch.
- Modify: `src/gdb_mcp/session/service.py`
  Expose new high-level service methods that delegate to execution/inspection layers.
- Modify: `src/gdb_mcp/session/execution.py`
  Extend `run`, add `finish`, and add inferior add/remove lifecycle helpers if that is where runtime mutation fits best.
- Modify: `src/gdb_mcp/session/inspection.py`
  Add `disassemble` and `get_source_context`, plus location-resolution and restoration helpers.
- Modify: `src/gdb_mcp/session/command_runner.py`
  Reuse existing `allow_running_timeout` flow for non-blocking run and, if needed, add small parsing helpers for MI inferior IDs or finish results.
- Modify: `tests/mcp/test_schemas.py`
  Cover request validation, selector conflicts, and new `RunArgs.wait_for_stop`.
- Modify: `tests/mcp/test_handlers.py`
  Verify routing and normalized handler behavior for every new tool.
- Modify: `tests/session/test_execution_api.py`
  Cover non-blocking run, finish, and inferior lifecycle state updates.
- Modify: `tests/session/test_inspection_api.py`
  Cover disassembly and source-context selection/restoration semantics.
- Modify: `tests/integration/test_gdb_integration.py`
  Add end-to-end scenarios for inferior add/remove, non-blocking run, finish, disassembly, and source-context.
- Modify: `README.md`
  Update tool list and migration notes.
- Modify: `TOOLS.md`
  Add full user-facing docs for the new and extended tools.
- Modify: `examples/USAGE_GUIDE.md`
  Replace raw command guidance with the new structured tools.

### Task 1: Add Domain Models And MCP Schemas

**Files:**
- Modify: `src/gdb_mcp/domain/models.py`
- Modify: `src/gdb_mcp/mcp/schemas.py`
- Test/Verify: `tests/mcp/test_schemas.py`

**Testing approach:** `TDD`
Reason: The new tool contracts are explicit and mostly validation-driven. Schema tests can fail first on missing fields, selector conflicts, and new defaults without needing implementation of the runtime behavior yet.

- [ ] **Step 1: Write failing schema tests for the new tools and extended run args**

```python
# tests/mcp/test_schemas.py

class TestInferiorLifecycleArgs:
    def test_add_inferior_args_defaults(self):
        args = AddInferiorArgs(session_id=1)
        assert args.session_id == 1
        assert args.executable is None
        assert args.make_current is False

    def test_remove_inferior_args_requires_positive_id(self):
        args = RemoveInferiorArgs(session_id=1, inferior_id=2)
        assert args.inferior_id == 2
        with pytest.raises(ValidationError):
            RemoveInferiorArgs(session_id=1, inferior_id=0)


class TestExtendedRunArgs:
    def test_run_args_defaults_wait_for_stop_true(self):
        args = RunArgs(session_id=1)
        assert args.wait_for_stop is True


class TestDisassembleArgs:
    def test_disassemble_args_accepts_current_context_defaults(self):
        args = DisassembleArgs(session_id=1)
        assert args.mode == "mixed"
        assert args.instruction_count == 32

    def test_disassemble_args_accepts_numeric_string_thread_and_frame(self):
        args = DisassembleArgs(session_id=1, thread_id="2", frame="1")
        assert args.thread_id == 2
        assert args.frame == 1

    def test_disassemble_args_rejects_conflicting_selectors(self):
        with pytest.raises(ValidationError):
            DisassembleArgs(session_id=1, function="main", address="0x401000")

    def test_disassemble_args_requires_complete_address_range(self):
        with pytest.raises(ValidationError):
            DisassembleArgs(session_id=1, start_address="0x401000")

    def test_disassemble_args_accepts_file_line_selector(self):
        args = DisassembleArgs(session_id=1, file="main.c", line="12")
        assert args.line == 12


class TestSourceContextArgs:
    def test_source_context_accepts_file_range(self):
        args = GetSourceContextArgs(session_id=1, file="main.c", start_line="10", end_line="20")
        assert args.start_line == 10
        assert args.end_line == 20

    def test_source_context_rejects_line_and_range_together(self):
        with pytest.raises(ValidationError):
            GetSourceContextArgs(
                session_id=1,
                file="main.c",
                line=12,
                start_line=10,
                end_line=20,
            )

    def test_source_context_rejects_mixed_selector_modes(self):
        with pytest.raises(ValidationError):
            GetSourceContextArgs(session_id=1, function="main", file="main.c", line=12)
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_schemas.py -k 'InferiorLifecycleArgs or ExtendedRunArgs or DisassembleArgs or SourceContextArgs'`
Expected: fails because the new argument models and validation rules do not exist yet.

- [ ] **Step 3: Implement the schema and domain model changes**

```python
# src/gdb_mcp/domain/models.py

class DisassemblyInstructionRecord(TypedDict, total=False):
    address: str
    instruction: str
    opcodes: str
    function: str
    offset: int
    file: str
    fullname: str
    line: int
    is_current: bool


class SourceLineRecord(TypedDict, total=False):
    line_number: int
    text: str
    is_current: bool


@dataclass(slots=True, frozen=True)
class InferiorAddInfo:
    inferior_id: int
    is_current: bool = False
    display: str | None = None
    description: str | None = None
    connection: str | None = None
    executable: str | None = None
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class InferiorRemoveInfo:
    inferior_id: int
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class FinishInfo:
    message: str
    return_value: str | None = None
    gdb_result_var: str | None = None
    frame: FrameRecord | None = None
    execution_state: str | None = None
    stop_reason: str | None = None
    last_stop_event: StopEvent | None = None


@dataclass(slots=True, frozen=True)
class DisassemblyInfo:
    scope: str
    thread_id: int | None
    frame: int | None
    function: str | None
    file: str | None
    fullname: str | None
    line: int | None
    start_address: str | None
    end_address: str | None
    mode: str
    instructions: list[DisassemblyInstructionRecord]
    count: int


@dataclass(slots=True, frozen=True)
class SourceContextInfo:
    scope: str
    thread_id: int | None
    frame: int | None
    function: str | None
    address: str | None
    file: str
    fullname: str | None
    line: int | None
    start_line: int
    end_line: int
    lines: list[SourceLineRecord]
    count: int
```

```python
# src/gdb_mcp/mcp/schemas.py

class AddInferiorArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    executable: str | None = Field(
        None,
        description="Optional executable to associate with the new inferior after creation.",
    )
    make_current: bool = Field(False, description="Whether to leave the new inferior selected")

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("executable must be a non-empty string")
        return text


class RemoveInferiorArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    inferior_id: int = Field(..., gt=0, description="Inferior ID to remove")


class FinishArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class DisassembleArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int | None = Field(None, description="Optional thread override")
    frame: int | None = Field(None, description="Optional frame override")
    function: str | None = Field(None, description="Function name selector")
    address: str | None = Field(None, description="Single address selector")
    start_address: str | None = Field(None, description="Start of explicit address range")
    end_address: str | None = Field(None, description="End of explicit address range")
    file: str | None = Field(None, description="Source file selector")
    line: int | None = Field(None, description="Source line selector")
    instruction_count: int = Field(32, gt=0, description="Upper bound on returned instructions")
    mode: Literal["assembly", "mixed"] = Field("mixed")

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str | None) -> int | None:
        return _coerce_int_like(value, field_name="frame", minimum=0, allow_none=True)

    @field_validator("line")
    @classmethod
    def validate_line(cls, value: int | str | None) -> int | None:
        return _coerce_int_like(value, field_name="line", minimum=1, allow_none=True)

    @model_validator(mode="after")
    def validate_selector_mode(self) -> "DisassembleArgs":
        # enforce one selector mode only
        return self


class GetSourceContextArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int | None = Field(None, description="Optional thread override")
    frame: int | None = Field(None, description="Optional frame override")
    function: str | None = Field(None, description="Function name selector")
    address: str | None = Field(None, description="Address selector")
    file: str | None = Field(None, description="Source file selector")
    line: int | None = Field(None, description="Source line selector")
    start_line: int | None = Field(None, description="Start of explicit line range")
    end_line: int | None = Field(None, description="End of explicit line range")
    context_before: int = Field(5, ge=0, description="Lines before focal line")
    context_after: int = Field(5, ge=0, description="Lines after focal line")

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: int | str | None) -> int | None:
        return _coerce_int_like(value, field_name="thread_id", minimum=1, allow_none=True)

    @field_validator("frame")
    @classmethod
    def validate_frame(cls, value: int | str | None) -> int | None:
        return _coerce_int_like(value, field_name="frame", minimum=0, allow_none=True)

    @field_validator("line", "start_line", "end_line")
    @classmethod
    def validate_lines(cls, value: int | str | None, info: ValidationInfo) -> int | None:
        return _coerce_int_like(value, field_name=info.field_name, minimum=1, allow_none=True)

    @model_validator(mode="after")
    def validate_selector_mode(self) -> "GetSourceContextArgs":
        # enforce one selector mode only and line/range compatibility
        return self
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_schemas.py -k 'InferiorLifecycleArgs or ExtendedRunArgs or DisassembleArgs or SourceContextArgs'`
Expected: all new schema tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/domain/models.py src/gdb_mcp/mcp/schemas.py tests/mcp/test_schemas.py
git commit -m "Add schemas for expanded MCP debugger tools"
```

### Task 2: Route New Tools Through MCP Handlers And Session Service

**Files:**
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `src/gdb_mcp/session/service.py`
- Modify: `tests/mcp/test_handlers.py`
- Modify: `src/gdb_mcp/mcp/schemas.py`

**Testing approach:** `TDD`
Reason: Handler routing is a clean behavior seam. Tests can verify that the new tool names dispatch to the correct service methods and that normalized selector arguments are forwarded exactly once.

- [ ] **Step 1: Write failing handler tests for the new tools**

```python
# tests/mcp/test_handlers.py

def test_add_inferior_routes_to_correct_session(dispatch, manager, session):
    session.add_inferior.return_value = OperationSuccess({"inferior_id": 2})

    dispatch(
        "gdb_add_inferior",
        {"session_id": 4, "executable": "/tmp/app", "make_current": True},
        manager,
    )

    session.add_inferior.assert_called_once_with(
        executable="/tmp/app",
        make_current=True,
    )


def test_remove_inferior_routes_to_correct_session(dispatch, manager, session):
    session.remove_inferior.return_value = OperationSuccess({"inferior_id": 2})

    dispatch("gdb_remove_inferior", {"session_id": 4, "inferior_id": 2}, manager)

    session.remove_inferior.assert_called_once_with(inferior_id=2)


def test_run_forwards_wait_for_stop(dispatch, manager, session):
    session.run.return_value = OperationSuccess({"command": "-exec-run"})

    dispatch(
        "gdb_run",
        {"session_id": 4, "args": "--flag value", "wait_for_stop": False, "timeout_sec": 5},
        manager,
    )

    session.run.assert_called_once_with(
        args=["--flag", "value"],
        timeout_sec=5,
        wait_for_stop=False,
    )


def test_finish_routes_to_execution_service(dispatch, manager, session):
    session.finish.return_value = OperationSuccess({"message": "finished"})

    dispatch("gdb_finish", {"session_id": 4, "timeout_sec": 9}, manager)

    session.finish.assert_called_once_with(timeout_sec=9)


def test_disassemble_routes_normalized_selectors(dispatch, manager, session):
    session.disassemble.return_value = OperationSuccess({"count": 0, "instructions": []})

    dispatch(
        "gdb_disassemble",
        {"session_id": 4, "thread_id": "2", "frame": "1", "instruction_count": 12},
        manager,
    )

    session.disassemble.assert_called_once_with(
        thread_id=2,
        frame=1,
        function=None,
        address=None,
        start_address=None,
        end_address=None,
        file=None,
        line=None,
        instruction_count=12,
        mode="mixed",
    )


def test_get_source_context_routes_normalized_selectors(dispatch, manager, session):
    session.get_source_context.return_value = OperationSuccess({"count": 0, "lines": []})

    dispatch(
        "gdb_get_source_context",
        {"session_id": 4, "file": "main.c", "line": "12", "context_before": 2, "context_after": 3},
        manager,
    )

    session.get_source_context.assert_called_once_with(
        thread_id=None,
        frame=None,
        function=None,
        address=None,
        file="main.c",
        line=12,
        start_line=None,
        end_line=None,
        context_before=2,
        context_after=3,
    )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'add_inferior or remove_inferior or wait_for_stop or finish or disassemble or source_context'`
Expected: fails because the new handler routes and service methods do not exist yet.

- [ ] **Step 3: Implement the handler/service wiring**

```python
# src/gdb_mcp/session/service.py

def add_inferior(
    self,
    *,
    executable: str | None = None,
    make_current: bool = False,
) -> OperationSuccess[InferiorAddInfo] | OperationError:
    return self._execution.add_inferior(executable=executable, make_current=make_current)


def remove_inferior(self, inferior_id: int) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
    return self._execution.remove_inferior(inferior_id)


def finish(self, timeout_sec: int = 30) -> OperationSuccess[FinishInfo] | OperationError:
    return self._execution.finish(timeout_sec=timeout_sec)


def disassemble(... ) -> OperationSuccess[DisassemblyInfo] | OperationError:
    return self._inspection.disassemble(...)


def get_source_context(... ) -> OperationSuccess[SourceContextInfo] | OperationError:
    return self._inspection.get_source_context(...)
```

```python
# src/gdb_mcp/mcp/handlers.py

def _handle_add_inferior(session: SessionService, args: AddInferiorArgs) -> ToolResult:
    return session.add_inferior(executable=args.executable, make_current=args.make_current)


def _handle_remove_inferior(session: SessionService, args: RemoveInferiorArgs) -> ToolResult:
    return session.remove_inferior(args.inferior_id)


def _handle_run(session: SessionService, args: RunArgs) -> ToolResult:
    run_args = _normalize_argv(args.args)
    if isinstance(run_args, OperationError):
        return run_args
    return session.run(args=run_args, timeout_sec=args.timeout_sec, wait_for_stop=args.wait_for_stop)


def _handle_finish(session: SessionService, args: FinishArgs) -> ToolResult:
    return session.finish(timeout_sec=args.timeout_sec)


def _handle_disassemble(session: SessionService, args: DisassembleArgs) -> ToolResult:
    return session.disassemble(
        thread_id=args.thread_id,
        frame=args.frame,
        function=args.function,
        address=args.address,
        start_address=args.start_address,
        end_address=args.end_address,
        file=args.file,
        line=args.line,
        instruction_count=args.instruction_count,
        mode=args.mode,
    )


def _handle_get_source_context(session: SessionService, args: GetSourceContextArgs) -> ToolResult:
    return session.get_source_context(
        thread_id=args.thread_id,
        frame=args.frame,
        function=args.function,
        address=args.address,
        file=args.file,
        line=args.line,
        start_line=args.start_line,
        end_line=args.end_line,
        context_before=args.context_before,
        context_after=args.context_after,
    )
```

```python
# src/gdb_mcp/mcp/schemas.py
# add new tool names in BATCH_STEP_TOOL_NAMES / BatchStepToolName and build_tool_definitions()
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'add_inferior or remove_inferior or wait_for_stop or finish or disassemble or source_context'`
Expected: all focused handler tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/mcp/handlers.py src/gdb_mcp/session/service.py src/gdb_mcp/mcp/schemas.py tests/mcp/test_handlers.py
git commit -m "Route expanded MCP debugger tools through handlers"
```

### Task 3: Implement Inferior Lifecycle And Extended Run Semantics

**Files:**
- Modify: `src/gdb_mcp/session/execution.py`
- Modify: `src/gdb_mcp/session/command_runner.py`
- Modify: `tests/session/test_execution_api.py`
- Modify: `tests/integration/test_gdb_integration.py`

**Testing approach:** `TDD`
Reason: This task changes observable runtime behavior and session state. The execution API tests are a direct seam for red-green verification before implementation.

- [ ] **Step 1: Write failing execution tests for add/remove inferior and non-blocking run**

```python
# tests/session/test_execution_api.py

def test_run_wait_for_stop_false_returns_running_success(scripted_running_session, mi_result):
    session, _controller = scripted_running_session([mi_result(message="running")])

    result = result_to_mapping(session.run(wait_for_stop=False, timeout_sec=1))

    assert result["status"] == "success"
    assert result["command"] == "-exec-run"
    assert session.runtime.execution_state == "running"


def test_add_inferior_updates_inventory(scripted_running_session, mi_result, mi_console):
    session, _controller = scripted_running_session(
        [mi_result({"inferior": "i2"})],
        [
            mi_console("  Num  Description       Connection           Executable        \n"),
            mi_console("* 1    <null>                                 /tmp/app \n"),
            mi_console("  2    <null>                                                   \n"),
            mi_result(),
        ],
    )

    result = result_to_mapping(session.add_inferior())

    assert result["status"] == "success"
    assert result["inferior_id"] == 2
    assert result["inferior_count"] == 2


def test_remove_inferior_updates_inventory(scripted_running_session, mi_result, mi_console):
    session, _controller = scripted_running_session(
        [mi_result()],
        [
            mi_console("  Num  Description       Connection           Executable        \n"),
            mi_console("* 1    <null>                                 /tmp/app \n"),
            mi_result(),
        ],
    )
    session.runtime.update_inferior_inventory(current_inferior_id=2, count=2, inferior_ids=(1, 2))
    session.runtime.mark_inferior_selected(2)

    result = result_to_mapping(session.remove_inferior(2))

    assert result["status"] == "success"
    assert result["inferior_id"] == 2
    assert result["current_inferior_id"] == 1
    assert result["inferior_count"] == 1
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/session/test_execution_api.py -k 'wait_for_stop_false or add_inferior or remove_inferior'`
Expected: fails because `run` lacks the new parameter and inferior lifecycle methods are not implemented.

- [ ] **Step 3: Implement execution-layer behavior**

```python
# src/gdb_mcp/session/execution.py

def run(
    self,
    args: list[str] | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    *,
    wait_for_stop: bool = True,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    if not self._runtime.has_controller:
        return OperationError(message="No active GDB session")

    if args:
        result = self._command_runner.execute_command_result(
            build_exec_arguments_command(args),
            timeout_sec=timeout_sec,
        )
        if isinstance(result, OperationError):
            return result

    return self._command_runner.execute_command_result(
        "-exec-run",
        timeout_sec=timeout_sec,
        allow_running_timeout=not wait_for_stop,
    )


def add_inferior(
    self,
    *,
    executable: str | None = None,
    make_current: bool = False,
) -> OperationSuccess[InferiorAddInfo] | OperationError:
    # call -add-inferior, parse iN -> N, optionally assign executable, refresh inventory
    ...


def remove_inferior(self, inferior_id: int) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
    # call -remove-inferior iN and refresh inventory
    ...
```

```python
# src/gdb_mcp/session/command_runner.py
# add a helper for parsing MI inferior IDs like "i2" into int 2 if needed
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/session/test_execution_api.py -k 'wait_for_stop_false or add_inferior or remove_inferior'`
Expected: focused execution tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/session/execution.py src/gdb_mcp/session/command_runner.py tests/session/test_execution_api.py tests/integration/test_gdb_integration.py
git commit -m "Implement inferior lifecycle and non-blocking run"
```

### Task 4: Implement Finish Execution Control

**Files:**
- Modify: `src/gdb_mcp/session/execution.py`
- Modify: `tests/session/test_execution_api.py`
- Modify: `tests/integration/test_gdb_integration.py`

**Testing approach:** `TDD`
Reason: `gdb_finish` is a new execution-control behavior with a crisp output contract. A failing test can drive both the result parsing and runtime-state expectations.

- [ ] **Step 1: Write failing tests for finish**

```python
# tests/session/test_execution_api.py

def test_finish_surfaces_return_value_and_frame(scripted_running_session, mi_result):
    session, _controller = scripted_running_session(
        [
            mi_result(
                {
                    "gdb-result-var": "$1",
                    "return-value": "42",
                    "frame": {"level": "0", "func": "caller", "file": "main.c", "line": "12"},
                }
            )
        ]
    )

    result = result_to_mapping(session.finish(timeout_sec=9))

    assert result["status"] == "success"
    assert result["return_value"] == "42"
    assert result["gdb_result_var"] == "$1"
    assert result["frame"]["func"] == "caller"
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/session/test_execution_api.py -k finish`
Expected: fails because `finish` does not exist yet.

- [ ] **Step 3: Implement finish**

```python
# src/gdb_mcp/session/execution.py

def finish(self, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> OperationSuccess[FinishInfo] | OperationError:
    result = self._command_runner.execute_command_result(
        "-exec-finish",
        timeout_sec=timeout_sec,
    )
    if isinstance(result, OperationError):
        return result

    payload = command_result_payload(result)
    raw = extract_mi_result_payload(payload)
    frame = frame_record(raw.get("frame")) if isinstance(raw, dict) else None
    return_value = raw.get("return-value") if isinstance(raw, dict) else None
    gdb_result_var = raw.get("gdb-result-var") if isinstance(raw, dict) else None
    return OperationSuccess(
        FinishInfo(
            message="Frame finished",
            return_value=return_value if isinstance(return_value, str) else None,
            gdb_result_var=gdb_result_var if isinstance(gdb_result_var, str) else None,
            frame=frame,
            execution_state=self._runtime.execution_state,
            stop_reason=self._runtime.stop_reason,
            last_stop_event=self._runtime.last_stop_event,
        )
    )
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/session/test_execution_api.py -k finish`
Expected: finish-focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/session/execution.py tests/session/test_execution_api.py tests/integration/test_gdb_integration.py
git commit -m "Add structured finish execution tool"
```

### Task 5: Implement Structured Disassembly

**Files:**
- Modify: `src/gdb_mcp/session/inspection.py`
- Modify: `src/gdb_mcp/domain/models.py`
- Modify: `tests/session/test_inspection_api.py`
- Modify: `tests/integration/test_gdb_integration.py`

**Testing approach:** `TDD`
Reason: Disassembly output shape and selector/restoration behavior are new observable contracts. Inspection tests are the correct seam to characterize them before implementation.

- [ ] **Step 1: Write failing disassembly tests**

```python
# tests/session/test_inspection_api.py

def test_disassemble_current_context(scripted_running_session, mi_result):
    session, _controller = scripted_running_session(
        [
            mi_result(
                {
                    "asm_insns": [
                        {"address": "0x401000", "inst": "push %rbp"},
                        {"address": "0x401001", "inst": "mov %rsp,%rbp"},
                    ]
                }
            )
        ]
    )

    result = result_to_mapping(session.disassemble(instruction_count=2, mode="assembly"))

    assert result["status"] == "success"
    assert result["count"] == 2
    assert result["instructions"][0]["address"] == "0x401000"
    assert result["mode"] == "assembly"


def test_disassemble_with_thread_and_frame_restores_selection(scripted_running_session, mi_result):
    session, controller = scripted_running_session(
        [mi_result({"threads": [{"id": "1"}, {"id": "2"}], "current-thread-id": "1"})],
        [mi_result({"frame": {"level": "0", "func": "main"}})],
        [mi_result()],
        [mi_result()],
        [mi_result({"asm_insns": []})],
        [mi_result()],
        [mi_result()],
    )

    result = result_to_mapping(session.disassemble(thread_id=2, frame=1))

    assert result["status"] == "success"
    written = [command.decode() for command in controller.io_manager.stdin.writes]
    assert written[-2].endswith("-thread-select 1\n")
    assert written[-1].endswith("-stack-select-frame 0\n")
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/session/test_inspection_api.py -k disassemble`
Expected: fails because `disassemble` does not exist yet.

- [ ] **Step 3: Implement disassembly**

```python
# src/gdb_mcp/session/inspection.py

def disassemble(
    self,
    *,
    thread_id: int | None = None,
    frame: int | None = None,
    function: str | None = None,
    address: str | None = None,
    start_address: str | None = None,
    end_address: str | None = None,
    file: str | None = None,
    line: int | None = None,
    instruction_count: int = 32,
    mode: Literal["assembly", "mixed"] = "mixed",
) -> OperationSuccess[DisassemblyInfo] | OperationError:
    # resolve one selector mode, optionally capture/restore selection,
    # call -data-disassemble, flatten MI output into instruction records
    ...
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/session/test_inspection_api.py -k disassemble`
Expected: disassembly-focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/session/inspection.py src/gdb_mcp/domain/models.py tests/session/test_inspection_api.py tests/integration/test_gdb_integration.py
git commit -m "Add structured disassembly inspection tool"
```

### Task 6: Implement Structured Source Context

**Files:**
- Modify: `src/gdb_mcp/session/inspection.py`
- Modify: `tests/session/test_inspection_api.py`
- Modify: `tests/integration/test_gdb_integration.py`

**Testing approach:** `TDD`
Reason: Source-context behavior is mostly about resolution logic and returned line windows. It has a strong automated seam in the inspection service with temporary test files and scripted GDB responses.

- [ ] **Step 1: Write failing source-context tests**

```python
# tests/session/test_inspection_api.py

def test_get_source_context_file_line_selector(tmp_path, session_service):
    source_file = tmp_path / "main.c"
    source_file.write_text("int main() {\n    return 0;\n}\n")

    result = result_to_mapping(
        session_service.get_source_context(
            file=str(source_file),
            line=2,
            context_before=1,
            context_after=0,
        )
    )

    assert result["status"] == "success"
    assert result["file"] == str(source_file)
    assert result["start_line"] == 1
    assert result["end_line"] == 2
    assert result["lines"][1]["line_number"] == 2
    assert result["lines"][1]["is_current"] is True
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/session/test_inspection_api.py -k source_context`
Expected: fails because `get_source_context` does not exist yet.

- [ ] **Step 3: Implement source-context retrieval**

```python
# src/gdb_mcp/session/inspection.py

def get_source_context(
    self,
    *,
    thread_id: int | None = None,
    frame: int | None = None,
    function: str | None = None,
    address: str | None = None,
    file: str | None = None,
    line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    context_before: int = 5,
    context_after: int = 5,
) -> OperationSuccess[SourceContextInfo] | OperationError:
    # resolve one source location, read lines from disk, and return structured line records
    ...
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/session/test_inspection_api.py -k source_context`
Expected: source-context-focused tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/session/inspection.py tests/session/test_inspection_api.py tests/integration/test_gdb_integration.py
git commit -m "Add structured source context inspection tool"
```

### Task 7: Update Public Docs And End-To-End Examples

**Files:**
- Modify: `README.md`
- Modify: `TOOLS.md`
- Modify: `examples/USAGE_GUIDE.md`
- Test/Verify: docs diff plus focused runtime tests already added above

**Testing approach:** `existing tests + targeted verification`
Reason: This task is documentation alignment after behavior is already implemented and covered. The main verification is that docs accurately match the shipped API and stop teaching raw-command fallbacks where structured tools now exist.

- [ ] **Step 1: Capture the stale docs that still recommend raw commands**

```bash
rg -n 'gdb_execute_command|run&|run &|disassemble|info files|info breakpoints|info sources' README.md TOOLS.md examples/USAGE_GUIDE.md
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `rg -n 'gdb_execute_command|run&|run &|disassemble|info files|info breakpoints|info sources' README.md TOOLS.md examples/USAGE_GUIDE.md`
Expected: shows the outdated guidance that will be replaced or narrowed to true escape-hatch cases.

- [ ] **Step 3: Implement the docs updates**

```markdown
# README.md
- `gdb_add_inferior` - Create a new inferior
- `gdb_remove_inferior` - Remove an inferior by ID
- `gdb_finish` - Finish the current frame and report any return value
- `gdb_disassemble` - Return structured assembly or mixed source/assembly for a resolved location
- `gdb_get_source_context` - Return structured source lines around a resolved location

- `gdb_run` accepts `wait_for_stop=false` for non-blocking launch workflows.
```

```markdown
# TOOLS.md
### `gdb_add_inferior`
### `gdb_remove_inferior`
### `gdb_finish`
### `gdb_disassemble`
### `gdb_get_source_context`

# update `gdb_run` parameters with `wait_for_stop`
```

```markdown
# examples/USAGE_GUIDE.md
- replace raw `run` examples with `gdb_run`
- replace raw `run&` with `gdb_run(wait_for_stop=false)`
- replace raw `disassemble` with `gdb_disassemble`
- replace source-listing examples with `gdb_get_source_context`
- narrow remaining `gdb_execute_command` usage to true escape-hatch cases
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/integration/test_gdb_integration.py -k 'inferior or finish or disassemble or source_context or wait_helpers'`
Expected: end-to-end coverage for the documented workflows passes.

- [ ] **Step 5: Commit**

```bash
git add README.md TOOLS.md examples/USAGE_GUIDE.md tests/integration/test_gdb_integration.py
git commit -m "Document expanded structured debugger tools"
```

### Task 8: Final Verification Sweep

**Files:**
- Modify: none
- Test/Verify: full relevant verification commands

**Testing approach:** `existing tests + targeted verification`
Reason: This is the final evidence pass before completion. It ensures the combined change set across schemas, handlers, runtime code, tests, and docs is internally consistent.

- [ ] **Step 1: Run the focused full-suite verification**

```bash
uv run pytest -q tests/mcp/test_schemas.py tests/mcp/test_handlers.py tests/session/test_execution_api.py tests/session/test_inspection_api.py tests/integration/test_gdb_integration.py
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_schemas.py tests/mcp/test_handlers.py tests/session/test_execution_api.py tests/session/test_inspection_api.py tests/integration/test_gdb_integration.py`
Expected: all targeted suites pass with the new tool surface covered end-to-end.

- [ ] **Step 3: Run static checks**

```bash
uv run ruff check src tests
uv run mypy src
git diff --check
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run ruff check src tests && uv run mypy src && git diff --check`
Expected: Ruff passes, mypy reports no issues, and `git diff --check` is clean.

- [ ] **Step 5: Commit**

```bash
git status --short
# Expect clean working tree after previous task commits and final verification.
```
