# MCP Tool Interface Revamp Design

**Date:** 2026-04-02

## Goal

Replace the current one-operation-per-tool MCP surface with a coherent domain-oriented API that:

- consolidates closely related tools under `*_manage` and `*_query` boundaries
- uses strict action-scoped payloads instead of broad flat argument models
- preserves explicit standalone tools for privileged or escape-hatch behavior
- gives clients a more uniform request, response, and error contract

## Background

The current server exposes 42 MCP tools. That structure has two strengths:

- most operations are explicit and easy to permission in isolation
- schemas, handlers, docs, and tests already enforce strong public-surface parity

It also has several costs that have grown with tool expansion:

1. Closely related operations are split across many tool names, especially around breakpoints, inferiors, execution control, and context inspection.
2. Similar selector and validation logic is duplicated across multiple argument models and handlers.
3. Batch and workflow tooling must carry large hard-coded tool-name allowlists.
4. The response surface is mostly flat and inconsistent, which makes client-side routing and validation harder than necessary.

This redesign is a deliberate clean break. It does not preserve legacy tool names as aliases.

## Scope

This design changes the public MCP surface for structured debugger operations, including:

- tool inventory and naming
- request schema structure
- action routing conventions
- result and error envelope conventions
- batch/workflow step shape
- README and TOOLS documentation structure
- MCP schema and handler tests that assert exported tool parity and behavior

## Non-Goals

- preserving backward compatibility with the legacy structured tool names
- removing `gdb_execute_command`
- redesigning `SessionService` internals beyond what the new MCP boundary requires
- changing transport-level MCP behavior beyond tool schemas and serialized payloads
- introducing MCP `outputSchema`, resources, or event streams as part of this change

## Design Principles

1. Consolidate by debugger domain, not by arbitrary implementation layer.
2. Split read-only query behavior from mutating manage behavior.
3. Keep privileged actions explicit and separately permissionable.
4. Use discriminated unions keyed by `action` instead of permissive models with many optional fields.
5. Normalize common selectors and response envelopes across domains.
6. Keep orchestration tools generic rather than embedding workflow semantics into each domain tool.

## Public Tool Inventory

The recommended v2 surface contains 17 tools.

### Session Lifecycle

- `gdb_session_start`
- `gdb_session_query`
- `gdb_session_manage`

`gdb_session_start` remains separate because startup has a unique request shape and should not be forced into an `action=start` union.

### Inferiors And Fork Workflow

- `gdb_inferior_query`
- `gdb_inferior_manage`

This domain owns inferior inventory, current inferior state, selection, creation/removal, and fork-follow configuration.

### Execution Control

- `gdb_execution_manage`

Execution remains one operational manage tool because run/continue/interrupt/step/wait are all stateful control operations rather than independent query surfaces.

### Breakpoints

- `gdb_breakpoint_query`
- `gdb_breakpoint_manage`

This domain unifies code breakpoints, watchpoints, and catchpoints behind one conceptual breakpoint model.

### Thread And Frame Context

- `gdb_context_query`
- `gdb_context_manage`

This domain owns thread inventory, backtrace retrieval, frame inspection, and explicit thread/frame selection.

### Data Inspection

- `gdb_inspect_query`

This domain owns expression evaluation, variable inspection, register inspection, memory reads, disassembly, and source-context retrieval.

### Workflow And Specialized Operations

- `gdb_workflow_batch`
- `gdb_capture_bundle`
- `gdb_run_until_failure`

These tools compose or automate other operations rather than representing one debugger domain, so they remain top-level specialized tools.

### Explicit Standalone Tools

- `gdb_execute_command`
- `gdb_attach_process`
- `gdb_call_function`

These remain explicit because they are privileged or intentionally outside the consolidated structured API.

## Request Model Conventions

### Top-Level Fields

All consolidated tools follow the same routing pattern:

- `action` is always top-level
- `session_id` is top-level for session-scoped actions
- global actions omit `session_id`

Examples:

```json
{
  "action": "list",
  "query": {}
}
```

```json
{
  "session_id": 7,
  "action": "status",
  "query": {}
}
```

### Action-Scoped Nested Payloads

Mutable tools use one singular domain payload:

- `session`
- `inferior`
- `execution`
- `breakpoint`
- `context`

Read-only tools use `query`.

This keeps each action isolated to one nested validation boundary and avoids models filled with irrelevant optional fields.

### Strict Action Validation

The v2 schemas must reject:

- missing action-required payload fields
- fields that belong to a different action
- invalid combinations that were formerly enforced by ad hoc handler logic

This should be implemented with discriminated unions keyed by `action`, not with large permissive models and after-the-fact branching.

### Query Tools Are Non-Mutating By Contract

`gdb_context_query`, `gdb_inspect_query`, and session-scoped query actions may temporarily override current thread, frame, or inferior internally. They must restore the prior selection before returning unless the tool is explicitly a `*_manage` tool.

### Selector Normalization

- Prefer JSON numbers for `session_id`, `thread_id`, `frame`, `inferior_id`, and breakpoint numbers.
- Continue accepting numeric strings only where compatibility with MI-style IDs is materially useful.
- Reuse selector names across tools instead of inventing tool-specific near-duplicates.

## Action Inventory

### `gdb_session_start`

No `action`. This remains a dedicated startup tool.

### `gdb_session_query`

- `list`
- `status`

`list` is global and does not require `session_id`. `status` is session-scoped and does.

### `gdb_session_manage`

- `stop`

This stays narrow in the first version. Future mutable session-level actions can be added here if needed.

### `gdb_inferior_query`

- `list`
- `current`

### `gdb_inferior_manage`

- `create`
- `remove`
- `select`
- `set_follow_fork_mode`
- `set_detach_on_fork`

### `gdb_execution_manage`

- `run`
- `continue`
- `interrupt`
- `step`
- `next`
- `finish`
- `wait_for_stop`

`wait_for_stop` belongs here even though it is observational, because it is execution synchronization rather than general inspection.

### `gdb_breakpoint_query`

- `list`
- `get`

### `gdb_breakpoint_manage`

- `create`
- `delete`
- `enable`
- `disable`
- `update`

`create` covers code breakpoints, watchpoints, and catchpoints via `breakpoint.kind`.

`update` is reserved as a first-class action so the redesign is more than a naming cleanup. The initial implementation can support a smaller set of fields, but the design should explicitly define update semantics.

### `gdb_context_query`

- `threads`
- `backtrace`
- `frame`

### `gdb_context_manage`

- `select_thread`
- `select_frame`

Inferior selection does not belong here. It remains part of inferior management.

### `gdb_inspect_query`

- `evaluate`
- `variables`
- `registers`
- `memory`
- `disassembly`
- `source`

### `gdb_workflow_batch`

No `action`. Each step invokes one of the new consolidated tool names with its normal payload shape.

### `gdb_capture_bundle`

No `action`.

### `gdb_run_until_failure`

No `action`.

### Explicit Standalone Tools

- `gdb_execute_command`
- `gdb_attach_process`
- `gdb_call_function`

## Detailed Request Shapes

### `gdb_breakpoint_manage`

The top-level shape is always `session_id`, `action`, and `breakpoint`.

For `create`, `breakpoint` is a discriminated union on `kind`.

Code breakpoint:

```json
{
  "session_id": 7,
  "action": "create",
  "breakpoint": {
    "kind": "code",
    "location": "src/main.c:42",
    "condition": "count > 100",
    "temporary": false
  }
}
```

Watchpoint:

```json
{
  "session_id": 7,
  "action": "create",
  "breakpoint": {
    "kind": "watch",
    "expression": "state->ready",
    "access": "write"
  }
}
```

Catchpoint:

```json
{
  "session_id": 7,
  "action": "create",
  "breakpoint": {
    "kind": "catch",
    "event": "syscall",
    "argument": "open",
    "temporary": false
  }
}
```

For `delete`, `enable`, and `disable`, the payload stays narrow:

```json
{
  "session_id": 7,
  "action": "disable",
  "breakpoint": {
    "number": 4
  }
}
```

For `update`, use an explicit `changes` object rather than null-clearing semantics:

```json
{
  "session_id": 7,
  "action": "update",
  "breakpoint": {
    "number": 4,
    "changes": {
      "condition": "count > 100",
      "clear_condition": false
    }
  }
}
```

### `gdb_breakpoint_query`

List:

```json
{
  "session_id": 7,
  "action": "list",
  "query": {
    "kinds": ["code", "watch"],
    "enabled": true
  }
}
```

Get one breakpoint:

```json
{
  "session_id": 7,
  "action": "get",
  "query": {
    "number": 4
  }
}
```

### `gdb_execution_manage`

Execution actions should normalize waiting behavior instead of splitting it into inconsistent booleans and ad hoc timeout handling.

Recommended pattern:

- `run`, `continue`, `step`, `next`, and `finish` accept an optional `wait` object
- `wait.until` supports at least `acknowledged` and `stop`

Run with argv override:

```json
{
  "session_id": 7,
  "action": "run",
  "execution": {
    "args": ["--mode", "fast"],
    "wait": {
      "until": "stop",
      "timeout_sec": 30
    }
  }
}
```

Continue and return when GDB acknowledges running:

```json
{
  "session_id": 7,
  "action": "continue",
  "execution": {
    "wait": {
      "until": "acknowledged"
    }
  }
}
```

Explicit stop wait:

```json
{
  "session_id": 7,
  "action": "wait_for_stop",
  "execution": {
    "timeout_sec": 10,
    "stop_reasons": ["breakpoint-hit", "signal-received"]
  }
}
```

### `gdb_context_query`

Threads:

```json
{
  "session_id": 7,
  "action": "threads",
  "query": {}
}
```

Backtrace:

```json
{
  "session_id": 7,
  "action": "backtrace",
  "query": {
    "thread_id": 3,
    "max_frames": 20
  }
}
```

Frame inspection:

```json
{
  "session_id": 7,
  "action": "frame",
  "query": {
    "thread_id": 3,
    "frame": 1
  }
}
```

### `gdb_context_manage`

Select thread:

```json
{
  "session_id": 7,
  "action": "select_thread",
  "context": {
    "thread_id": 3
  }
}
```

Select frame:

```json
{
  "session_id": 7,
  "action": "select_frame",
  "context": {
    "frame": 1
  }
}
```

### `gdb_inspect_query`

Use one shared optional `context` object plus an explicit `location` union for source and disassembly selectors.

Expression evaluation:

```json
{
  "session_id": 7,
  "action": "evaluate",
  "query": {
    "context": {
      "thread_id": 3,
      "frame": 1
    },
    "expression": "node->count"
  }
}
```

Register inspection:

```json
{
  "session_id": 7,
  "action": "registers",
  "query": {
    "context": {
      "thread_id": 3,
      "frame": 0
    },
    "register_names": ["rax", "rip"],
    "value_format": "hex"
  }
}
```

Memory reads:

```json
{
  "session_id": 7,
  "action": "memory",
  "query": {
    "address": "buffer",
    "count": 64,
    "offset": 0
  }
}
```

Disassembly:

```json
{
  "session_id": 7,
  "action": "disassembly",
  "query": {
    "location": {
      "kind": "function",
      "function": "process_data"
    },
    "instruction_count": 24,
    "mode": "mixed"
  }
}
```

Source context:

```json
{
  "session_id": 7,
  "action": "source",
  "query": {
    "location": {
      "kind": "file_line",
      "file": "src/main.c",
      "line": 42
    },
    "context_before": 5,
    "context_after": 5
  }
}
```

The `location.kind` union should support:

- `current`
- `address`
- `address_range`
- `function`
- `file_line`
- `file_range`

That explicit location union replaces the current pattern of many optional mutually-exclusive fields with after-the-fact validation.

## Response And Error Conventions

### Success Envelope

Every successful v2 response should include:

- `status`: always `"success"`
- `action`: present on action-based tools
- `result`: action-specific structured payload
- `warnings`: optional human-readable warnings
- `message`: optional short summary

Example:

```json
{
  "status": "success",
  "action": "create",
  "result": {
    "breakpoint": {
      "number": 4,
      "kind": "code",
      "location": "main",
      "enabled": true,
      "temporary": false
    }
  }
}
```

List responses should keep collections inside `result` instead of flattening them into the top-level payload:

```json
{
  "status": "success",
  "action": "list",
  "result": {
    "breakpoints": [
      {
        "number": 4,
        "kind": "code",
        "enabled": true
      }
    ],
    "count": 1
  }
}
```

### State And Event Reporting

When an action changes execution or selection state, the response should expose the resulting state in explicit nested objects instead of ad hoc top-level fields.

Recommended conventions:

- `result.session`: lightweight post-action session state when relevant
- `result.stop_event`: included when an action produces or observes a stop
- `result.selection`: included for thread/frame/inferior selection actions

Example:

```json
{
  "status": "success",
  "action": "continue",
  "result": {
    "session": {
      "execution_state": "paused",
      "current_inferior_id": 1,
      "current_thread_id": 3,
      "current_frame": 0
    },
    "stop_event": {
      "reason": "breakpoint-hit",
      "thread_id": 3,
      "frame": {
        "func": "main",
        "file": "src/main.c",
        "line": 42
      }
    }
  }
}
```

### Error Envelope

Every error response should include:

- `status`: always `"error"`
- `code`: required machine-readable error code
- `message`: deterministic human-readable summary
- `action`: included when known
- `details`: optional structured error context
- `fatal`: optional and present only when the session or tool state is no longer usable

Example:

```json
{
  "status": "error",
  "action": "create",
  "code": "validation_error",
  "message": "breakpoint.location is required for kind=code",
  "details": {
    "field_errors": [
      {
        "field": "breakpoint.location",
        "issue": "missing"
      }
    ]
  }
}
```

### Recommended Stable Error Codes

- `validation_error`
- `unknown_action`
- `unsupported_combination`
- `invalid_state`
- `not_found`
- `timeout`
- `permission_denied`
- `transport_error`
- `gdb_error`
- `internal_error`

The exact list can grow, but these codes must be stable and documented.

### Batch Error Reporting

`gdb_workflow_batch` step failures should report:

- `step_index`
- `tool`
- `action` when available
- `code`
- `message`

That is especially important in v2 because failures can occur at both tool-name and action-routing layers.

## Implementation Structure

### MCP Boundary Redesign

The primary surface changes belong in:

- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`

In `schemas.py`:

- replace most one-model-per-tool request shapes with discriminated unions per consolidated tool
- replace `BATCH_STEP_TOOL_NAMES` and `BatchStepToolName` with the v2 tool inventory
- keep `gdb_session_start`, `gdb_workflow_batch`, `gdb_execute_command`, `gdb_attach_process`, `gdb_call_function`, `gdb_capture_bundle`, and `gdb_run_until_failure` as explicit top-level schemas
- add shared nested schema primitives for selectors, action payloads, and location unions

In `handlers.py`:

- replace the many-small public dispatch registry with per-tool action dispatch
- keep top-level handlers narrow and delegate internally by `action`
- avoid moving unrelated business logic into giant monolithic handler functions

Recommended handler grouping:

- `_handle_session_query`
- `_handle_session_manage`
- `_handle_inferior_query`
- `_handle_inferior_manage`
- `_handle_execution_manage`
- `_handle_breakpoint_query`
- `_handle_breakpoint_manage`
- `_handle_context_query`
- `_handle_context_manage`
- `_handle_inspect_query`

### Internal Consolidation

Recommended internal cleanup while adapting the new MCP boundary:

- extract shared thread/frame selector normalization helpers
- extract shared source/disassembly location validation helpers
- add a normalized breakpoint-operation layer so code/watch/catch creation all route through one MCP family even if the session service still uses distinct methods internally
- add a response-builder layer for v2 envelopes so handlers do not handcraft `result` payloads inconsistently

This redesign should not force a broad `SessionService` rewrite unless implementation reveals a real gap.

### Workflow Tool Updates

`gdb_workflow_batch` must be updated to operate on the new tool names and normal action-scoped payloads.

`gdb_run_until_failure` remains a separate higher-level workflow tool, but any embedded setup steps should also adopt the new batch-step request format.

### Documentation Rewrite

`README.md` and `TOOLS.md` both require structural updates rather than small incremental edits.

Recommended doc shape:

- `README.md`: explain the domain-oriented tool model and the high-level inventory
- `TOOLS.md`: one section per v2 tool, with action tables and action-specific examples
- add a migration appendix that maps legacy tool names to replacement tools and actions

### Test Strategy

Schema tests in `tests/mcp/test_schemas.py` should cover:

- action-union validation for each consolidated tool
- rejection of wrong-action fields
- batch-step allowlist coverage for the new tool inventory
- selector normalization and location-union validation

Handler tests in `tests/mcp/test_handlers.py` should cover:

- per-tool and per-action routing
- v2 success and error envelope shapes
- batch step error shape including `tool`, `action`, and `step_index`

Integration tests under `tests/integration/` should be rewritten around high-value end-to-end flows:

- session start plus status
- breakpoint create/list/disable/delete
- run/continue/wait workflows
- thread/backtrace/frame inspection
- memory/source/disassembly inspection
- inferior and fork workflows
- batch and run-until-failure flows

### Suggested Implementation Order

1. Add shared v2 schema primitives and response-envelope helpers.
2. Implement `gdb_session_query`, `gdb_session_manage`, `gdb_context_query`, `gdb_context_manage`, and `gdb_execution_manage`.
3. Implement `gdb_breakpoint_query` and `gdb_breakpoint_manage`.
4. Implement `gdb_inferior_query` and `gdb_inferior_manage`.
5. Implement `gdb_inspect_query`.
6. Update `gdb_workflow_batch`.
7. Update `gdb_run_until_failure`.
8. Rewrite documentation.
9. Rewrite and expand tests to fully match the new public surface.

## Migration Appendix

This is a clean break, but documentation should still include a migration map so existing users can translate old habits quickly.

Suggested mappings:

- `gdb_start_session` -> `gdb_session_start`
- `gdb_list_sessions` -> `gdb_session_query(action="list")`
- `gdb_get_status` -> `gdb_session_query(action="status")`
- `gdb_stop_session` -> `gdb_session_manage(action="stop")`
- `gdb_list_inferiors` -> `gdb_inferior_query(action="list")`
- `gdb_select_inferior` -> `gdb_inferior_manage(action="select")`
- `gdb_add_inferior` -> `gdb_inferior_manage(action="create")`
- `gdb_remove_inferior` -> `gdb_inferior_manage(action="remove")`
- `gdb_set_follow_fork_mode` -> `gdb_inferior_manage(action="set_follow_fork_mode")`
- `gdb_set_detach_on_fork` -> `gdb_inferior_manage(action="set_detach_on_fork")`
- `gdb_run` -> `gdb_execution_manage(action="run")`
- `gdb_continue` -> `gdb_execution_manage(action="continue")`
- `gdb_interrupt` -> `gdb_execution_manage(action="interrupt")`
- `gdb_step` -> `gdb_execution_manage(action="step")`
- `gdb_next` -> `gdb_execution_manage(action="next")`
- `gdb_finish` -> `gdb_execution_manage(action="finish")`
- `gdb_wait_for_stop` -> `gdb_execution_manage(action="wait_for_stop")`
- `gdb_set_breakpoint` -> `gdb_breakpoint_manage(action="create", breakpoint.kind="code")`
- `gdb_set_watchpoint` -> `gdb_breakpoint_manage(action="create", breakpoint.kind="watch")`
- `gdb_set_catchpoint` -> `gdb_breakpoint_manage(action="create", breakpoint.kind="catch")`
- `gdb_list_breakpoints` -> `gdb_breakpoint_query(action="list")`
- `gdb_delete_breakpoint` -> `gdb_breakpoint_manage(action="delete")`
- `gdb_delete_watchpoint` -> `gdb_breakpoint_manage(action="delete")`
- `gdb_enable_breakpoint` -> `gdb_breakpoint_manage(action="enable")`
- `gdb_disable_breakpoint` -> `gdb_breakpoint_manage(action="disable")`
- `gdb_get_threads` -> `gdb_context_query(action="threads")`
- `gdb_get_backtrace` -> `gdb_context_query(action="backtrace")`
- `gdb_get_frame_info` -> `gdb_context_query(action="frame")`
- `gdb_select_thread` -> `gdb_context_manage(action="select_thread")`
- `gdb_select_frame` -> `gdb_context_manage(action="select_frame")`
- `gdb_evaluate_expression` -> `gdb_inspect_query(action="evaluate")`
- `gdb_get_variables` -> `gdb_inspect_query(action="variables")`
- `gdb_get_registers` -> `gdb_inspect_query(action="registers")`
- `gdb_read_memory` -> `gdb_inspect_query(action="memory")`
- `gdb_disassemble` -> `gdb_inspect_query(action="disassembly")`
- `gdb_get_source_context` -> `gdb_inspect_query(action="source")`
- `gdb_batch` -> `gdb_workflow_batch`

## Summary

The recommended redesign is a coherent domain API:

- 17 tools instead of 42
- read/write split by domain
- action-scoped payloads with strict validation
- explicit standalone tools for privileged and escape-hatch behavior
- generic orchestration kept separate from domain actions
- uniform success and error envelopes with stable machine-readable error codes

This gives the server a public surface that is easier to document, validate, compose, and extend without turning it into a single opaque mega-dispatcher.
