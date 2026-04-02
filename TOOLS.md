# GDB MCP Server - Tools Reference

This document describes the current v2 MCP interface. The public surface is a clean break from the earlier one-tool-per-operation inventory.

All examples in this file use current v2 tool names. Historical names appear only in the migration appendix at the end.

## Response Conventions

### Direct Success Payloads

Tools without an `action` field return direct structured success payloads:

- `gdb_session_start`
- `gdb_workflow_batch`
- `gdb_capture_bundle`
- `gdb_run_until_failure`
- `gdb_execute_command`
- `gdb_attach_process`
- `gdb_call_function`

Example:

```json
{
  "status": "success",
  "session_id": 7,
  "message": "GDB session started successfully",
  "target_loaded": true,
  "execution_state": "not_started"
}
```

### Action-Based Success Envelope

Action-based tools return:

```json
{
  "status": "success",
  "action": "status",
  "result": {
    "is_running": true,
    "target_loaded": true,
    "execution_state": "paused"
  }
}
```

The wrapped `result` object is action-specific.

### Error Envelope

Errors are uniform across all tools:

```json
{
  "status": "error",
  "code": "validation_error",
  "message": "breakpoint.location is required for kind=code",
  "action": "create",
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

Common machine-readable codes include:

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

## Shared Request Patterns

### `session_id`

Every session-scoped tool takes a `session_id` returned by `gdb_session_start`.

### Action-Scoped Payloads

Action families use one nested payload object per domain:

- `query`
- `session`
- `inferior`
- `execution`
- `breakpoint`
- `changes`
- `context`

Dedicated workflow tools use their own top-level objects such as `startup`, `failure`, `capture`, and `steps`.

### Empty Payload Objects

Some actions still require an explicit empty object for strict validation:

- `gdb_session_query(action="list", query={})`
- `gdb_session_manage(action="stop", session={})`
- `gdb_context_query(action="threads", query={})`

### Thread/Frame Context

Inspection requests may carry an optional context override:

```json
{
  "context": {
    "thread_id": 3,
    "frame": 1
  }
}
```

### Location Selector Union

`gdb_inspect_query(action="disassembly" | "source")` uses an explicit `location` union:

- `{"kind":"current"}`
- `{"kind":"function","function":"main"}`
- `{"kind":"address","address":"0x401000"}`
- `{"kind":"address_range","start_address":"0x401000","end_address":"0x401040"}`
- `{"kind":"file_line","file":"src/main.c","line":42}`
- `{"kind":"file_range","file":"src/main.c","start_line":40,"end_line":48}`

### Batch Steps

`gdb_workflow_batch.steps` and `gdb_run_until_failure.setup_steps` use this format:

```json
{
  "tool": "gdb_context_query",
  "label": "stack",
  "arguments": {
    "action": "backtrace",
    "query": {}
  }
}
```

Shorthand string steps are also allowed:

```json
"gdb_context_query"
```

Batch steps never include `session_id`; the enclosing workflow injects it.
In practice, string shorthand is most useful for dedicated tools with empty or defaultable arguments. Action-based tools usually need full object form so `action` is explicit.

## Tool Inventory

| Tool | Role |
| --- | --- |
| `gdb_session_start` | Start a new GDB session |
| `gdb_session_query` | Query session inventory or one live session |
| `gdb_session_manage` | Mutate session lifecycle |
| `gdb_inferior_query` | Query inferior inventory or current inferior |
| `gdb_inferior_manage` | Create, remove, select, or reconfigure inferiors |
| `gdb_execution_manage` | Run, continue, interrupt, step, next, finish, or wait |
| `gdb_breakpoint_query` | List or fetch breakpoints |
| `gdb_breakpoint_manage` | Create, update, delete, enable, or disable breakpoints |
| `gdb_context_query` | List threads, backtraces, or frame info |
| `gdb_context_manage` | Select thread or frame |
| `gdb_inspect_query` | Evaluate expressions and inspect program state |
| `gdb_workflow_batch` | Execute a structured multi-step workflow in one session |
| `gdb_capture_bundle` | Write a forensic bundle to disk |
| `gdb_run_until_failure` | Repeat fresh runs until failure predicates match |
| `gdb_execute_command` | Escape hatch for CLI or MI commands |
| `gdb_attach_process` | Privileged attach-by-PID |
| `gdb_call_function` | Privileged function execution in the target |

## `gdb_session_start`

Dedicated startup tool. This is not action-based.

### Request Fields

- `program`: optional executable path
- `args`: optional argv override as `list[str]` or shell-style string
- `core`: optional core-dump path
- `init_commands`: optional list of GDB commands to run after environment setup
- `env`: optional environment mapping applied before `init_commands`
- `gdb_path`: optional GDB binary override
- `working_dir`: optional working directory for the GDB process

`args` and `core` are mutually exclusive.

### Success Fields

- `status`
- `session_id`
- `message`
- `program`
- `core`
- `target_loaded`
- `execution_state`
- `stop_reason`
- `exit_code`
- `startup_output`
- `warnings`
- `env_output`
- `init_output`

### Example

```json
{
  "program": "/path/to/app",
  "args": ["--mode", "fast"],
  "init_commands": [
    "set pagination off"
  ]
}
```

## `gdb_session_query`

Query session inventory or inspect one live session.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `list` | `{"action":"list","query":{}}` | `sessions`, `count` |
| `status` | `{"session_id":7,"action":"status","query":{}}` | `is_running`, `target_loaded`, `has_controller`, `execution_state`, `stop_reason`, `exit_code`, `current_inferior_id`, `inferior_count`, `inferior_states`, `follow_fork_mode`, `detach_on_fork` |

### Example

```json
{
  "session_id": 7,
  "action": "status",
  "query": {}
}
```

## `gdb_session_manage`

Mutate session lifecycle state.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `stop` | `{"session_id":7,"action":"stop","session":{}}` | `message` |

## `gdb_inferior_query`

Query inferior state inside one live session.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `list` | `{"session_id":7,"action":"list","query":{}}` | `inferiors`, `count`, `current_inferior_id` |
| `current` | `{"session_id":7,"action":"current","query":{}}` | `inferior` |

## `gdb_inferior_manage`

Create, remove, select, or reconfigure inferiors.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `create` | `{"session_id":7,"action":"create","inferior":{"executable":"/path/app","make_current":true}}` | `inferior_id`, `is_current`, `display`, `description`, `connection`, `executable`, `current_inferior_id`, `inferior_count`, `message` |
| `remove` | `{"session_id":7,"action":"remove","inferior":{"inferior_id":2}}` | `inferior_id`, `current_inferior_id`, `inferior_count`, `message` |
| `select` | `{"session_id":7,"action":"select","inferior":{"inferior_id":2}}` | `inferior_id`, `is_current`, `display`, `description`, `connection`, `executable`, `message` |
| `set_follow_fork_mode` | `{"session_id":7,"action":"set_follow_fork_mode","inferior":{"mode":"child"}}` | `mode`, `message` |
| `set_detach_on_fork` | `{"session_id":7,"action":"set_detach_on_fork","inferior":{"enabled":false}}` | `enabled`, `message` |

## `gdb_execution_manage`

Run or synchronize execution state.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `run` | `{"session_id":7,"action":"run","execution":{"args":["--mode","fast"],"wait":{"until":"stop","timeout_sec":30}}}` | direct execution payload under `result` |
| `continue` | `{"session_id":7,"action":"continue","execution":{"wait":{"until":"acknowledged"}}}` | direct execution payload under `result` |
| `interrupt` | `{"session_id":7,"action":"interrupt","execution":{}}` | direct execution payload under `result` |
| `step` | `{"session_id":7,"action":"step","execution":{"wait":{"until":"stop","timeout_sec":30}}}` | direct execution payload under `result` |
| `next` | `{"session_id":7,"action":"next","execution":{"wait":{"until":"stop","timeout_sec":30}}}` | direct execution payload under `result` |
| `finish` | `{"session_id":7,"action":"finish","execution":{"wait":{"until":"stop","timeout_sec":30}}}` | `message`, `return_value`, `gdb_result_var`, `frame`, `execution_state`, `stop_reason`, `last_stop_event` |
| `wait_for_stop` | `{"session_id":7,"action":"wait_for_stop","execution":{"timeout_sec":10,"stop_reasons":["breakpoint-hit"]}}` | `message`, `matched`, `timed_out`, `source`, `execution_state`, `stop_reason`, `reason_filter`, `last_stop_event` |

### Wait Policy

`run`, `continue`, `step`, `next`, and `finish` accept:

```json
{
  "wait": {
    "until": "acknowledged",
    "timeout_sec": 30
  }
}
```

`until` can be:

- `acknowledged`
- `stop`

### Notes

- `run`, `continue`, `step`, `next`, and `interrupt` wrap the structured command result produced by GDB. Inside `result`, expect `command`, optional text `output`, and optional machine-readable `result`.
- `wait_for_stop` is the structured replacement for manual polling.

## `gdb_breakpoint_query`

Query breakpoints.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `list` | `{"session_id":7,"action":"list","query":{"kinds":["code","watch"],"enabled":true}}` | `breakpoints`, `count` |
| `get` | `{"session_id":7,"action":"get","query":{"number":4}}` | `breakpoint` |

`list` filters are optional.

## `gdb_breakpoint_manage`

Create or mutate code breakpoints, watchpoints, and catchpoints.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `create` | `{"session_id":7,"action":"create","breakpoint":{...}}` | `breakpoint` |
| `update` | `{"session_id":7,"action":"update","breakpoint":{"number":4},"changes":{"condition":"count > 100","clear_condition":false}}` | `breakpoint` |
| `delete` | `{"session_id":7,"action":"delete","breakpoint":{"number":4}}` | `message` |
| `enable` | `{"session_id":7,"action":"enable","breakpoint":{"number":4}}` | `message` |
| `disable` | `{"session_id":7,"action":"disable","breakpoint":{"number":4}}` | `message` |

### `create` Variants

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

### `update` Semantics

`changes` currently supports:

- `condition`
- `clear_condition`

`condition` and `clear_condition=true` are mutually exclusive.

## `gdb_context_query`

Read thread and frame state without mutating selection.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `threads` | `{"session_id":7,"action":"threads","query":{}}` | `threads`, `current_thread_id`, `count` |
| `backtrace` | `{"session_id":7,"action":"backtrace","query":{"thread_id":3,"max_frames":20}}` | `thread_id`, `frames`, `count` |
| `frame` | `{"session_id":7,"action":"frame","query":{"thread_id":3,"frame":1}}` | `frame` |

All `backtrace` and `frame` selectors are optional. If omitted, the current selection is used without mutating it.

## `gdb_context_manage`

Mutate the current thread or frame selection.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `select_thread` | `{"session_id":7,"action":"select_thread","context":{"thread_id":3}}` | `thread_id`, `new_thread_id`, `frame` |
| `select_frame` | `{"session_id":7,"action":"select_frame","context":{"frame":1}}` | `frame_number`, `frame`, `message` |

## `gdb_inspect_query`

Read program state without using raw debugger commands.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `evaluate` | `{"session_id":7,"action":"evaluate","query":{"context":{"thread_id":3,"frame":1},"expression":"node->count"}}` | `expression`, `value` |
| `variables` | `{"session_id":7,"action":"variables","query":{"context":{"thread_id":3,"frame":0}}}` | `thread_id`, `frame`, `variables` |
| `registers` | `{"session_id":7,"action":"registers","query":{"context":{"thread_id":3,"frame":0},"register_names":["rax","rip"],"value_format":"hex"}}` | `registers` |
| `memory` | `{"session_id":7,"action":"memory","query":{"address":"buffer","count":64,"offset":0}}` | `address`, `count`, `offset`, `blocks`, `block_count`, `captured_bytes` |
| `disassembly` | `{"session_id":7,"action":"disassembly","query":{"location":{"kind":"function","function":"process_data"},"instruction_count":24,"mode":"mixed"}}` | `scope`, `thread_id`, `frame`, `function`, `file`, `fullname`, `line`, `start_address`, `end_address`, `mode`, `instructions`, `count` |
| `source` | `{"session_id":7,"action":"source","query":{"location":{"kind":"file_line","file":"src/main.c","line":42},"context_before":5,"context_after":5}}` | `scope`, `thread_id`, `frame`, `function`, `address`, `file`, `fullname`, `line`, `start_line`, `end_line`, `lines`, `count` |

### Register Filters

`registers` supports these optional selectors:

- `register_numbers`
- `register_names`
- `include_vector_registers`
- `max_registers`
- `value_format`

### Location Notes

- `disassembly` accepts `function`, `address`, `address_range`, `file_line`, `file_range`, or `current`.
- `source` accepts the same location union.

## `gdb_workflow_batch`

Execute a structured sequence of session-scoped tools under one workflow lock.

### Request Fields

- `session_id`
- `steps`
- `fail_fast`
- `capture_stop_events`

### Step Rules

- `session_id` is inherited from the batch and must not appear in any step.
- `gdb_session_query(action="list")` is not allowed inside a batch.
- `gdb_session_manage` is not allowed inside a batch.
- Nested `gdb_workflow_batch` and `gdb_run_until_failure` steps are not allowed.

### Success Fields

- `status`
- `steps`
- `count`
- `completed_steps`
- `error_count`
- `stopped_early`
- `failure_step_index`
- `final_execution_state`
- `final_stop_reason`
- `last_stop_event`

Each step result includes:

- `index`
- `tool`
- `label`
- `status`
- `action`
- `code`
- `result`
- `stop_event`

`steps[].result` preserves the full wrapped result of the inner action-based tool call.

## `gdb_capture_bundle`

Write a structured forensic bundle to disk for one live session.

### Request Fields

- `session_id`
- `output_dir`
- `bundle_name`
- `expressions`
- `memory_ranges`
- `max_frames`
- `include_threads`
- `include_backtraces`
- `include_frame`
- `include_variables`
- `include_registers`
- `include_transcript`
- `include_stop_history`

`memory_ranges` accepts either object form:

```json
{
  "address": "&value",
  "count": 16,
  "offset": 0,
  "name": "value-bytes"
}
```

or shorthand string form:

```json
"&value:16@0"
```

### Success Fields

- `status`
- `message`
- `bundle_dir`
- `bundle_name`
- `manifest_path`
- `artifacts`
- `artifact_count`
- `failed_sections`
- `execution_state`
- `stop_reason`
- `last_stop_event`

Each artifact contains `name`, `path`, `status`, and `kind`.

## `gdb_run_until_failure`

Repeat fresh debugger sessions until failure predicates match or the iteration limit is reached.

### Request Fields

- `startup`: full `gdb_session_start` payload reused for every iteration
- `setup_steps`: optional batch-style setup steps run before `run`
- `run_args`: optional argv override for the run phase
- `run_timeout_sec`
- `max_iterations`
- `failure`
- `capture`

### `failure` Fields

- `failure_on_error`
- `failure_on_timeout`
- `stop_reasons`
- `execution_states`
- `exit_codes`
- `result_text_regex`

### `capture` Fields

- `enabled`
- `output_dir`
- `bundle_name_prefix`
- `bundle_name`
- `expressions`
- `memory_ranges`
- `max_frames`
- `include_threads`
- `include_backtraces`
- `include_frame`
- `include_variables`
- `include_registers`
- `include_transcript`
- `include_stop_history`

`capture.bundle_name` and `capture.bundle_name_prefix` are mutually exclusive.

### Success Fields

- `status`
- `message`
- `matched_failure`
- `iterations_requested`
- `iterations_completed`
- `failure_iteration`
- `trigger`
- `execution_state`
- `stop_reason`
- `exit_code`
- `capture_bundle`
- `capture_error`
- `last_result`
- `iterations`

Each `iterations[]` entry contains:

- `iteration`
- `status`
- `execution_state`
- `stop_reason`
- `exit_code`
- `matched_failure`
- `trigger`
- `message`

## `gdb_execute_command`

Escape hatch for CLI or MI commands. This is not action-based.

### Request Fields

- `session_id`
- `command`
- `timeout_sec`

### Success Fields

- `status`
- `command`
- `output`
- `result`

Use this when no dedicated structured tool exists. Prefer the structured families when they already cover the operation.

## `gdb_attach_process`

Attach to a running process by PID. This is a privileged tool.

### Request Fields

- `session_id`
- `pid`
- `timeout_sec`

### Success Fields

- `status`
- `command`
- `output`
- `result`

## `gdb_call_function`

Execute a function call in the target process. This is a privileged tool.

### Request Fields

- `session_id`
- `function_call`
- `timeout_sec`

### Success Fields

- `status`
- `function_call`
- `result`

Example:

```json
{
  "session_id": 7,
  "function_call": "printf(\"debug: x=%d\\n\", x)",
  "timeout_sec": 30
}
```

## Migration Appendix

Historical tool names map to the v2 surface as follows. Keep new clients, prompts, skills, and examples on the v2 names above.

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
