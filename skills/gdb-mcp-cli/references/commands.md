# gdb-mcp-client command reference

Every command maps onto one MCP tool call. Flags are the tool's flags; `--help` on any command
is authoritative. `--session-id N` is accepted wherever a session is used and defaults to the
project's recorded session.

Common flags on every command: `--server-url URL`, `--json`, `--payload-json JSON`.
Boolean flags come in `--flag` / `--no-flag` pairs (for example `--fail-fast` / `--no-fail-fast`).

## Sessions

| Command | Tool call | Notes |
| --- | --- | --- |
| `start PROGRAM` | `gdb_session_start` | Also: `--arg A` (repeatable), `--init-command CMD`, `--env K=V`, `--working-dir DIR`, `--gdb-path PATH`, `--core FILE`. Records the new session as the project default. |
| `status` | `gdb_session_query` action=list | Lists sessions; appends `default session: N` (or `(not running)`). Rejects `--session-id`. |
| `stop` | `gdb_session_manage` action=stop | Stops the default session (or `--session-id N`) and clears it when it was the default. |
| `session use ID` | validates via `gdb_session_query` action=status | Makes `ID` the default for this project. Requires the background server, not `--server-url`. |
| `session current` | — (local cookie) | Prints the default session id; exit 1 when there is none. |
| `daemon status` | — | Shows the background server for this project, or that there is none. |
| `daemon stop [--force]` | `POST /shutdown` | `--force` stops even with live sessions. |

## Run control

| Command | Action | Notes |
| --- | --- | --- |
| `run` | `execution_manage` run | `--arg A` passes inferior arguments. |
| `continue` | continue | |
| `interrupt` | interrupt | |
| `step` | step | |
| `next` | next | |
| `finish` | finish | |
| `wait-stop` | wait_for_stop | Blocks until the next stop event. |

All of them accept `--wait-until stop|acknowledged` (default `stop`), `--timeout-sec N`, and
`--stop-reason R` (repeatable).

`attach PID` → `gdb_attach_process`: attach into a session (`--session-id`, `--timeout-sec`).

## Context and inspection

| Command | Action | Notes |
| --- | --- | --- |
| `bt` | `context_query` backtrace | `--max-frames N`, `--thread-id`, `--frame`. |
| `threads` | context_query threads | |
| `frame N` | context_query frame | Positional maps to `--frame`. |
| `locals` | `inspect_query` variables | `--expression E`, `--frame`, `--thread-id`. |
| `regs` | inspect_query registers | `--register-number N` / `--register-name NAME` (repeatable), `--include-vector-registers` / `--no-include-vector-registers`, `--max-registers N`, `--value-format hex|natural`. |
| `memory ADDR` | inspect_query memory | `--count N` is required by the action, `--offset N` optional. |
| `disasm` | inspect_query disassembly | Location selectors below plus `--instruction-count N`, `--mode assembly|mixed`, `--context-before/--context-after N`. |
| `list` | inspect_query source | Location selectors plus `--context-before/--context-after N`. |

Location selectors (for `disasm` and `list`): `--location-kind current|function|address|address-range|file-line|file-range`
with `--function F`, `--address A`, `--start-address/--end-address A`, `--file F --line N`,
or `--file F --start-line/--end-line N`.

## Breakpoints

| Command | Action | Notes |
| --- | --- | --- |
| `break add LOCATION` | `breakpoint_manage` create | Code breakpoint by default; `--condition C`, `--temporary`. |
| `break list` | `breakpoint_query` list | `--number N`, `--kind code|watch|catch`, `--enabled` / `--no-enabled`. |
| `break rm N` | breakpoint_manage delete | |
| `break enable N` / `break disable N` | breakpoint_manage enable/disable | |
| `watch EXPR` | breakpoint_manage create (kind=watch) | `--access write|read|access`. |
| `catch EVENT` | breakpoint_manage create (kind=catch) | Events: throw, rethrow, catch, exec, fork, vfork, load, unload, signal, syscall. |

Breakpoint updates, single-breakpoint queries, and argument/condition edits use the escape
hatch: `tool breakpoint_manage --action update --number 1 --condition "x > 3"`.

## Direct commands and workflows

| Command | Tool call | Notes |
| --- | --- | --- |
| `exec "CMD"` | `gdb_execute_command` | Raw GDB/MI-adjacent command, `--timeout-sec N`. |
| `call "EXPR"` | `gdb_call_function` | Calls a function in the inferior, `--timeout-sec N`. |
| `batch` | `gdb_workflow_batch` | `--step TOOL`, `--step-arg path=value` (dotted paths, e.g. `query.max_frames=20`), `--step-label L`, `--fail-fast` / `--no-fail-fast`, `--capture-stop-events`. Exits 1 when any step fails. |
| `capture` | `gdb_capture_bundle` | `--output-dir DIR`, `--bundle-name NAME`, `--expression E`, `--memory-range START:SIZE` (e.g. `0x401000:64`), `--max-frames N`, `--include-*` / `--no-include-*`. |
| `campaign` | `gdb_run_until_failure` | Startup (`--startup-*`), setup steps (`--setup-step*`), run (`--run-arg`, `--run-timeout-sec`, `--max-iterations`), failure predicates (`--failure-on-error`, `--failure-on-timeout`, `--failure-stop-reason`, `--failure-execution-state`, `--failure-exit-code`, `--failure-result-text-regex`), and capture (`--capture-*`, each boolean with a `--no-` counterpart). |

## Escape hatch

```bash
gdb-mcp-client tool <name> [--payload-json JSON | field flags]
```

- `<name>` accepts `gdb_session_query` or the short `session_query`.
- `tool <name> --help` prints the tool's flags and validated payload examples.
- `--payload-json` is validated against the published tool schema and cannot be combined with
  field flags.

Tools: `session_start`, `session_query`, `session_manage`, `inferior_query`, `inferior_manage`,
`execution_manage`, `breakpoint_query`, `breakpoint_manage`, `context_query`, `context_manage`,
`inspect_query`, `workflow_batch`, `capture_bundle`, `run_until_failure`, `execute_command`,
`attach_process`, `call_function`.

## Output and exit codes

- Default output is human-readable; `--json` prints the raw envelope.
- Success: `{"status": "success", "action": ..., "result": {...}}`.
- Failure: `{"status": "error", "code": ..., "message": ..., "details": {...}}`; validation
  failures use `code: "validation_error"` with `details.field_errors`.
- Exit codes: `0` success, `1` tool or transport failure (also batch `error_count > 0`),
  `2` usage or payload-validation errors.
