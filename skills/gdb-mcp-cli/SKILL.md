---
name: gdb-mcp-cli
description: "Use when driving gdb-mcp from a shell through the `gdb-mcp-client` command: starting or attaching debug sessions, running and stepping, breakpoints and watchpoints, inspecting threads/frames/locals/memory/disassembly, batch and failure campaigns, forensic captures, and managing the per-project background server. Covers the short commands (`start`, `run`, `bt`, `break add`, …), default-session handling, and the `tool` escape hatch."
argument-hint: "[task, e.g. 'debug the crash in ./build/app']"
---

# gdb-mcp CLI

## Overview

`gdb-mcp-client` is the shell face of gdb-mcp. Every command maps onto one MCP tool call with the same flags, so anything an MCP host can do is reachable from a terminal:

```bash
gdb-mcp-client start ./build/app   # start a session
gdb-mcp-client break add parse_token
gdb-mcp-client run
gdb-mcp-client bt
gdb-mcp-client locals
gdb-mcp-client continue
```

Without `--server-url` (or `GDB_MCP_SERVER_URL`) the client starts one background server per project and reuses it; sessions are remembered per project, so `--session-id` is usually unnecessary.

## When to Use

- You can run shell commands and want a tight loop: build → launch → break → inspect → fix.
- You need reproducible command transcripts rather than host-specific MCP wiring.
- You want the same structured envelopes (`--json`) that the MCP tools return, plus shell exit codes.

Prefer the structured MCP tools directly when the environment does not grant shell access, or when results must stay inside an existing MCP conversation.

## Core Rules

1. **`--help` is authoritative.** Every command lists its exact flags and, for `tool` subcommands, validated payload examples. Use `references/commands.md` to pick the command, then `--help` for flag details.
2. **Sessions are remembered.** `start` records the new session for the project; later commands use it when `--session-id` is omitted. `stop` clears it. Switch with `session use ID`, inspect with `session current`, and `status` marks the current default (adding "(not running)" when it is gone).
3. **One project, one background server.** The cookie is keyed by the nearest VCS root, defaults to the platform cache dir, and holds the endpoint plus bearer token. Manually started servers never idle-exit; background ones exit after 15 minutes without requests or live sessions.
4. **Check exit codes.** `0` success, `1` tool or daemon failure, `2` usage errors. A program that stops on a breakpoint is a success, not an error.
5. **Errors are structured.** With `--json`, failures carry `status: "error"`, `code`, `message`, and `details` (validation failures use `code: "validation_error"` with `field_errors`).

## Command Map

| Task | Command |
| --- | --- |
| Start / list / stop sessions | `start`, `status`, `stop` |
| Default session | `session use ID`, `session current` |
| Run control | `run`, `continue`, `interrupt`, `step`, `next`, `finish`, `wait-stop` |
| Attach to a PID | `attach PID` |
| Threads, frames, backtrace | `threads`, `frame N`, `bt` |
| Variables, registers, memory, source, assembly | `locals`, `regs`, `memory ADDR`, `list`, `disasm` |
| Breakpoints | `break add LOCATION`, `break list`, `break rm N`, `break enable N`, `break disable N` |
| Watchpoint / catchpoint | `watch EXPR`, `catch EVENT` |
| Direct GDB command | `exec "info registers"` |
| Function call in the inferior | `call "func(1)"` |
| Batch steps, failure campaign, evidence capture | `batch`, `campaign`, `capture` |
| Any MCP tool | `tool <name>` |
| Background server | `daemon status`, `daemon stop [--force]` |

Exact flags, positionals, and the underlying tool for each command: [`references/commands.md`](references/commands.md).

## The Escape Hatch

`tool <name>` reaches every MCP tool; the `gdb_` prefix is optional:

```bash
gdb-mcp-client tool session_query --action status --session-id 2
gdb-mcp-client tool inferior_manage --action select --inferior-id 2
gdb-mcp-client tool inspect_query --action evaluate --expression "cfg->retries"
```

Use it for actions with no short command (inferior management, thread/frame selection, breakpoint updates, expression evaluation) and whenever you want the raw tool surface. `--payload-json '<json>'` sends a payload verbatim, validated against the published tool schema; `tool <name> --help` prints ready-to-use examples.

## Worked Loop

```bash
$ gdb-mcp-client start ./build/app
status: success
session_id: 1
target_loaded: True
execution_state: not_started
message: GDB session started

$ gdb-mcp-client break add parse_token
action: create
status: success
result:
  breakpoint:
    number: 1
    ...

$ gdb-mcp-client run
$ gdb-mcp-client bt --max-frames 5
$ gdb-mcp-client frame 1
$ gdb-mcp-client locals
$ gdb-mcp-client exec "print cfg->retries"
$ gdb-mcp-client continue
```

Rules that keep this reliable:

- `bt`, `locals`, and `regs` fail with `No registers.` until the inferior is running and stopped; start with `run` or `attach` first.
- Run control commands take `--wait-until stop` (default) or `acknowledged`, and `--timeout-sec N` for long runs.
- After a crash or exit, use `status` to see the stop reason; the session stays alive for inspection.
- Two sessions on one program are fine — `session use` decides which one bare commands hit.

## Batch, Campaign, Capture

- `batch` runs ordered tool steps with `--step <tool>` / `--step-arg path=value` / `--step-label text`; it exits non-zero when any step fails.
- `campaign` repeats start → setup → run → collect until a failure predicate matches (exit code, stop reason, execution state, regex, timeout) and writes a capture; use it for flaky reproductions.
- `capture` writes a forensic bundle (frames, variables, registers, memory ranges, transcript) to `--output-dir`.

Each of these has many flags; read `gdb-mcp-client campaign --help` before composing one, and prefer `--json` so you can parse counts and paths.

## Flags and Environment

| Flag / variable | Effect |
| --- | --- |
| `--server-url` / `GDB_MCP_SERVER_URL` | Talk to an existing server; disables session defaults and the cookie |
| `--session-id N` | Override the project default session |
| `--json` | Print the raw response envelope |
| `--payload-json '<json>'` | Send a raw tool payload (mutually exclusive with field flags) |
| `GDB_MCP_AUTH_TOKEN` | Bearer token used by both the client and a server started with it |
| `GDB_MCP_STATE_DIR` | Where cookies, locks, and daemon logs live (defaults to the platform cache dir) |
| `GDB_MCP_DAEMON_IDLE_SEC` | Idle timeout for client-spawned servers (default 900, `0` disables) |
| `GDB_PATH` / `gdb_session_start.gdb_path` | GDB binary selection |

## Troubleshooting

- **Command says the default session is not running** — the session was stopped or died; `status` lists live sessions, then `session use` another or `start` a new one.
- **Background server will not start** — the CLI prints the tail of the daemon log; the full log is under `<state_dir>/logs/<hash>.log`.
- **`session use` refuses with `--server-url`** — defaults belong to the project background server; pass `--session-id` explicitly when driving an existing server.
- **Connection errors after an upgrade** — the client replaces stale cookies automatically; `daemon stop` cleans up a server you no longer want.
- **Long-running program** — `run --timeout-sec 600` or `interrupt` from another shell; `wait-stop` blocks until the next stop event.

## Reference

- [`references/commands.md`](references/commands.md) — every command, its positionals, key flags, and the tool call it makes.
