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

Set `GDB_MCP_SERVER_URL` to avoid repeating `--server-url`. `--payload-json '<json>'` sends a raw tool payload (mutually exclusive with field flags); every action-based subcommand lists validated payload examples in `--help`. `gdb_workflow_batch` exits non-zero when any step's nested result has `status="error"`.

## Response Conventions

Every tool result carries the same payload twice: as `structuredContent` (structured tool output) and as serialized JSON in `content[0].text` (for clients that only read text). Both are always the envelope described below.

Every successful call has `status: "success"` and a tool-specific object under
`result`. Action-based tools also include the selected `action` at the top level.
When an operation completed with a warning, `warnings` is a top-level array;
`status` remains `success`. Failures use `status: "error"` and include `code`
and `message`.

Collection sizes are the lengths of the returned arrays. Session, inferior,
breakpoint, thread, backtrace, disassembly, and source results omit redundant
`count` fields; capture bundles omit `artifact_count` and memory reads omit
`block_count`. Memory results use `requested_bytes` for the requested size and
`captured_bytes` for the actual readable size. The memory request still uses
`query.count`.

### Canonical Payload Fields

Records keep whatever fields the debugger reported and add a small set that is
canonical across backends, so callers can read them without knowing which
debugger is behind the session:

- `enabled` (breakpoints) is a boolean; GDB reports `y`/`n`
- `times` (breakpoints) is an integer hit count; GDB reports a string
- `level` (frames) is an integer; GDB reports a string
- `line` (frames) is an integer; GDB reports a string
- `number` (registers) is an integer index; GDB reports a string
- `is_current` (source lines and disassembly instructions) is a boolean;
  it is derived by this server rather than reported by the debugger, so a line,
  instruction that is not current says `false` instead of omitting the field;
  inferior selection is represented by `current_inferior_id` on the inventory
- `stop_kind` accompanies every `stop_reason` (and `final_stop_kind` every
  `final_stop_reason`) with the canonical meaning: `breakpoint`, `watchpoint`,
  `step`, `signal`, `exception`, `exit`, `entry`, `pause`, `fork`, `vfork`,
  `exec`, `thread_exit`, or `unknown`. The debugger's own `stop_reason` string is
  kept as-is; `stop_reasons` request filters keep matching that native string
- session status reports the selected inferior's stop kind; a stop on another
  inferior does not replace it
- execution observations apply state, selection, and new stop history together;
  status and capture readers do not observe a partially applied stop
- `state_detail` is `"crashed"` when a paused inferior stopped on a fatal signal
  or LLDB reported a crashed process; the execution state itself stays `paused`
- stop events expose portable signal fields once: `signal_name`,
  `signal_meaning`, `signal_code`, and `fault_address`. Remaining debugger data
  is namespaced under `backend_data` with its `debugger` tag; LLDB crash
  extensions are kept there. A fault address of `0x0` is kept when the signal
  identifies one; the field is not invented for ordinary breaks
- `source` lines carry an optional `column`; only the current line normally has
  one, since source files do not record a column for every line
- `variables` carry `summary`, `address`, and `children_count` when the backend
  can provide them; aggregate children are still fetched through the debugger's
  own value model rather than expanded here
- `breakpoint.locations[]` is the resolved location list. It replaces the old
  flat `addr`/`func`/`file`/`fullname`/`line` fields; each location carries those
  fields plus `column` and `resolved`
- a watchpoint adds `watch` with `access` (`write`, `read`, `access`), `address`,
  `size`, and `spec`; the old top-level `what` field is gone
- expression and function-call results add `type` and `summary` alongside their
  value/result

A field that is not declared is passed through unchanged, so a debugger-specific
field always reaches the caller. A declared field always carries its declared
type: a payload value that contradicts the debugger's own grammar (a flag that is
neither `y` nor `n`, a count that is not a number) fails the command rather than
being converted to a plausible value.

### Success Envelope

Every successful tool returns the same shape. Dedicated tools put their full
tool-specific payload under `result`; action-based tools also include the
selected `action` at the top level. Warnings, when present, are top-level too.

Dedicated tools include:

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
  "result": {
    "session_id": 7,
    "message": "GDB session started successfully",
    "target_loaded": true,
    "execution_state": "not_started"
  }
}
```

### Action-Based Success Envelope

Action-based tools return:

```json
{
  "status": "success",
  "action": "status",
  "result": {
    "debugger_alive": true,
    "target_loaded": true,
    "session_state": "ready",
    "execution_state": "paused"
  }
}
```

The `result` object is action-specific.

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
Schema-validation failures inside workflow steps use paths such as
`steps[1].arguments.query.expression`, or
`setup_steps[1].arguments.query.expression` for a campaign setup step. The
`tool` and `action` identify the invalid inner request. Steps are validated
before execution, so an invalid step does not execute earlier valid steps.

Request validation is separate from decoding debugger output. Additional
debugger record fields, including nested fields and hyphenated keys, are kept;
disassembly keeps instruction extensions and carries mixed-source extensions
onto each instruction from that source group.
an unfamiliar notification or native stop reason is not a decoding failure.
Unclassified stop reasons retain their text and use `stop_kind="unknown"`.
If a field consumed by an operation cannot be decoded, that operation returns
`code="protocol_error"`, not `validation_error`. The session remains usable
while its debugger connection is usable. Unexpected implementation exceptions
return `internal_error` and are logged by the server.
This also applies to stop and interrupt: implementation failures are not
converted to ordinary debugger errors during these operations.
These distinctions also apply during startup: malformed consumed debugger data
returns `protocol_error`, and unexpected implementation failures return
`internal_error`, after startup resources are cleaned up. Failed startup does
not publish a session id. Unknown GDB/MI result classes retain their original
text in command backend data; only `error` is classified as a debugger error
and only `running` directly changes execution state to running.

A failure that a debugger reported carries **everything that debugger printed**: GDB's console context (`Thread 1 (...):`, the address `x` tried) followed by its MI `msg`. A timeout carries whatever arrived before the deadline. `message` is only a short sentence when this server itself rejected the request (no session, no frame, an unsupported option).

Session-scoped failures also carry the session's state in `details` (`session_id`, `execution_state`, `stop_reason`, `exit_code`), so a failure whose message is only `No registers.` still tells the caller that the inferior exited.
Failures nested in `gdb_workflow_batch` steps carry the same action, session id,
and available execution-state details as the corresponding direct tool call.

Common machine-readable codes include:

- `validation_error`, `unsupported_combination` — invalid arguments or incompatible options
- `unsupported_operation` — the selected backend or Delve release lacks the requested operation;
  capability checks apply to direct calls and workflow batch steps
- `invalid_session`, `session_closing` — the session is gone or shutting down
- `no_registers`, `inferior_not_running`, `inferior_running`, `no_stack_frame`, `memory_unavailable`, `symbol_unavailable`, `no_symbols_loaded`, `value_unavailable`, `command_timeout` — classified GDB failures
- `protocol_error` — debugger data could not be decoded, or a debugger transport
  reported a protocol failure
- `not_started`, `unknown_operation`, `build_failed` — debugger session or LLDB
  bridge lifecycle failures
- `unknown_tool`, `internal_error`, `not_found` — dispatch and lookup failures
- `error` — fallback when a debugger failure matches no specific classification

## Shared Request Patterns

### `session_id`

Every session-scoped tool takes a `session_id` returned by `gdb_session_start`.

### Published Tool Schemas

Published schemas may use `anyOf`, `$ref`, and `$defs`, but never `oneOf`, `allOf`, or OpenAPI's `discriminator`.
A tagged union keeps one alternative per value, so each alternative states its own `required` fields instead of being merged into one object whose conditional rules would have to be described in prose; `const` is spelled as a single-value `enum`.
Requests are validated against the typed request models rather than by re-validating the published schema, so the schema stays a descriptive client contract while the models remain authoritative.
High-complexity tools also publish request `examples` inside their `inputSchema`.

### Published Output Schemas

Every tool also publishes an `outputSchema` describing the `structuredContent` of its result. It is generated from the payload type the session layer returns and normalized the same way as an input schema, so it follows the code instead of a hand-kept copy:

- the schema offers one alternative per success action plus one for the error envelope, each stating its own `required` fields — every success nests its payload under `result`;
- a field that can be `null` in a result keeps its `null` alternative, because a result really carries `null` where a request only omits the field;
- a field carrying arbitrary debugger data (an MI result payload) refers to a recursive definition, since the payload really is arbitrary JSON;
- a success alternative always allows additional fields, so a debugger-specific field is never rejected.

Clients that validate structured output (the MCP Python SDK does) therefore accept every payload this server can produce.

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
| `gdb_session_start` | Start a new debugger session (gdb-mcp spawns it) |
| `gdb_session_connect` | Connect to an external GDB/MI stream, LLDB remote stub, or Delve RPC server |
| `gdb_session_query` | Query session inventory or one live session |
| `gdb_session_manage` | Mutate session lifecycle |
| `gdb_inferior_query` | Query inferior inventory or current inferior |
| `gdb_inferior_manage` | Create, remove, select, or reconfigure inferiors |
| `gdb_execution_manage` | Run, continue, interrupt, step, next, jump, until, finish, or wait |
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
- `debugger`: backend to drive, default `gdb`; supported values are `gdb`
  (GDB/MI), `lldb` (native SB API bridge), and `delve` (JSON-RPC API v2). The value is validated against the
  implemented set, and a backend that cannot perform an operation answers with
  `code="unsupported_operation"`.
- `args`: optional argv override as `list[str]` or shell-style string
- `core`: optional core-dump path
- `init_commands`: optional list of debugger commands to run after environment setup
- `env`: optional environment mapping applied before `init_commands`
- `debugger_path`: optional GDB binary override; for `debugger="lldb"` it selects an
  LLDB binary/installation instead; for Delve it selects the `dlv` executable,
  overriding `DLV_PATH` and the default `dlv`
- `debugger_args`: optional extra startup arguments, validated for the selected backend;
  GDB and Delve forward them, while LLDB rejects nonempty arguments
- `working_dir`: optional working directory for the debugger process
- `rr_replay`: optional rr trace directory to replay instead of launching a program;
  GDB only. LLDB rejects this field because auto-starting rr would hide the loss
  of reverse stepping; use `gdb_session_connect` instead when forward replay is
  enough

`args` and `core` are mutually exclusive. With `rr_replay` the recording already
owns the target, so the session runs `rr replay --interpreter=mi <trace>` instead
of a plain `gdb`; a `program` passed alongside it is ignored and reported in
`warnings`, while `args` and `core` are rejected. The `RR_PATH` environment
variable (default `rr`) selects the rr binary, and `debugger_path` is passed to rr as
its `-d` debugger choice. With `rr_replay`, `debugger_args` are forwarded to the
debugger rr starts, after rr's `--` separator.

**LLDB and rr:** start `rr replay -s <port> <trace>` yourself, then use
`gdb_session_connect` with `debugger="lldb"`,
`endpoint="connect://127.0.0.1:<port>"`, and `program` set to the trace's
executable. LLDB can follow rr's forward replay and inspect stops, but LLDB has
no reverse-step/reverse-continue API. Connecting LLDB therefore gives up rr's
reverse stepping; use `debugger="gdb"` with `rr_replay` when reverse execution
matters.

`debugger_args` are passed verbatim as argv elements, so an option that takes a
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
  "debugger_args": ["--directory=/path/to/rustlib/etc", "-iex", "add-auto-load-safe-path /path/to/rustlib/etc"]
}
```

Go embeds an absolute path, so the trust entry alone is enough; a kernel build
needs `add-auto-load-safe-path` for its build tree. `debugger_path` pointing at a
wrapper such as `rust-gdb` achieves the same by injecting those options.

### Success Fields

- `session_id`
- `message`
- `program`
- `core`
- `target_loaded`
- `execution_state`
- `stop_reason`
- `exit_code`
- `diagnostics`

These fields are inside the success envelope's `result` object. Any `warnings`
array is at the envelope root.

`diagnostics` contains `environment_results` and `initialization_results`, each
an array of complete tool-result envelopes, plus `backend_data`. The tagged
`backend_data` object contains `debugger`, `console_output`, and `data`; the
last field holds debugger-specific structured startup data. Both result arrays
are always present; a backend may apply startup configuration without producing
separate per-command result envelopes.

Failed startup returns an error without registering a session. Termination and
cleanup of an owned GDB or LLDB process share a single 10-second budget,
including graceful shutdown, escalation to terminate and kill, and pipe cleanup.
LLDB skips graceful shutdown after a response timeout or a protocol failure.
This cleanup budget is additional to the existing startup command timeouts.

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

### Delve Sessions

Set `debugger="delve"` to use Delve RPC API v2. The same backend supports
multiple Delve releases; `debugger_path` selects a binary per session.
Compatibility tests cover v1.20.2 / Go 1.20.14, v1.24.2 / Go 1.24.2, and
v1.27.2 / Go 1.27.1. Delve's own target-Go compatibility checks remain enabled.
TCP is available across these releases; Unix listeners require release support.

With `program`, startup uses `dlv exec` and returns the target paused at entry.
The first `run` resumes it; later `run` calls restart it, optionally replacing
arguments. `core` uses `dlv core` and requires `program`.

Without `program`, startup registers a configured session and defers spawning
Delve until `gdb_attach_process`. The deferred session reports
`session_state="ready"`, `execution_state="not_started"`, `debugger_alive=false`,
`target_loaded=false`, and `inferior_count=0`. It can be stopped without a
debugger ever being started. A failed attach can be retried. Attached targets
use `continue`; `run` rejects them with `unsupported_combination`.

```json
{"debugger":"delve","debugger_path":"/opt/delve/1.24.2/dlv"}
```

Follow with `gdb_attach_process`:

```json
{"session_id":1,"pid":12345,"timeout_sec":30}
```

Thread and context IDs are goroutine IDs throughout inspection, selection,
stop events, and capture bundles. Thread records retain native OS thread IDs
as `native_thread_id`. Variables retain Delve's aggregate children and loading
metadata. Source text is read on the MCP server host.

Execution requests remain active after `command_timeout`; a late reply updates
the session and stop history. Delve status and interrupt calls can run during
a blocking execution or workflow wait. `wait_for_stop` reports the existing
stop or waits for the pending command. Startup diagnostics include
`backend_data.data.version` from `GetVersion`, or `deferred=true` before attach.
Attach results include the version once Delve has started.

Breakpoints, conditional and temporary breakpoints, watchpoints, goroutine
selection, stacks, locals/arguments, evaluation, registers, memory, source,
disassembly, stepping, finish, and function calls use structured tools.
`jump` uses `SetExecutionPoint` when the selected release provides it; Delve
restricts its destination to the current function. Older releases return
`unsupported_operation`. Reverse execution requires a recorded Delve target.
There is one exposed inferior per session; use separate sessions for more
targets. Raw command execution, `init_commands`, forced return, frame-scoped
`until`, GDB catchpoints, inferior creation/removal, and GDB fork controls are
unsupported.

Delve arguments cannot override headless mode, RPC API version, listener,
multiclient mode, startup continuation, init file, working directory, or log
destination. These fields belong to session setup. Other flags are forwarded
to the selected binary. Stop kills targets launched by this session, detaches
from attached targets, and only disconnects from externally owned servers.
Owned debugger cleanup uses the shared 10-second budget.

## `gdb_session_connect`

Dedicated tool for connecting to a target that **another process already owns**.
The session never spawns a new debugger for this path, and stopping the session
detaches instead of terminating the external owner.

### Backends and Endpoint Forms

With `debugger="gdb"` (the default), `endpoint` is a GDB/MI stream:

| Form | Meaning |
| --- | --- |
| `tcp://host:port` | GDB/MI exposed over TCP (for example via `socat`) |
| `unix:///path/to/socket` | GDB/MI exposed over a Unix domain socket |
| `pty:///dev/pts/N` | GDB/MI carried by a pty/tty device |

With `debugger="lldb"`, `endpoint` is passed to `SBTarget::ConnectRemote`, so it
is a remote stub URL such as `connect://host:port`. The optional `program`
names the executable to associate with the remote target. LLDB remote sessions
support the same inspection, breakpoint, and execution tools as local sessions.

With `debugger="delve"`, `endpoint` is a JSON-RPC socket at `tcp://host:port`
or `unix:///path`. Use `dlv --headless --api-version=2 --accept-multiclient
--listen=127.0.0.1:2345 exec ./app` to keep an external server alive after
disconnect. Stopping the MCP session closes its connection without issuing
Delve's `Detach` RPC. The external server controls what disconnect does; in
single-client mode Delve itself exits when its client disconnects.

### Request Fields

- `endpoint` (required): a GDB/MI endpoint or an LLDB remote stub URL
- `debugger`: `gdb` (default), `lldb`, or `delve`
- `program`: executable for an LLDB remote target; the GDB/MI path rejects it
  because the external process already owns the target
- `debugger_path`: LLDB binary/installation path for the LLDB path; ignored by GDB/MI
- `init_commands`: optional debugger commands to run right after connecting

For GDB/MI, `program`, `args`, and `core` are rejected because the external
process already owns the target. LLDB remote accepts `program` but rejects
`args` and `core` for the same reason.

### Success Fields

Same payload as `gdb_session_start` under `result`, plus `endpoint`.
`target_loaded` is probed from the connected debugger, so it reflects the
external target.

### Examples

GDB/MI:

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

LLDB remote stub:

```json
{
  "debugger": "lldb",
  "program": "/path/to/app",
  "endpoint": "connect://127.0.0.1:12345",
  "debugger_path": "/opt/lldb/19/bin/lldb"
}
```

LLDB remote is also the only LLDB path to rr. Start rr in server-only mode:

```bash
rr replay -s 6099 /path/to/trace
# then:
gdb_session_connect {
  "debugger": "lldb",
  "program": "/path/to/app",
  "endpoint": "connect://127.0.0.1:6099"
}
```

This is forward replay only. LLDB's SB API exposes no reverse-continue or
reverse-step operation, so connecting LLDB to rr gives up rr's reverse stepping.
Use `debugger="gdb"` with `rr_replay` when reverse execution is required.

## LLDB Backend

Inferior inventory reports the process execution state and preserves known stop
details when its records omit them. A transition to running or not started clears
the previous stop. Status polling records a stop once per process and inferior;
starting the session again resets stop identity. Unfamiliar execution states
become `unknown` in both status output and the cached runtime state.

The LLDB backend is compiled from this repository against the LLDB 4.0.1 SB API
and talks to the selected real `liblldb` at runtime. Newer APIs are isolated in
optional layers: error-returning core/step operations, recognized arguments,
siginfo/crash information, watchpoint access/spec, and tagged-address fixup.
When a layer cannot be loaded, that capability is absent and the baseline path
is used.

The backend is selected with `debugger="lldb"` and does not need the official
Python `lldb` package. It never parses LLDB command output; only
`gdb_execute_command` uses the command interpreter.

Environment variables:

| Variable | Purpose |
| --- | --- |
| `LLDB_LIBRARY` | Path to the selected `liblldb` (or its directory) |
| `LLDB_PREFIX` / `LLDB_PATH` | LLDB installation prefix |
| `LLDB_BRIDGE_CXX` | C++ compiler used to build the bridge and layers |
| `LLDB_BRIDGE_EXECUTABLE` | Use a prebuilt bridge instead of building one |
| `LLDB_BRIDGE_DIR` | Directory containing a prebuilt bridge and layers |
| `LLDB_BRIDGE_LAYER_DIR` | Override the layer directory for a prebuilt bridge |
| `LLDB_BRIDGE_CACHE` | Cache directory for built artifacts |

The bridge must be built with a compiler whose C library is compatible with the
selected `liblldb`. When `LLDB_BRIDGE_CXX` is unset, the builder tries `c++`,
`g++`, then `clang++` from `PATH`; set it explicitly when the selected LLDB was
built against a different runtime.

See the `gdb-mcp-cli` and `debug-with-gdb-mcp` skills for full runbooks.

## `gdb_session_query`

Query session inventory or inspect one live session.
### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `list` | `{"action":"list","query":{}}` | `sessions` |
| `status` | `{"session_id":7,"action":"status","query":{}}` | `debugger_alive`, `target_loaded`, `session_state`, `execution_state`, `stop_reason`, `exit_code`, `current_inferior_id`, `inferior_count`, `inferior_states`, `follow_fork_mode`, `detach_on_fork` |

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
| `list` | `{"session_id":7,"action":"list","query":{}}` | `inferiors`, `current_inferior_id` |
| `current` | `{"session_id":7,"action":"current","query":{}}` | `inferior` |

## `gdb_inferior_manage`

Create, remove, select, or reconfigure inferiors.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `create` | `{"session_id":7,"action":"create","inferior":{"executable":"/path/app","make_current":true}}` | `inferior_id`, `display`, `description`, `connection`, `executable`, `current_inferior_id`, `inferior_count`, `message` |
| `remove` | `{"session_id":7,"action":"remove","inferior":{"inferior_id":2}}` | `inferior_id`, `current_inferior_id`, `inferior_count`, `message` |
| `select` | `{"session_id":7,"action":"select","inferior":{"inferior_id":2}}` | `inferior_id`, `display`, `description`, `connection`, `executable`, `message` |
| `set_follow_fork_mode` | `{"session_id":7,"action":"set_follow_fork_mode","inferior":{"mode":"child"}}` | `mode`, `message` |
| `set_detach_on_fork` | `{"session_id":7,"action":"set_detach_on_fork","inferior":{"enabled":false}}` | `enabled`, `message` |

The LLDB backend supports `set_follow_fork_mode` from LLDB 14 onward (older
releases follow the parent only) and answers `set_detach_on_fork` with
`code="unsupported_operation"`: LLDB has no corresponding setting.

## `gdb_execution_manage`

Run or synchronize execution state.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `run` | `{"session_id":7,"action":"run"}` | `command`, `backend_data` |
| `continue` | `{"session_id":7,"action":"continue"}` | `command`, `backend_data` |
| `interrupt` | `{"session_id":7,"action":"interrupt"}` | `message`, `backend_data` |
| `step` | `{"session_id":7,"action":"step"}` | `command`, `backend_data` |
| `next` | `{"session_id":7,"action":"next"}` | `command`, `backend_data` |
| `step_instruction` | `{"session_id":7,"action":"step_instruction","execution":{"reverse":false}}` | `command`, `backend_data` |
| `next_instruction` | `{"session_id":7,"action":"next_instruction","execution":{"reverse":false}}` | `command`, `backend_data` |
| `return` | `{"session_id":7,"action":"return","execution":{"value":"42"}}` | `command`, `backend_data` (including `return_value`) |
| `finish` | `{"session_id":7,"action":"finish"}` | `message`, `return_value`, `result_var`, `frame`, `execution_state`, `stop_reason`, `last_stop_event` |
| `jump` | `{"session_id":7,"action":"jump","execution":{"location":"worker.c:42"}}` | `command`, `location`, `execution_state`, `stop_reason`, `stop_kind`, `last_stop_event`, `backend_data` |
| `until` | `{"session_id":7,"action":"until","execution":{"location":"60"}}` | `command`, `location`, `execution_state`, `stop_reason`, `stop_kind`, `last_stop_event`, `backend_data` |
| `wait_for_stop` | `{"session_id":7,"action":"wait_for_stop","execution":{"timeout_sec":10,"stop_reasons":["breakpoint-hit"]}}` | `message`, `matched`, `timed_out`, `source`, `execution_state`, `stop_reason`, `reason_filter`, `last_stop_event` |

### Wait Policy

`run`, `continue`, `step`, `next`, `step_instruction`, `next_instruction`, and
`finish` accept an optional `execution` payload with a flattened wait policy.
`jump` and `until` accept the same fields alongside their required
`execution.location`:

```json
{
  "execution": {
    "wait_until": "acknowledged",
    "timeout_sec": 30
  }
}
```

`wait_until` can be `acknowledged` or `stop` (default `stop`). Omitting `execution` entirely is equivalent to `wait_until="stop"` with the default timeout. For `run`, `execution.args` may also override the inferior argv as an explicit list or one shell-style string.

`continue`, `step`, `next`, `step_instruction`, `next_instruction`, and `finish`
also accept `"reverse": true`, which runs the command backwards
(`-exec-continue --reverse`, `-exec-step-instruction --reverse`, and friends).
It requires a target that can execute in reverse: an `rr_replay` session, or a
gdb recording started with `record`/`target record-full`. `run`, `interrupt`,
`return`, `jump`, `until`, and `wait_for_stop` reject the flag. LLDB has no reverse execution and
returns `code="unsupported_combination"` when `reverse` is true.

`return` does not resume execution. It pops the selected frame to its caller and
optionally sets the return value from `execution.value`; omit `value` for a void
return.

### Jump And Until

Both actions require `execution.location` as a non-empty string, just like code
breakpoints. Structured location objects are not accepted. Examples:

| Location | Meaning |
| --- | --- |
| `"worker"` | Function name |
| `"60"` | Line in the source context selected by the debugger |
| `"worker.c:60"` | Source file and line |
| `"*0x4012ab"` | Instruction address |
| `"*$pc + 4"` | Address expression in the debugger's syntax |

The server validates the request shape and passes the location to the debugger.
It does not preflight the execution state, inspect line tables, check address
readability, reject ambiguous destinations, or enforce a function boundary.
Line numbers and address expressions are interpreted by the selected debugger;
native failures retain their debugger text. Addresses may be expressions such
as `$pc + 4`, subject to that debugger's expression syntax.

`jump` relocates execution and resumes. Destinations may be in another function;
there is no `allow_cross_function` flag. GDB uses `-exec-jump`. LLDB uses
`thread jump --force` followed by continue, since its jump command alone stays
paused. Jumping does not establish a function-call frame or restore earlier
memory and variable values. The debugger determines the next stop.

`until` executes normally using the debugger's native frame-scoped until:
the destination must be reached in the starting frame to satisfy that operation,
and returning from that frame also stops execution. Breakpoints, signals, or
other debugger stops can interrupt it first. Native restrictions are preserved:
LLDB can reject a destination outside the current function. `until` is not
implemented as an unrestricted run-to-address. A destination is required; the
GDB-only no-argument form is not exposed by this action.

The success `location` echoes the requested location string; it does not claim a
resolved destination. `execution_state` reports the state actually observed.
`stop_reason`, `stop_kind`, and `last_stop_event` describe only a new stop or exit
observed during this request and are `null` when none was observed. Native stop
reasons are preserved; GDB's `location-reached` has canonical `stop_kind="step"`.
`command` describes the backend command or SB API operation, and `backend_data`
retains the native payload and console output.

`wait_until="acknowledged"` returns after command acceptance, possibly already
stopped or exited if execution completed quickly. `wait_until="stop"` waits for
the next stop or exit, not necessarily the requested destination. Reaching a
breakpoint before the destination is a successful call. A wait timeout returns
`command_timeout` without implicitly interrupting the inferior. If LLDB
relocates but fails to resume, the failure includes `details.relocated=true`
and the resulting execution state.

CLI examples:

```bash
gdb-mcp-client jump worker.c:42
gdb-mcp-client until 60
gdb-mcp-client until --location '*0x4012ab' --wait-until acknowledged
```

### Notes

- `run`, `continue`, `step`, `next`, `step_instruction`, `next_instruction`, `return`, and `interrupt` return tagged `backend_data` inside the success `result` object. It contains `debugger`, `console_output`, and debugger-specific `data`. `interrupt` may include top-level `warnings` when no stop notification arrives.
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

This reverse path is GDB-only. An LLDB session connected to an rr server can
inspect the replayed trace and run forward to breakpoints, but `reverse: true`
returns `unsupported_combination` because LLDB's SB API has no reverse stepping.

## `gdb_breakpoint_query`

Query breakpoints.

### Actions

| Action | Request Shape | Success `result` |
| --- | --- | --- |
| `list` | `{"session_id":7,"action":"list","query":{"kinds":["code","watch"],"enabled":true}}` | `breakpoints` |
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

The LLDB backend supports exception catchpoints (`throw`, `rethrow`, `catch`)
only. LLDB's public SB API has no breakpoint kind for fork, exec, syscall,
load/unload, or signal catchpoints; those requests return
`code="unsupported_combination"`. Fork/exec events can still appear as native
stop reasons when LLDB stops on them.

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
| `threads` | `{"session_id":7,"action":"threads","query":{}}` | `threads`, `current_thread_id` |
| `backtrace` | `{"session_id":7,"action":"backtrace","query":{"thread_id":3,"max_frames":20}}` | `thread_id`, `frames` |
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
| `memory` | `{"session_id":7,"action":"memory","query":{"address":"buffer","count":64,"offset":0}}` | `address`, `requested_bytes`, `offset`, `blocks`, `captured_bytes` |
| `disassembly` | `{"session_id":7,"action":"disassembly","query":{"location":{"kind":"function","function":"process_data"},"instruction_count":24,"mode":"mixed"}}` | `context`, `location`, `range`, `mode`, `instructions` |
| `source` | `{"session_id":7,"action":"source","query":{"location":{"kind":"file_line","file":"src/main.c","line":42},"context_before":5,"context_after":5}}` | `context`, `location`, `range`, `lines` |

Inspection results group the selected `thread_id` and `frame` under `context`.
Their `location` contains `kind`, `function`, `address`, `file`, `fullname`,
and `line` where available. `range` contains `start_address` and `end_address`
for disassembly, or `start_line` and `end_line` for the source window.

### Register Filters

`registers` supports these optional selectors:

- `register_numbers`
- `register_names`
- `include_vector_registers`
- `max_registers`
- `value_format`

`include_vector_registers=false` classifies registers with the target
architecture, inferred from the register names GDB reports. Filtered families:
x86 `xmm/ymm/zmm/mm/k/tmm`; AArch64 `v/q/d/s/h/b` plus SVE `z/p/ffr` and SME
`za/zt0`; ARM `d/s/q` and MVE `p0`; RISC-V `v`; PowerPC `vr/v/vs/cvr/cvs/ev`;
s390 `v`; LoongArch `vr/xr`. Names that are not vector registers on some target
(such as RISC-V `s0`) are never filtered, and control/status registers such as
`mxcsr`, `vscr`, `fpsr`, and `fpcr` are kept. Explicit `register_numbers` or
`register_names` are returned as requested and bypass the filter. When the
architecture cannot be inferred, only unambiguous vector names are removed.

### Memory Notes

- `blocks` carries only the readable memory. A request that spans unreadable
  memory comes back as several blocks, each with `begin`, `offset`, `end`, and
  `contents`.
- `requested_bytes` records the requested length and `captured_bytes` the actual
  readable length. A short read adds `Partial read: captured X of Y bytes`; a
  discontinuous read adds `Discontinuous read: N blocks returned`. The number of
  returned blocks is `blocks.length`.

### Location Notes

- `disassembly` accepts `function`, `address`, `address_range`, `file_line`, `file_range`, or `current`.
- `source` accepts the same location union.

### Source Notes

- Source text is produced by GDB's own `list`, so the file is resolved with
  GDB's settings. A path rewritten with `set substitute-path`, a directory
  added with `set directories` or `--directory`, and the session's working
  directory all apply exactly as they do for GDB itself.
- `file_line` and `file_range` must therefore name a source file GDB knows
  from the program's debug info; an unrelated path fails with
  `No source file named <file>`.
- The returned `text` is GDB's rendering of the line: control bytes appear in
  caret notation (`^L` for a form feed, `^[` for escape) and bytes that are not
  valid UTF-8 appear as octal escapes (`\377`).

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

- `steps`
- `requested_steps`
- `final_execution_state`
- `final_stop_reason`
- `last_stop_event`

These fields are inside the success envelope's `result` object.

Each step includes:

- `index`
- `tool`
- `label`
- `result`
- `stop_event`

`steps[].result` preserves the full wrapped result of the inner tool call, using
the same success and error envelope as a direct call. The number of completed
steps is `steps.length`; inspect each nested result's `status` to find errors.
With `capture_stop_events=true`, each step includes the latest stop event it
produced, independent of stop-history retention. A step that produces no new
stop has `stop_event=null`.
`requested_steps` is the number of steps in the request, including steps skipped
after a fail-fast error.

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

- `message`
- `bundle_dir`
- `bundle_name`
- `manifest_path`
- `artifacts`
- `failed_sections`
- `execution_state`
- `stop_reason`
- `last_stop_event`

These fields are inside the success envelope's `result` object.

Each artifact contains `name`, `path`, `status`, and `kind`.

Capture status, manifest metadata, histories, selection, and returned stop
metadata are read together before capture inspection begins. Individual
inspection artifacts describe their subsequent debugger queries.

## `gdb_run_until_failure`

Repeat fresh debugger sessions until failure predicates match or the iteration limit is reached.

`startup.debugger` selects the backend for each iteration, including Delve;
the CLI exposes this as `campaign --startup-debugger delve`. Startup arguments,
binary selection, and replay settings are forwarded to each fresh session.

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

- `message`
- `iterations_requested`
- `capture_bundle`
- `capture_error`
- `iterations`

These fields are inside the success envelope's `result` object. Any `warnings`
array is at the envelope root.

Each `iterations[]` entry contains:

- `iteration`
- `phase`
- `result` (the complete envelope from the operation for this phase)
- `execution_state`
- `stop_reason`
- `stop_kind`
- `exit_code`
- `matched_failure`
- `trigger`

The completed count is the length of `iterations`. State and stop information
belongs to each iteration, so the campaign result does not repeat a potentially
different final state at its root.

## `gdb_execute_command`

Escape hatch for CLI or MI commands. This is not action-based.

### Request Fields

- `session_id`
- `command`
- `timeout_sec`

### Success Fields

- `command`
- `backend_data`

`backend_data` contains the `debugger` tag, `console_output`, and the raw
debugger-specific `data` object.

These fields are inside the success envelope's `result` object.

Use this when no dedicated structured tool exists. Prefer the structured families when they already cover the operation.

## `gdb_attach_process`

Attach to a running process by PID. This is a privileged tool.

### Request Fields

- `session_id`
- `pid`
- `timeout_sec`

### Success Fields

- `command`
- `backend_data`

These fields are inside the success envelope's `result` object.

## `gdb_call_function`

Execute a function call in the target process. This is a privileged tool.

### Request Fields

- `session_id`
- `function_call`
- `timeout_sec`

### Success Fields

- `function_call`
- `result`

These fields are inside the success envelope's `result` object.

Example:

```json
{
  "session_id": 7,
  "function_call": "printf(\"debug: x=%d\\n\", x)",
  "timeout_sec": 30
}
```
