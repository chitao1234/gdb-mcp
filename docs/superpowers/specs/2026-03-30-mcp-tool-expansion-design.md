# MCP Tool Expansion Design

**Date:** 2026-03-30

## Goal

Expand the exposed MCP tool surface so common debugging workflows no longer need to fall back to `gdb_execute_command` for inferior lifecycle management, asynchronous launch, disassembly, stepping out of frames, or source-context retrieval.

## Background

The current tool set already covers the core structured debugging loop well:

- session lifecycle and status
- attach/run/continue/wait/interrupt
- inferior listing and selection
- thread and frame navigation
- breakpoints, watchpoints, and catchpoints
- expression evaluation, memory reads, variables, registers
- batch workflows and forensic capture

However, several important workflows still depend on `gdb_execute_command`:

- `add-inferior`
- `run&`
- `disassemble`
- source-listing commands such as `list`
- ad hoc “step out” using raw GDB command vocabulary

This creates three problems:

1. Agents have to know raw GDB command syntax for common tasks.
2. Clients get text output where a structured response would be more reliable.
3. Documentation and examples keep teaching the escape hatch instead of the structured API.

## Scope

This design adds or changes the following client-visible tools:

- Add `gdb_add_inferior`
- Add `gdb_remove_inferior`
- Extend `gdb_run`
- Add `gdb_disassemble`
- Add `gdb_finish`
- Add `gdb_get_source_context`

## Non-Goals

- Replacing `gdb_execute_command` entirely
- Retrofitting every existing inspection tool around a new generic selector abstraction
- Adding a symbol-browser/search family beyond what this request requires
- Implementing output schemas for the entire MCP server as part of this change

## Design Overview

The preferred implementation strategy is a mixed backend with a normalized structured surface:

- Use MI-backed commands where GDB already exposes machine-readable data.
- Use narrow CLI or local-file helpers only where MI is weak.
- Keep selection restoration rules aligned with existing inspection tools.
- Keep execution-control tools stateful and explicit rather than hiding debugger state changes.

### Service Placement

- Inferior lifecycle tools belong with the current inferior inventory/selection area.
- `gdb_run` extension and `gdb_finish` belong in the execution service.
- `gdb_disassemble` and `gdb_get_source_context` belong in the inspection service.

### State Semantics

- Inferior lifecycle tools may change selected inferior by design.
- Execution-control tools change execution state by design.
- Read-only inspection tools must restore temporary thread/frame overrides.
- Direct location selectors such as `function`, `address`, or `file`/`line` must not mutate debugger selection.

## Tool Changes

### `gdb_add_inferior`

Create a new inferior as a first-class structured operation.

**Request**

```python
class AddInferiorArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    executable: str | None = Field(
        None,
        description=(
            "Optional executable to associate with the new inferior after creation."
        ),
    )
    make_current: bool = Field(
        False,
        description="When true, leave the new inferior selected after the call.",
    )
```

**Response**

```python
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
```

**Behavior**

- Create the inferior using MI `-add-inferior`.
- Normalize MI thread-group IDs such as `i3` into public `inferior_id=3`.
- If `executable` is provided, associate it with the new inferior after creation.
- If `make_current=false`, restore the previously selected inferior after any temporary switch.
- Refresh inferior inventory before responding so `current_inferior_id` and `inferior_count` are stable.

### `gdb_remove_inferior`

Remove one inferior as a first-class structured operation.

**Request**

```python
class RemoveInferiorArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    inferior_id: int = Field(..., gt=0, description="Inferior ID to remove")
```

**Response**

```python
@dataclass(slots=True, frozen=True)
class InferiorRemoveInfo:
    inferior_id: int
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    message: str | None = None
```

**Behavior**

- Accept numeric inferior IDs in the public API.
- Translate `inferior_id=N` into MI form `iN` for `-remove-inferior`.
- Refresh inferior inventory after removal and return the new current inferior and count.
- Return deterministic error messages when GDB refuses removal of an active inferior.

### `gdb_run`

Extend launch semantics so callers can start execution without using raw `run&`.

**Request**

```python
class RunArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    args: list[str] | str | None = Field(
        None,
        description=(
            "Override inferior arguments for this run. "
            "Accepts either an explicit argv list or one shell-style string."
        ),
    )
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")
    wait_for_stop: bool = Field(
        True,
        description=(
            "When true, wait for the same stop/prompt behavior as today. "
            "When false, return success once GDB acknowledges running."
        ),
    )
```

**Response**

- Keep using `CommandExecutionInfo`.
- When `wait_for_stop=false` and execution is acknowledged but still running, return success with a warning instead of a timeout error.

**Behavior**

- `wait_for_stop=true` preserves current behavior.
- `wait_for_stop=false` becomes the structured replacement for raw `run&`.
- The response must direct clients to `gdb_wait_for_stop` or `gdb_interrupt` for subsequent synchronization.

### `gdb_finish`

Add a dedicated step-out tool for finishing the current frame.

**Request**

```python
class FinishArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")
```

**Response**

```python
@dataclass(slots=True, frozen=True)
class FinishInfo:
    message: str
    return_value: str | None = None
    gdb_result_var: str | None = None
    frame: FrameRecord | None = None
    execution_state: str | None = None
    stop_reason: str | None = None
    last_stop_event: StopEvent | None = None
```

**Behavior**

- Use MI `-exec-finish`.
- This tool is current-context only in v1.
- Surface any returned value when GDB provides one.
- Preserve the standard execution-state and stop-event reporting style already used by execution helpers.

### `gdb_disassemble`

Add structured disassembly with broad selector support.

**Request**

```python
class DisassembleArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int | str | None = Field(None, description="Optional thread override")
    frame: int | str | None = Field(None, description="Optional frame override")
    function: str | None = Field(None, description="Function name to disassemble")
    address: str | None = Field(None, description="Single address selector")
    start_address: str | None = Field(None, description="Start of explicit address range")
    end_address: str | None = Field(None, description="End of explicit address range")
    file: str | None = Field(None, description="Source file selector")
    line: int | str | None = Field(None, description="Source line selector")
    instruction_count: int = Field(32, gt=0, description="Upper bound on returned instructions")
    mode: Literal["assembly", "mixed"] = Field(
        "mixed",
        description="Whether to request assembly only or mixed source/assembly output",
    )
```

**Response**

```python
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
```

**Behavior**

- Use MI `-data-disassemble`.
- Normalize mixed-mode output into one flat instruction list instead of exposing raw nested MI layout.
- Include source metadata per instruction when available.
- Mark the current PC instruction when it can be inferred.

### `gdb_get_source_context`

Add structured source retrieval with broad selector support.

**Request**

```python
class GetSourceContextArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int | str | None = Field(None, description="Optional thread override")
    frame: int | str | None = Field(None, description="Optional frame override")
    function: str | None = Field(None, description="Function name selector")
    address: str | None = Field(None, description="Address selector")
    file: str | None = Field(None, description="Source file selector")
    line: int | str | None = Field(None, description="Source line selector")
    start_line: int | str | None = Field(None, description="Start of explicit line range")
    end_line: int | str | None = Field(None, description="End of explicit line range")
    context_before: int = Field(5, ge=0, description="Lines before focal line")
    context_after: int = Field(5, ge=0, description="Lines after focal line")
```

**Response**

```python
class SourceLineRecord(TypedDict, total=False):
    line_number: int
    text: str
    is_current: bool


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

**Behavior**

- Resolve a single location first, then read source from disk into normalized line records.
- For current-context and thread/frame selectors, derive file/line from current frame info.
- For `function` or `address`, use GDB to resolve to file/line before reading the file.
- For explicit `file`/`line` or `file`/range selectors, read directly from disk.
- Mark the focal line with `is_current=true` when one line is the selected center.

## Selector Rules

Broad selectors are allowed, but each request must resolve to exactly one selector mode.

### `gdb_disassemble` selector modes

- Current-context mode: optional `thread_id` and `frame`, with no direct location fields
- Function mode: `function`
- Address mode: `address`
- Address-range mode: `start_address` and `end_address`
- File-line mode: `file` and `line`

### `gdb_get_source_context` selector modes

- Current-context mode: optional `thread_id` and `frame`, with no direct location fields
- Function mode: `function`
- Address mode: `address`
- File-line mode: `file` and `line`
- File-range mode: `file`, `start_line`, and `end_line`

### Validation constraints

- `thread_id > 0`
- `frame >= 0`
- `line >= 1`
- `start_line >= 1`
- `end_line >= 1`
- `start_line <= end_line`
- `instruction_count > 0`
- `context_before >= 0`
- `context_after >= 0`
- `line` cannot be combined with `start_line` or `end_line`
- `start_line` and `end_line` must appear together
- selector groups are mutually exclusive and must not be guessed

## Runtime Behavior And Restoration

### `gdb_add_inferior`

- May temporarily switch inferiors when associating an executable.
- Must restore the previous inferior unless `make_current=true`.

### `gdb_remove_inferior`

- No restoration. This is an explicit lifecycle mutation.

### `gdb_run`

- No restoration. This is an execution-state mutation.

### `gdb_finish`

- No restoration. This is an execution-control command like `step` or `next`.

### `gdb_disassemble`

- If using `thread_id` and/or `frame`, capture and restore thread/frame selection exactly like existing inspection tools.
- If using `function`, `address`, `address range`, or `file`/`line`, do not mutate debugger selection.

### `gdb_get_source_context`

- Same restoration behavior as `gdb_disassemble`.
- Thread/frame selectors are temporary inspection overrides.
- Direct location selectors must not change debugger context.

## Backend Mapping

- `gdb_add_inferior`: MI `-add-inferior`, then optional executable association and inventory refresh
- `gdb_remove_inferior`: MI `-remove-inferior`
- `gdb_run`: existing MI launch path with extended running-acknowledged behavior
- `gdb_finish`: MI `-exec-finish`
- `gdb_disassemble`: MI `-data-disassemble`
- `gdb_get_source_context`: GDB-assisted location resolution plus local file reads

## Testing Plan

### Schema tests

- Selector mutual-exclusion validation
- Numeric-string coercion for thread/frame/line fields
- `gdb_run.wait_for_stop` defaulting and validation
- Invalid combinations such as `function` plus `file`

### Handler tests

- New tools route to the correct `SessionService` methods
- `gdb_run` forwards `wait_for_stop`
- Selector arguments are normalized before service dispatch

### Session/service tests

- Inferior add/remove updates runtime inventory
- Add-with-executable restores prior inferior when `make_current=false`
- Non-blocking run returns success-on-running instead of timeout error
- Finish surfaces `return_value` and `gdb_result_var`
- Disassemble normalizes MI output into stable instruction records
- Source-context returns the expected line window and focal-line marker

### Integration tests

- Add inferior, select it, remove it
- Run with `wait_for_stop=false`, then wait for the stop
- Finish from a nested function
- Disassemble using current-frame and explicit selectors
- Source-context using current-frame and explicit selectors

## Documentation Plan

Update the following together with implementation:

- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`
- `README.md`
- `TOOLS.md`
- `examples/USAGE_GUIDE.md`

Examples and guidance should stop recommending raw `run`, `run&`, `disassemble`, or source-listing commands when the new structured tools cover those workflows.

## Compatibility Notes

- `gdb_execute_command` remains available as an escape hatch.
- `gdb_run` remains backward compatible by keeping `wait_for_stop=true` as the default.
- The new tools are additive and should not break existing clients.

## Open Decisions Resolved In This Design

- Inferior lifecycle is first-class: both add and remove are included.
- `gdb_run` is extended rather than replaced.
- `gdb_disassemble` and `gdb_get_source_context` return structured outputs, not text blobs.
- Selector support is broad from v1, with strict mutual-exclusion validation.
- `gdb_finish` stays current-context only in v1 to avoid hidden selector-induced execution changes.
