---
name: debug-with-gdb-mcp
description: Structured workflows for debugging executables and core dumps with gdb-mcp. Use when Codex needs to start or attach debugger sessions, inspect crashes and hangs, navigate threads and frames, manage breakpoints/watchpoints/catchpoints, debug forked processes, or capture reproducible forensic bundles.
---

# Debug With gdb-mcp

## Overview

Use gdb-mcp tools to run deterministic debugger workflows with structured outputs.
Prefer dedicated tools over raw CLI text commands, check startup health immediately, and preserve reproducibility with capture bundles.

## Core Workflow

1. Start session with `gdb_start_session`.
2. Validate startup with `warnings`, `target_loaded`, and `execution_state`.
3. Configure breakpoints/watchpoints/catchpoints as needed.
4. Run or resume with `gdb_run` or `gdb_continue`.
5. Wait for stops with `gdb_wait_for_stop` instead of polling loops.
6. Inspect state using thread/frame/data tools.
7. Capture artifacts with `gdb_capture_bundle` when findings matter.
8. End with `gdb_stop_session`.

## Tool Selection Rules

- Prefer structured tools (`gdb_run`, `gdb_get_variables`, `gdb_get_backtrace`) over `gdb_execute_command`.
- Use `gdb_execute_command` only for GDB features not covered by dedicated tools.
- Use `gdb_wait_for_stop` after `gdb_continue` to block for the next event.
- Use `gdb_interrupt` before inspection when the inferior is still running.
- Use `gdb_step` and `gdb_next` only when execution is paused.
- Use `gdb_call_function` only when active code execution side effects are acceptable.

## State Discipline

- Treat `execution_state` as authoritative for the currently selected inferior.
- In fork or multi-inferior workflows, also inspect `inferior_states` from `gdb_get_status` or `gdb_list_sessions` for full process state.
- Run `gdb_continue` only from a paused state.
- Run `gdb_run` only when target startup has completed and execution is not already running.
- For core dumps, expect `execution_state=paused` at startup.
- For live programs, expect `execution_state=not_started` before the first run.

## Fork and Multi-Inferior Debugging

- Configure policy before running: `gdb_set_follow_fork_mode` and `gdb_set_detach_on_fork`.
- Catch process events with `gdb_set_catchpoint` (`fork`, `vfork`, `exec`).
- After fork/exit stops, use `gdb_get_status` to confirm `current_inferior_id` plus `inferior_states` before resuming.
- Inspect process inventory with `gdb_list_inferiors`.
- Switch target process explicitly with `gdb_select_inferior`.

## Reproducibility Pattern

- Use `gdb_batch` for tightly ordered, session-scoped procedures.
- Use `gdb_run_until_failure` for flaky failures and automated campaign runs.
- Attach `gdb_capture_bundle` settings to failure workflows to keep machine-readable evidence.

## Reference Material

- Read [`references/playbooks.md`](references/playbooks.md) for copy-ready workflows and `gdb_batch`/`gdb_run_until_failure` templates.
