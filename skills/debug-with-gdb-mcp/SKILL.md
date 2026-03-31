---
name: debug-with-gdb-mcp
description: Use when Codex needs to debug a native program through gdb-mcp, including live launches, attach-to-process sessions, core dumps, hangs, crashes, forked children, or flaky failures that need reproducible evidence.
---

# Debug With gdb-mcp

## Overview

Use gdb-mcp to drive GDB through structured, machine-readable tools instead of ad-hoc CLI transcripts. Make session startup explicit, keep execution-state transitions disciplined, and preserve enough evidence that a later agent can reproduce or audit what happened.

## When to Use

- Need to launch a program under GDB with explicit `args`, `env`, `working_dir`, or `init_commands`
- Need to attach to a running PID and inspect it without screen-scraping GDB output
- Need to analyze a core dump with executable and symbol-path setup
- Need to debug hangs, crashes, signals, watchpoints, or fork/exec behavior
- Need deterministic batch workflows or repeated run-until-failure campaigns
- Need structured artifacts for handoff or postmortem

Use a different approach when the task is not GDB-based or when some other debugger is the real tool.

## Core Workflow

1. Start the right kind of session: live launch, attach, or core-dump analysis.
2. Validate startup immediately with `target_loaded`, `warnings`, and `execution_state`.
3. Configure stop conditions before running: breakpoints, watchpoints, catchpoints, and fork policy.
4. Transition execution with `gdb_run`, `gdb_continue`, `gdb_step`, `gdb_next`, `gdb_finish`, or `gdb_interrupt`.
5. Wait for stops with `gdb_wait_for_stop` instead of polling loops.
6. Inspect state with thread, frame, source, disassembly, variable, register, expression, and memory tools.
7. Capture artifacts with `gdb_capture_bundle` when findings matter.
8. End with `gdb_stop_session`.

## Startup Configuration

### Choose the Right Startup Mode

| Situation | Startup shape | Important notes |
| --- | --- | --- |
| Fresh live launch | `gdb_start_session` with `program` and optional `args`, `env`, `working_dir`, `init_commands` | Use when GDB should launch the program |
| Attach to existing process | `gdb_start_session` first, then `gdb_attach_process` | Start empty if you only need to attach |
| Core dump analysis | `gdb_start_session` with `core`, preferably also `program` | Expect startup to leave the target `paused` |

### Configure Startup Fields Deliberately

| Field | Use it for | Notes |
| --- | --- | --- |
| `program` | Executable path for live runs or symbol-rich core analysis | Prefer `program + core` together for post-mortem work |
| `args` | Inferior argv for live launches | Not valid with `core`; use list form when exact argv matters |
| `working_dir` | Reproducible cwd-dependent behavior | Set this when the program reads relative config or data paths |
| `env` | Inferior environment variables | Applied before `init_commands`; use for `LD_LIBRARY_PATH`, feature flags, ports, and test modes |
| `init_commands` | GDB settings or commands not covered by dedicated tools | Keep launch config in structured fields when possible |
| `core` | Post-mortem debugging | Do not combine with `args` |
| `gdb_path` | Non-default GDB binary | Use when a specific GDB version or build matters |

### Environment and Launch Setup Rules

- Put inferior environment in `env`, not inside `init_commands`.
- Pair `env` with `working_dir` when launch behavior depends on both environment and cwd.
- Keep `init_commands` for GDB configuration, symbol paths, and one-off debugger commands.
- If you need different argv on a later rerun, prefer `gdb_run.args` instead of recreating the whole session.
- If you need a background-style launch, prefer `gdb_run(wait_for_stop=false)` over raw `run &`.
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

## Startup Validation

Run these checks immediately after `gdb_start_session` or `gdb_attach_process`:

- `target_loaded`: must be `true` before trusting later inspection
- `warnings`: treat missing symbols, bad executables, or load failures as real blockers
- `execution_state`: tells you whether the inferior is `not_started`, `running`, `paused`, `exited`, or `unknown`
- `env_output` and `init_output`: read these when startup configuration or symbol-path setup mattered
- `stop_reason`: important when startup leaves the inferior paused

Treat these outcomes as hard gates:

- `target_loaded=false`: fix the executable, core, or attach target before going deeper
- unexpected `execution_state=running`: use `gdb_interrupt` before inspecting state or setting expectations
- warning about missing symbols: continue only if low-fidelity backtraces or variables are acceptable

## Tool Selection Rules

- Prefer structured tools such as `gdb_run`, `gdb_get_status`, `gdb_get_backtrace`, `gdb_get_variables`, `gdb_disassemble`, `gdb_get_source_context`, and `gdb_list_breakpoints`.
- Use `gdb_execute_command` only for GDB features not covered by dedicated tools.
- Use `gdb_run(wait_for_stop=false)` when you need the structured replacement for background `run &`; follow it with `gdb_wait_for_stop` or `gdb_interrupt`.
- Use `gdb_wait_for_stop` after `gdb_continue` to block for the next event.
- Use `gdb_interrupt` before inspection when the inferior is still running.
- Use `gdb_step` and `gdb_next` only when execution is paused.
- Use `gdb_finish` when you want to step out of the current frame and stop in the caller.
- Use `gdb_disassemble` for structured assembly or mixed source/assembly instead of raw `disassemble`.
- Use `gdb_get_source_context` for structured source windows instead of raw `list`.
- Use `gdb_add_inferior` and `gdb_remove_inferior` when explicit inferior lifecycle management matters.
- Use `gdb_call_function` only when active code execution side effects are acceptable.
- Use `gdb_batch` when ordering matters and you want one structured transcript of the whole sequence.
- Use `gdb_run_until_failure` when you would otherwise write an ad-hoc rerun loop.

## State Discipline

- Treat `execution_state` as authoritative for the currently selected inferior.
- In fork or multi-inferior workflows, also inspect `inferior_states` from `gdb_get_status` or `gdb_list_sessions` for full process state.
- Run `gdb_run` only when target startup has completed and execution is not already running.
- After `gdb_run(wait_for_stop=false)` or any resume that leaves the inferior running, synchronize with `gdb_wait_for_stop` or `gdb_interrupt` before inspection.
- Run `gdb_continue` only from a paused state.
- For core dumps, expect `execution_state=paused` at startup.
- For live programs, expect `execution_state=not_started` before the first run.
- After attach, expect the process to become paused and inspectable.
- Before thread or frame inspection in multi-inferior sessions, confirm the selected inferior explicitly with `gdb_select_inferior`.

## Workflow Guides

### Live Launch and Crash Triage

1. Start the session with `program` plus any needed `args`, `env`, `working_dir`, or `init_commands`.
2. Validate startup.
3. Set breakpoints or watchpoints before running.
4. Launch with `gdb_run`.
5. Block for the next stop with `gdb_wait_for_stop`.
6. Inspect `gdb_get_threads`, `gdb_get_backtrace`, `gdb_get_source_context`, `gdb_get_variables`, and focused `gdb_get_registers`.
7. Capture a bundle if the stop matters.

### Background Launch and Later Synchronization

1. Start the session and configure breakpoints or catchpoints first.
2. Launch with `gdb_run(wait_for_stop=false)` when you intentionally want the inferior to keep running.
3. Use `gdb_wait_for_stop` when you expect a later stop event and want a blocking handoff point.
4. Use `gdb_interrupt` if you need to force a pause before inspection.
5. Do not inspect threads, frames, locals, or source context until execution is paused again.

### Attach to a Running Process

1. Start a session, usually with an empty startup request.
2. Call `gdb_attach_process` with the PID.
3. Expect the process to stop in a paused state.
4. Use `gdb_get_status`, `gdb_get_threads`, and `gdb_get_backtrace` first to understand where it stopped.
5. Only resume with `gdb_continue` if you intentionally want the attached process to run again.

### Core Dump Analysis

1. Start with `program + core` whenever the executable is known.
2. Put `sysroot` or `solib-search-path` in `init_commands` after the core is loaded.
3. Expect `execution_state=paused`.
4. Inspect threads, backtraces, frames, and expressions without trying to run the inferior.
5. If frames are unresolved or variables are missing, treat that as a symbol-loading problem, not a runtime mystery.

### Hang or Deadlock Investigation

1. Call `gdb_get_status`.
2. If the inferior is running, call `gdb_interrupt`.
3. Get all threads.
4. Collect backtraces for the interesting threads or for all threads in lock-contention cases.
5. Use registers, expressions, and memory reads to confirm wait conditions or state corruption.
6. Capture a bundle once you have a useful snapshot.

### Focused Source and Assembly Inspection

1. Stop at the code region you care about.
2. Use `gdb_get_source_context` around the current stop, or resolve by `function`, `address`, or `file` plus line selectors.
3. Use `gdb_disassemble` with `mode="mixed"` when you need source and assembly aligned in one structured payload.
4. Use `gdb_finish` when the current frame is just a helper and the caller context matters more.
5. Re-run `gdb_get_source_context` or `gdb_disassemble` after stepping if you need updated code context.

### Fork and Multi-Inferior Debugging

1. Set `gdb_set_follow_fork_mode` before running.
2. Set `gdb_set_detach_on_fork` based on whether you need both sides of the fork.
3. Add catchpoints for `fork`, `vfork`, or `exec` when process transitions matter.
4. After stops, inspect `current_inferior_id` and `inferior_states`.
5. Use `gdb_list_inferiors` and `gdb_select_inferior` before thread or frame inspection.
6. Avoid assuming the currently selected inferior is still the one you care about after process events.

### Manual Inferior Lifecycle

1. Use `gdb_add_inferior` when one GDB session needs an extra explicit inferior before process events create one for you.
2. Use `gdb_list_inferiors` to confirm the returned `inferior_id`, executable association, and current selection.
3. Use `gdb_select_inferior` before thread or frame inspection on the new inferior.
4. Use `gdb_remove_inferior` when that explicit inferior is no longer needed.

### Flaky Failure Reproduction

1. Prefer `gdb_run_until_failure` over handwritten rerun loops.
2. Put repeatable setup in `setup_steps`.
3. Encode failure criteria with structured `stop_reasons` or other supported conditions.
4. Capture bundles automatically on failure so you keep the exact failing evidence.
5. Use `gdb_batch` for one-shot ordered sequences inside a known session.

## Inspection Patterns

- `gdb_get_status`: first stop for lifecycle, execution state, selected inferior, and stop reason
- `gdb_get_threads`: inventory before choosing a thread to study
- `gdb_get_backtrace`: preferred for stack inspection because it does not require changing the selected thread
- `gdb_select_thread` + `gdb_select_frame`: use when you need persistent context for multiple follow-up commands
- `gdb_get_source_context`: inspect source windows around the current stop or a resolved location without raw `list`
- `gdb_disassemble`: inspect structured assembly or mixed source/assembly instead of raw `disassemble`
- `gdb_get_variables`: inspect locals in a target thread or frame without disturbing selection
- `gdb_evaluate_expression`: use for specific expressions or globals when you already know what to ask
- `gdb_get_registers`: request only the registers you need when payload size matters
- `gdb_read_memory`: use when raw bytes matter more than pretty-printed values
- `gdb_list_breakpoints`: verify actual installed breakpoint or watchpoint state instead of assuming setup succeeded
- `gdb_finish`: use when the top frame is a noisy helper and you want the caller context next

## Troubleshooting and Common Mistakes

- Ignoring `warnings` and then trusting broken symbols or empty locals
- Forgetting that `args` and `core` are mutually exclusive
- Using `init_commands` to fake launch configuration that belongs in `args`, `env`, or `working_dir`
- Launching a background-style run with raw `run &` instead of `gdb_run(wait_for_stop=false)`
- Inspecting while the inferior is still running instead of interrupting first
- Calling `gdb_continue` when execution is already running
- Calling `gdb_step` or `gdb_next` when the inferior is not paused
- Forgetting `gdb_finish` when stepping out is simpler than repeated `gdb_next`
- Forgetting that attach workflows inherit the target's existing environment; `env` only affects future launches
- Losing track of the active inferior after fork or exec stops
- Relying on raw `gdb_execute_command` output when structured tools such as `gdb_disassemble`, `gdb_get_source_context`, or `gdb_add_inferior` exist
- Forgetting `gdb_stop_session` and leaking debugger sessions

## Reference Material

- Read [`references/playbooks.md`](references/playbooks.md) for copy-ready JSON payloads and `gdb_batch` or `gdb_run_until_failure` templates.
- Use [`TOOLS.md`](../../../TOOLS.md) when you need exact parameter and response shapes for a specific tool.
