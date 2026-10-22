---
name: debug-with-gdb-mcp
description: Use when Codex needs to debug a native program through gdb-mcp, including live launches, attach-to-process sessions, core dumps, hangs, crashes, forked children, or flaky failures that need reproducible evidence.
---

# Debug With gdb-mcp

## Overview

Use gdb-mcp through its structured interface: one startup tool, domain-specific query and manage families, and dedicated workflow tools for batch execution, capture, and failure campaigns. Prefer structured payloads over ad-hoc CLI transcripts, validate state after every transition, and preserve enough evidence that another agent can reproduce the session.

The workflow is transport-independent: every tool and payload below works the same over stdio or streamable HTTP. Server start-up and connection configuration live in [`../../INSTALL.md`](../../INSTALL.md).

## When to Use

- Need to launch a program under GDB with explicit `args`, `env`, `working_dir`, or `init_commands`
- Need to attach to a running PID and inspect it without screen-scraping GDB output
- Need to analyze a core dump with executable and symbol-path setup
- Need to debug hangs, crashes, signals, watchpoints, or fork/exec behavior
- Need deterministic multi-step workflows or repeated run-until-failure campaigns
- Need structured artifacts for handoff or postmortem

Use `debugger="lldb"` for LLDB or `debugger="delve"` for Go through Delve RPC.

## Go With Delve

Start with `gdb_session_start` using `debugger="delve"` and a prebuilt Go
`program`. Set `debugger_path` to select a particular Delve release. Set code
breakpoints before `gdb_execution_manage(action="run")`, then inspect with the
usual context and inspection tools. Thread IDs represent goroutine IDs.

For attach, omit `program` from startup, then call `gdb_attach_process` with the
returned session ID and PID. Delve starts at attach time. The empty session
reports `debugger_alive=false` and `execution_state="not_started"`. Stop leaves
attached processes alive. Resume attached targets with `continue`.

Use structured tools for initialization; Delve rejects `init_commands` and
`gdb_execute_command`. Unsupported operations return `unsupported_operation`.
After `command_timeout`, query status, interrupt, or wait for stop; the
execution request remains active. `gdb_run_until_failure` accepts
`startup.debugger="delve"` for repeated launches.

## Tool Families

| Need | Tool |
| --- | --- |
| Start a session | `gdb_session_start` |
| Connect to an externally started gdb | `gdb_session_connect` |
| Check session inventory or status | `gdb_session_query` |
| Stop a session | `gdb_session_manage` |
| Manage inferiors and fork policy | `gdb_inferior_query`, `gdb_inferior_manage` |
| Run, continue, interrupt, step, next, step_instruction, next_instruction, return, finish, jump, until, wait | `gdb_execution_manage` |
| Create or mutate breakpoints, watchpoints, catchpoints | `gdb_breakpoint_manage` |
| Query breakpoint inventory | `gdb_breakpoint_query` |
| Read thread and frame state | `gdb_context_query` |
| Select thread or frame | `gdb_context_manage` |
| Inspect expressions, locals, registers, memory, source, disassembly | `gdb_inspect_query` |
| Run ordered workflows | `gdb_workflow_batch` |
| Capture a forensic bundle | `gdb_capture_bundle` |
| Repeat until failure | `gdb_run_until_failure` |
| Escape hatch for unsupported debugger commands | `gdb_execute_command` |
| Privileged attach or in-target function calls | `gdb_attach_process`, `gdb_call_function` |

When referring to an action-based tool below, use the full form such as `gdb_execution_manage(action="run")` or `gdb_context_query(action="backtrace")`.

## Core Workflow

1. Choose the right startup mode with `gdb_session_start`: live launch, empty attach bootstrap, or core-dump analysis.
2. Validate startup immediately with `result.target_loaded`, top-level `warnings`, `result.execution_state`, and startup outputs.
3. Configure stop conditions before resuming: `gdb_breakpoint_manage(action="create")` and, when needed, `gdb_inferior_manage(action="set_follow_fork_mode" | "set_detach_on_fork")`.
4. Transition execution with `gdb_execution_manage(action="run" | "continue" | "step" | "next" | "step_instruction" | "next_instruction" | "return" | "finish" | "jump" | "until" | "interrupt")`.
5. Synchronize explicitly with `gdb_execution_manage(action="wait_for_stop")` when execution remains live.
6. Inspect state with `gdb_context_query`, `gdb_context_manage`, and `gdb_inspect_query`.
7. Capture artifacts with `gdb_capture_bundle` when the stop matters.
8. End with `gdb_session_manage(action="stop")`.

## Jump And Until

Use `gdb_execution_manage(action="jump")` to relocate and resume, or
`gdb_execution_manage(action="until")` to execute normally toward a destination
with the debugger's starting-frame return boundary. Both work with GDB and LLDB
and require a plain `execution.location` string, just like code breakpoints:

```json
{"session_id":7,"action":"jump","execution":{"location":"worker.c:42"}}
{"session_id":7,"action":"until","execution":{"location":"60"}}
{"session_id":7,"action":"until","execution":{"location":"*0x4012ab","wait_until":"acknowledged"}}
```

The debugger resolves the string and decides which destinations and states it
supports. There is no server-enforced function boundary or opt-in flag for
cross-function jumps. Native failures retain their debugger text; LLDB may
reject an outside-function `until` target. `jump` does not initialize a call
frame or restore prior variable values.

Read `result.execution_state` and `result.last_stop_event` after either action:
an intervening breakpoint or a frame return can stop execution before the
destination. `result.location` echoes the location string, not a resolved destination;
stop fields are `null` if this call observed no new stop. Both accept the
existing wait policy and timeout; neither accepts `reverse`. A timeout does
not interrupt the inferior. A jump failure with `details.relocated=true` means
LLDB moved execution before its resume attempt failed.

## Startup Configuration

### Choose the Right Startup Mode

| Situation | Startup shape | Important notes |
| --- | --- | --- |
| Fresh live launch | `gdb_session_start` with `program` and optional `args`, `env`, `working_dir`, `init_commands` | Use when the debugger should launch the program. Set `debugger="lldb"` for the SB API backend |
| Attach to existing process | `gdb_session_start` first, then `gdb_attach_process` | Start empty if you only need to attach |
| Core dump analysis | `gdb_session_start` with `core`, preferably also `program` | Expect startup to leave the target `paused` |
| GDB already running elsewhere | `gdb_session_connect` with an `endpoint` | gdb-mcp never spawns gdb; stop only detaches |
| LLDB remote stub | `gdb_session_connect` with `debugger="lldb"`, `endpoint="connect://host:port"`, and `program` | stop only detaches; `args` and `core` are rejected |
| Reverse debugging an rr recording | `gdb_session_start` with `rr_replay` | GDB only; the trace owns the target. LLDB cannot reverse-step rr; use `gdb_session_connect` to an external `rr replay -s` server only for forward replay |

### Configure Startup Fields Deliberately

| Field | Use it for | Notes |
| --- | --- | --- |
| `program` | Executable path for live runs or symbol-rich core analysis | Prefer `program + core` together for post-mortem work |
| `args` | Inferior argv for live launches | Not valid with `core`; use list form when exact argv matters |
| `working_dir` | Reproducible cwd-dependent behavior | Set this when the program reads relative config or data paths |
| `env` | Inferior environment variables | Applied before `init_commands`; use for `LD_LIBRARY_PATH`, feature flags, ports, and test modes |
| `init_commands` | GDB settings or commands not covered by dedicated tools | Keep launch config in structured fields when possible |
| `core` | Post-mortem debugging | Do not combine with `args` |
| `debugger_path` | Non-default debugger binary | For GDB: pick a specific version or build (with `rr_replay` it becomes rr's `-d` debugger, and `rust-gdb` also loads Rust's pretty-printers). For LLDB: an LLDB binary or installation |
| `debugger_args` | Extra startup arguments for GDB or Delve, applied before the program loads | The channel for GDB pre-load settings such as auto-load `safe-path`; each argument is one list entry. LLDB rejects this field |
| `rr_replay` | Replay an rr trace instead of launching a program | GDB only. `program` is ignored with a warning; `args` and `core` are rejected; enables `execution.reverse`. LLDB rejects this field; connect it to an external `rr replay -s` server for forward replay only |

### Environment and Launch Setup Rules

- Put inferior environment in `env`, not inside `init_commands`.
- Act on auto-load warnings: when top-level `warnings` says a script was declined or could not be found, the language's GDB helpers (Rust pretty-printers, Go's goroutine commands, a kernel's `vmlinux-gdb.py`) are inactive. For Rust, restart with `debugger_path="rust-gdb"`; otherwise pass `debugger_args`. `init_commands` cannot fix it because the program is already loaded.
- Pair `env` with `working_dir` when launch behavior depends on both environment and cwd.
- Keep `init_commands` for GDB configuration, symbol paths, and one-off debugger commands.
- If you need different argv on a later rerun, prefer `gdb_execution_manage(action="run", execution.args=...)` instead of recreating the whole session.
- If you need a background-style launch, prefer `gdb_execution_manage(action="run", execution.wait_until="acknowledged")` over raw `run &`.
- `env` affects launches from this GDB session; it does not retroactively change a process you attach to later.

### Copy-Ready Startup Patterns

**Live launch with environment and cwd:**

```json
{
  "program": "/path/to/app",
  "args": ["--mode", "stress"],
  "working_dir": "/path/to/workdir",
  "env": {
    "LD_LIBRARY_PATH": "/opt/app/lib",
    "APP_CONFIG": "debug",
    "FEATURE_X": "1"
  }
}
```

**Replay an rr recording (reverse debugging):**

```json
{
  "rr_replay": "/home/me/.local/share/rr/app-0"
}
```

**Core dump with symbol-path setup:**

```json
{
  "program": "/path/to/app",
  "core": "/tmp/core.12345",
  "init_commands": [
    "set sysroot /opt/sysroot",
    "set solib-search-path /opt/sysroot/lib:/opt/sysroot/usr/lib"
  ]
}
```

### Connect to an Externally Started gdb

Use `gdb_session_connect` when another process already owns the gdb process and
the target, and you only want gdb-mcp to drive that same MI session. gdb-mcp
does **not** spawn gdb in this mode, and `gdb_session_manage(action="stop")`
**detaches** instead of killing gdb.

Endpoint forms:

| Endpoint | Meaning |
| --- | --- |
| `tcp://host:port` | GDB/MI exposed over TCP |
| `unix:///path/to/socket` | GDB/MI exposed over a Unix domain socket |
| `pty:///dev/pts/N` | GDB/MI carried by a pty/tty device |

**Recipe A - give gdb its own TCP endpoint (recommended):**

```bash
# One external process owns gdb and exposes MI on 127.0.0.1:4399.
socat TCP-LISTEN:4399,reuseaddr,fork \
  EXEC:'gdb --quiet --interpreter=mi2 /path/to/app',pty,raw,echo=0,stderr
```

```json
{ "endpoint": "tcp://127.0.0.1:4399" }
```

**Recipe B - unix socket:**

```bash
socat UNIX-LISTEN:/tmp/gdbmi.sock,fork \
  EXEC:'gdb --quiet --interpreter=mi2 /path/to/app',pty,raw,echo=0,stderr
```

```json
{ "endpoint": "unix:///tmp/gdbmi.sock" }
```

Keep `raw,echo=0` on the socat `pty` and add `fork`; a cooked tty rewrites gdb's
line endings as CRLF and the connect handshake times out.

**Recipe C - attach to a gdb you already have open (gdb >= 9, `new-ui`):**

```bash
# Bridge TCP <-> a pty whose slave path you then hand to new-ui.
socat TCP-LISTEN:4399,reuseaddr PTY,link=/tmp/gdbmi,raw,echo=0
```

In the already-running gdb, type:

```
new-ui mi2 /tmp/gdbmi
```

Then connect gdb-mcp to the TCP side:

```json
{ "endpoint": "tcp://127.0.0.1:4399" }
```

The external gdb keeps its own CLI, and both UIs share one debug state.

**Recipe D - direct pty:**

When an external harness owns a pty master and exposes the tty path that carries
MI, connect straight to it:

```json
{ "endpoint": "pty:///dev/pts/7" }
```

Rules and expectations:

- Never pass `program`, `args`, or `core` with `endpoint`; the external gdb owns
  the target. gdb-mcp rejects the combination.
- `target_loaded` is probed from the connected gdb via `info files`, so it
  reflects the external target even though gdb-mcp did not load it.
- `gdb_session_manage(action="stop")` detaches gdb-mcp; the external gdb keeps
  running. Stop the external process yourself when you are done.
- Prefer `gdb_session_start` + `gdb_attach_process` when gdb-mcp should own the
  gdb process.
- Everything after connecting (`gdb_breakpoint_manage`, `gdb_execution_manage`,
  `gdb_context_query`, ...) behaves exactly as for a started session.

## Startup Validation

Run these checks immediately after `gdb_session_start`, `gdb_session_connect`, or `gdb_attach_process`:

- `result.target_loaded`: must be `true` before trusting later inspection
- top-level `warnings`: treat missing symbols, bad executables, or load failures as real blockers
- `result.execution_state`: tells you whether the inferior is `not_started`, `running`, `paused`, `exited`, or `unknown`
- `result.diagnostics.environment_results` and `result.diagnostics.initialization_results`: read these when startup configuration or symbol-path setup mattered
- `result.stop_reason`: important when startup leaves the inferior paused

Treat these outcomes as hard gates:

- `result.target_loaded=false`: fix the executable, core, or attach target before going deeper
- unexpected `result.execution_state=running`: use `gdb_execution_manage(action="interrupt")` before inspecting state
- warning about missing symbols: continue only if low-fidelity backtraces or variables are acceptable

## Tool Selection Rules

- Prefer structured tools such as `gdb_execution_manage`, `gdb_context_query`, `gdb_inspect_query`, `gdb_breakpoint_manage`, and `gdb_breakpoint_query`.
- Use `gdb_execute_command` only for GDB features not covered by dedicated structured tools.
- Use `gdb_execution_manage(action="run", execution.wait_until="acknowledged")` when you need the structured replacement for background `run &`; follow it with `gdb_execution_manage(action="wait_for_stop")` or `gdb_execution_manage(action="interrupt")`.
- Use `gdb_execution_manage(action="wait_for_stop")` after a background run or continue when you expect a later stop event and want a blocking handoff point.
- Use `gdb_execution_manage(action="interrupt")` before inspection when the inferior is still running.
- Use `gdb_execution_manage(action="step")` and `gdb_execution_manage(action="next")` only when execution is paused.
- Use `step_instruction`/`next_instruction` for assembly-level stepping; GDB supports `reverse=true` on a reversible target, LLDB rejects it.
- Use `gdb_execution_manage(action="return", execution.value="...")` to force the selected frame to return without executing its remaining code.
- Use `gdb_execution_manage(action="finish")` when you want to step out of the current frame and stop in the caller.
- Use `gdb_inspect_query(action="disassembly")` and `gdb_inspect_query(action="source")` instead of raw `disassemble` or `list`.
- Use `gdb_inferior_manage(action="create" | "remove" | "select")` when explicit inferior lifecycle management matters.
- Manage existing breakpoints with `gdb_breakpoint_manage(action="update" | "delete" | "enable" | "disable")` and fetch one by number with `gdb_breakpoint_query(action="get")`.
- Use `gdb_call_function` only when active code execution side effects are acceptable.
- Use `gdb_workflow_batch` when ordering matters and you want one structured transcript of the whole sequence.
- Use `gdb_run_until_failure` when you would otherwise write an ad-hoc rerun loop.

## State Discipline

- Treat `gdb_session_query(action="status")` as authoritative for execution state, selected inferior, and stop reason.
- In fork or multi-inferior workflows, inspect `inferior_states` from `gdb_session_query(action="status")` for full process state.
- Run `gdb_execution_manage(action="run")` only when target startup has completed and execution is not already running.
- After `gdb_execution_manage(action="run", execution.wait_until="acknowledged")` or any resume that leaves the inferior running, synchronize with `gdb_execution_manage(action="wait_for_stop")` or `gdb_execution_manage(action="interrupt")` before inspection.
- Run `gdb_execution_manage(action="continue")` only from a paused state.
- For core dumps, expect `execution_state=paused` at startup.
- For live programs, expect `execution_state=not_started` before the first run.
- After attach, expect the process to become paused and inspectable.
- Before thread or frame inspection in multi-inferior sessions, confirm the selected inferior explicitly with `gdb_inferior_query(action="current")` or `gdb_session_query(action="status")`.

## Workflow Guides

### Live Launch and Crash Triage

1. Start the session with `program` plus any needed `args`, `env`, `working_dir`, or `init_commands`.
2. Validate startup.
3. Set breakpoints, watchpoints, or catchpoints before running.
4. Launch with `gdb_execution_manage(action="run")`.
5. If you intentionally left the inferior running, block for the next stop with `gdb_execution_manage(action="wait_for_stop")`.
6. Inspect `gdb_context_query(action="threads" | "backtrace" | "frame")` and `gdb_inspect_query(action="variables" | "registers" | "source" | "disassembly")`.
7. Capture a bundle if the stop matters.

### Background Launch and Later Synchronization

1. Start the session and configure breakpoints or catchpoints first.
2. Launch with `gdb_execution_manage(action="run", execution.wait_until="acknowledged")` when you intentionally want the inferior to keep running.
3. Use `gdb_execution_manage(action="wait_for_stop")` when you expect a later stop event.
4. Use `gdb_execution_manage(action="interrupt")` if you need to force a pause before inspection.
5. Do not inspect threads, frames, locals, or source context until execution is paused again.

### Attach to a Running Process

1. Start a session, usually with an empty startup request.
2. Call `gdb_attach_process` with the PID.
3. Expect the process to stop in a paused state.
4. Use `gdb_session_query(action="status")`, `gdb_context_query(action="threads")`, and `gdb_context_query(action="backtrace")` first to understand where it stopped.
5. Only resume with `gdb_execution_manage(action="continue")` if you intentionally want the attached process to run again.

### Core Dump Analysis

1. Start with `program + core` whenever the executable is known.
2. Put `sysroot` or `solib-search-path` in `init_commands` after the core is loaded.
3. Expect `execution_state=paused`.
4. Inspect threads, backtraces, frames, and expressions without trying to run the inferior.
5. If frames are unresolved or variables are missing, treat that as a symbol-loading problem, not a runtime mystery.

### Hang or Deadlock Investigation

1. Call `gdb_session_query(action="status")`.
2. If the inferior is running, call `gdb_execution_manage(action="interrupt")`.
3. Call `gdb_context_query(action="threads")`.
4. Collect backtraces for the interesting threads or for all threads in lock-contention cases.
5. Use `gdb_inspect_query(action="registers" | "evaluate" | "memory")` to confirm wait conditions or state corruption.
6. Capture a bundle once you have a useful snapshot.

### Focused Source and Assembly Inspection

1. Stop at the code region you care about.
2. Use `gdb_inspect_query(action="source")` around the current stop, or resolve by `function`, `address`, or `file` selectors.
3. Use `gdb_inspect_query(action="disassembly", query.mode="mixed")` when you need source and assembly aligned in one structured payload.
4. Use `gdb_execution_manage(action="finish")` when the current frame is just a helper and the caller context matters more.
5. Re-run `gdb_inspect_query(action="source" | "disassembly")` after stepping if you need updated code context.

### Fork and Multi-Inferior Debugging

1. Set `gdb_inferior_manage(action="set_follow_fork_mode")` before running.
2. Set `gdb_inferior_manage(action="set_detach_on_fork")` based on whether you need both sides of the fork.
3. Add catchpoints through `gdb_breakpoint_manage(action="create", breakpoint.kind="catch")` when `fork`, `vfork`, or `exec` matter.
4. After stops, inspect `current_inferior_id` and `inferior_states` from `gdb_session_query(action="status")`.
5. Use `gdb_inferior_query(action="list")` and `gdb_inferior_manage(action="select")` before thread or frame inspection.
6. Avoid assuming the currently selected inferior is still the one you care about after process events.

### Manual Inferior Lifecycle

1. Use `gdb_inferior_manage(action="create")` when one GDB session needs an extra explicit inferior.
2. Use `gdb_inferior_query(action="list")` to confirm the returned `inferior_id`, executable association, and current selection.
3. Use `gdb_inferior_manage(action="select")` before thread or frame inspection on the new inferior.
4. Use `gdb_inferior_manage(action="remove")` when that explicit inferior is no longer needed.

### Flaky Failure Reproduction

1. Prefer `gdb_run_until_failure` over handwritten rerun loops.
2. Put repeatable setup in `setup_steps` using the tool names and arguments.
3. Encode failure criteria with structured `stop_reasons`, `execution_states`, `exit_codes`, or `result_text_regex`.
4. Capture bundles automatically on failure so you keep the exact failing evidence.
5. Use `gdb_workflow_batch` for one-shot ordered sequences inside a known session.

## Inspection Patterns

- `gdb_session_query(action="status")`: first stop for lifecycle, execution state, selected inferior, and stop reason
- `gdb_context_query(action="threads")`: inventory before choosing a thread to study
- `gdb_context_query(action="backtrace")`: preferred for stack inspection because it does not require changing the selected thread
- `gdb_context_manage(action="select_thread" | "select_frame")`: use when you need persistent context for multiple follow-up commands
- `gdb_inspect_query(action="source")`: inspect source windows around the current stop or a resolved location
- `gdb_inspect_query(action="disassembly")`: inspect structured assembly or mixed source and assembly
- `gdb_inspect_query(action="variables")`: inspect locals in a target thread or frame without disturbing selection
- `gdb_inspect_query(action="evaluate")`: use for specific expressions or globals when you already know what to ask
- `gdb_inspect_query(action="registers")`: request only the registers you need when payload size matters
- `gdb_inspect_query(action="memory")`: use when raw bytes matter more than pretty-printed values
- `gdb_breakpoint_query(action="list" | "get")`: verify installed breakpoints or fetch one by number instead of assuming setup succeeded
- `gdb_breakpoint_manage(action="update" | "delete" | "enable" | "disable")`: change or clear a condition, remove, or toggle an existing breakpoint
- `gdb_execution_manage(action="finish")`: use when the top frame is a noisy helper and you want the caller context next

## Troubleshooting and Common Mistakes

- Ignoring `warnings` and then trusting broken symbols or empty locals
- Forgetting that `args` and `core` are mutually exclusive
- Using `init_commands` to fake launch configuration that belongs in `args`, `env`, or `working_dir`
- Launching a background-style run with raw `run &` instead of `gdb_execution_manage(action="run", execution.wait_until="acknowledged")`
- Inspecting while the inferior is still running instead of interrupting first
- Calling `gdb_execution_manage(action="continue")` when execution is already running
- Calling `gdb_execution_manage(action="step" | "next")` when the inferior is not paused
- Forgetting `gdb_execution_manage(action="finish")` when stepping out is simpler than repeated `next`
- Forgetting that attach workflows inherit the target's existing environment; `env` only affects future launches
- Losing track of the active inferior after fork or exec stops
- Relying on raw `gdb_execute_command` output when structured tools already cover the operation
- Forgetting `gdb_session_manage(action="stop")` and leaking debugger sessions

## Reference Material

- Read [`references/playbooks.md`](references/playbooks.md) for copy-ready JSON payloads and `gdb_workflow_batch` or `gdb_run_until_failure` templates.
- Use [`../../TOOLS.md`](../../TOOLS.md) when you need exact parameter and response shapes for a specific tool.
