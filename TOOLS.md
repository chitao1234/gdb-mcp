# GDB MCP Server - Tools Reference

This document is the contract for the MCP tools this server exposes: every tool,
its actions, request fields, response fields, and error codes.

## Transport Note

The tool payloads and responses in this reference are transport-independent. You can expose the same MCP surface over:

- stdio with the default `gdb-mcp-server` launch
- streamable HTTP with `gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp`

Only the connection method changes. Tool names, arguments, and response envelopes stay the same.

## CLI Client Note

The same tool inventory is available through the first-party `gdb-mcp-client`
command, which starts and reuses a background server per project unless you point
it at one with `--server-url`:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  tool breakpoint_manage \
  --session-id 7 \
  --action create \
  --breakpoint-kind code \
  --location main
```

Debugging shortcuts such as `break add main`, `run`, `bt`, and `exec "info files"` wrap the same tools, and `--session-id` defaults to the session started most recently.

Add `--json` when you want the structured payload instead of human-oriented text.

Set `GDB_MCP_SERVER_URL` to avoid repeating `--server-url`. `--payload-json '<json>'` sends a raw tool payload (mutually exclusive with field flags); every action-based subcommand lists validated payload examples in `--help`. `gdb_workflow_batch` exits non-zero when the response reports `error_count > 0`.

## Response Conventions

Every tool result carries the same payload twice: as `structuredContent` (structured tool output) and as serialized JSON in `content[0].text` (for clients that only read text). Both are always the envelope described below.

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

Errors are uniform across all tools and are returned as MCP tool results with `isError: true`:

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
        "issue": "missing",
        "message": "breakpoint.location is required for kind=code"
      }
    ]
  }
}
```

Validation failures use `code="validation_error"` and populate `details.field_errors`; the top-level `message` lists every failure, including the selecting union tag when one applies, and is not truncated.

Session-scoped failures also carry the session's state in `details` (`session_id`, `execution_state`, `stop_reason`, `exit_code`), so a failure whose message is only `No registers.` still tells the caller that the inferior exited.

Common machine-readable codes include:

- `validation_error`, `unsupported_combination` — the request was rejected before any debugger work
- `invalid_session`, `session_closing` — the session is gone or shutting down
- `no_registers`, `inferior_not_running`, `inferior_running`, `no_stack_frame`, `memory_unavailable`, `symbol_unavailable`, `no_symbols_loaded`, `value_unavailable`, `command_timeout` — classified GDB failures
- `unknown_tool`, `internal_error`, `not_found` — dispatch and lookup failures
- `error` — fallback when a GDB failure matches no specific classification

## Shared Request Patterns

### `session_id`

Every session-scoped tool takes a `session_id` returned by `gdb_session_start`.

### Published Tool Schemas

Published tool input schemas are flattened for broad MCP client compatibility: they contain no `oneOf`, `anyOf`, `allOf`, `$ref`, or `$defs` constructs.
Each action-scoped payload is advertised as one object whose discriminator (`action`, `kind`) is an `enum` of the accepted values, requirements that only apply to some values are spelled out in the schema description under `Conditional requirements:`, and a payload whose accepted fields differ per value lists them under `Accepted fields depend on <discriminator>:`.
Requests are validated against the typed request models rather than by re-validating the published schema, so the schema stays a descriptive client contract while the models remain authoritative.
High-complexity tools also publish request `examples` inside their `inputSchema`.

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

Actions whose payload has no fields accept an omitted payload; passing an explicit empty object stays valid:

- `gdb_session_query(action="list")` or `gdb_session_query(action="list", query={})`
- `gdb_session_manage(action="stop")` or `gdb_session_manage(action="stop", session={})`
- `gdb_context_query(action="threads")` or `gdb_context_query(action="threads", query={})`

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
| `gdb_session_start` | Start a new GDB session (gdb-mcp spawns gdb) |
| `gdb_session_connect` | Connect to an externally started GDB/MI endpoint |
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
- `gdb_args`: optional extra GDB command-line arguments, inserted before the program
- `working_dir`: optional working directory for the GDB process
- `rr_replay`: optional rr trace directory to replay instead of launching a program

`args` and `core` are mutually exclusive. With `rr_replay` the recording already
owns the target, so the session runs `rr replay --interpreter=mi <trace>` instead
of a plain `gdb`; a `program` passed alongside it is ignored and reported in
`warnings`, while `args` and `core` are rejected. The `RR_PATH` environment
variable (default `rr`) selects the rr binary, and `gdb_path` is passed to rr as
its `-d` debugger choice. With `rr_replay`, `gdb_args` are forwarded to the
debugger rr starts, after rr's `--` separator.

`gdb_args` are passed verbatim as argv elements, so an option that takes a
separate operand needs two entries (`["-iex", "set print elements 0"]`). GDB
loads a `program` given on its command line during its own startup, so this is
the only channel for settings that must apply *before* the target is read:
auto-load `safe-path` and script search directories. `init_commands` run
afterwards and cannot enable auto-loading. Entries that would replace the MI
interpreter (`-i`, `--interpreter`), duplicate the target wiring (`--args`,
`core`/`--pid`/`--attach`) or sit bare without a preceding option are rejected
with `validation_error`.

Language support arrives as GDB scripts a binary embeds in `.debug_gdb_scripts`,
and GDB refuses to run them unless their directory is trusted. When that happens
`gdb_session_start` reports it in `warnings` and names the fix, for example
Rust's pretty-printers (needs both the search directory and the trust entry,
because rustc embeds a bare file name):

```json
{
  "program": "/path/to/app",
  "gdb_args": ["--directory=/path/to/rustlib/etc", "-iex", "add-auto-load-safe-path /path/to/rustlib/etc"]
}
```

Go embeds an absolute path, so the trust entry alone is enough; a kernel build
needs `add-auto-load-safe-path` for its build tree. `gdb_path` pointing at a
wrapper such as `rust-gdb` achieves the same by injecting those options.

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

## `gdb_session_connect`

Dedicated tool for attaching to a GDB/MI stream that **another process already
started**. gdb-mcp does not spawn gdb in this mode, and stopping the session
only detaches: it never terminates the external gdb.

### Endpoint Forms

| Form | Meaning |
| --- | --- |
| `tcp://host:port` | GDB/MI exposed over TCP (for example via `socat`) |
| `unix:///path/to/socket` | GDB/MI exposed over a Unix domain socket |
| `pty:///dev/pts/N` | GDB/MI carried by a pty/tty device |

### Request Fields

- `endpoint` (required): one of the endpoint forms above
- `init_commands`: optional GDB commands to run right after connecting

`program`, `args`, and `core` are rejected: the external process already owns
the target.

### Success Fields

Same shape as `gdb_session_start`, plus `endpoint`. `target_loaded` is probed
from the connected gdb (via `info files`), so it reflects the external target.

### Example

```json
{
  "endpoint": "tcp://127.0.0.1:4399"
}
```

Typical setup (external gdb exposed over TCP with `socat`):

```bash
socat TCP-LISTEN:4399,reuseaddr,fork \
  EXEC:'gdb --quiet --interpreter=mi2 ./app',pty,raw,echo=0,stderr
# then: gdb_session_connect {"endpoint": "tcp://127.0.0.1:4399"}
```

Keep `raw,echo=0` on the socat `pty`; a cooked tty rewrites gdb's line endings as
CRLF, which breaks MI result records and makes the handshake time out.

See the `gdb-mcp-cli` and `debug-with-gdb-mcp` skills for full runbooks.

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
| `run` | `{"session_id":7,"action":"run"}` | direct execution payload under `result` |
| `continue` | `{"session_id":7,"action":"continue"}` | direct execution payload under `result` |
| `interrupt` | `{"session_id":7,"action":"interrupt"}` | direct execution payload under `result` |
| `step` | `{"session_id":7,"action":"step"}` | direct execution payload under `result` |
| `next` | `{"session_id":7,"action":"next"}` | direct execution payload under `result` |
| `finish` | `{"session_id":7,"action":"finish"}` | `message`, `return_value`, `gdb_result_var`, `frame`, `execution_state`, `stop_reason`, `last_stop_event` |
| `wait_for_stop` | `{"session_id":7,"action":"wait_for_stop","execution":{"timeout_sec":10,"stop_reasons":["breakpoint-hit"]}}` | `message`, `matched`, `timed_out`, `source`, `execution_state`, `stop_reason`, `reason_filter`, `last_stop_event` |

### Wait Policy

`run`, `continue`, `step`, `next`, and `finish` accept an optional `execution` payload with a flattened wait policy:

```json
{
  "execution": {
    "wait_until": "acknowledged",
    "timeout_sec": 30
  }
}
```

`wait_until` can be `acknowledged` or `stop` (default `stop`). Omitting `execution` entirely is equivalent to `wait_until="stop"` with the default timeout. For `run`, `execution.args` may also override the inferior argv as an explicit list or one shell-style string.

`continue`, `step`, `next`, and `finish` also accept `"reverse": true`, which
runs the command backwards (`-exec-continue --reverse` and friends). It requires
a target that can execute in reverse: an `rr_replay` session, or a gdb recording
started with `record`/`target record-full`. `run`, `interrupt`, and
`wait_for_stop` reject the flag.

### Notes

- `run`, `continue`, `step`, `next`, and `interrupt` wrap the structured command result produced by GDB. Inside `result`, expect `command`, optional text `output`, and optional machine-readable `result`.
- `wait_for_stop` is the structured replacement for manual polling.

### Reverse Debugging

Two ways to get a reversible session:

1. **rr (recommended)**: record outside gdb-mcp (`rr record ./app`), then
   `gdb_session_start {"rr_replay": "/path/to/trace"}`. Breakpoints, `continue`,
   and `reverse` stepping all work, and `gdb_execute_command` can still reach
   rr-specific commands such as `when` or `info record`.
2. **gdb recording**: start a normal session and run
   `gdb_execute_command {"command": "record"}` before the region of interest.

Reverse stepping goes through `gdb_execution_manage` with
`"execution": {"reverse": true}`; the escape hatch
(`gdb_execute_command {"command": "reverse-step"}`) remains available.

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

`location` may be a function, `file:line`, or `*address`. Absolute-address
breakpoints are resolved against the current target, so on a PIE that has not
been loaded yet they can be accepted by GDB but never inserted. When GDB
reports `Cannot insert breakpoint N` for a *pre-existing* breakpoint while
creating a new one, `create` no longer blames the requested location: it
re-reads the inventory and, if the new breakpoint was created, returns success
with a `warnings` entry naming the blocker; otherwise it returns
`code="breakpoint_insert_blocked"` with `blocked_breakpoint_numbers`,
`blocked_breakpoints`, and (when present) `created_breakpoint` in `details`.
Delete or fix the blocking breakpoint before running.

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

### Memory Notes

- `blocks` carries only the readable memory. A request that spans unreadable
  memory comes back as several blocks, each with `begin`, `offset`, `end`, and
  `contents`.
- A read that returns fewer bytes than `count`, or more than one block, adds a
  `warnings` entry: `Partial read: captured X of Y bytes` and
  `Discontinuous read: N blocks returned`.

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
