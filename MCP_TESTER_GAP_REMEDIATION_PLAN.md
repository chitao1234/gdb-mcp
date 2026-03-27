# MCP Tester Gap Remediation Plan

## Context

External tester feedback identified reliability and contract gaps in the current MCP-facing GDB tools. The goal is to remove schema/runtime mismatches, eliminate false-negative tool failures, and harden command behavior for automation workloads.

This plan is based on direct code-path tracing and local repros.

## Root Causes and Fix Strategy

### 1. `gdb_start_session.args` schema mismatch

Root cause:
- `gdb_start_session.args` is list-only in `src/gdb_mcp/mcp/schemas.py`.
- `gdb_run.args` supports list or shell-style string.
- This creates inconsistent caller expectations between startup and run-time argument overrides.

Fix:
- Accept list or shell-style string for `gdb_start_session.args`.
- Normalize startup args through one shared parser (`shlex`-based).
- Keep `args` and `core` mutual exclusion behavior unchanged.

### 2. `thread_id`/`frame` input shape drift in inspection tools

Root cause:
- Inspection tools currently advertise integer-only `thread_id`/`frame` in schemas.
- Real-world clients often keep thread IDs as strings (matching GDB/MI payload conventions).
- Contract drift causes typed-client friction and brittle integrations.

Fix:
- Accept integer or numeric-string forms for these inspection parameters:
  - `gdb_get_backtrace.thread_id`
  - `gdb_get_variables.thread_id`, `gdb_get_variables.frame`
  - `gdb_get_registers.thread_id`, `gdb_get_registers.frame`
  - `gdb_evaluate_expression.thread_id`, `gdb_evaluate_expression.frame`
- Normalize at MCP handler boundary into strict ints before session-service calls.
- Return explicit validation errors for non-numeric string values.

### 3. `gdb_set_catchpoint` temporary throw false-negative

Root cause:
- Catchpoint number extraction relies on a narrow regex that misses output like:
  - `Temporary catchpoint  1 (throw)`
- Tool can return error even when catchpoint is successfully created.

Fix:
- Broaden catchpoint-number parsing to support temporary and case variations.
- Add fallback inference path from refreshed breakpoint inventory when console text parsing fails.

### 4. Attach state inconsistency (`not_started` after successful attach)

Root cause:
- Attach-state transition only forces paused from `unknown`, not from `not_started`.
- Startup with program often sets `not_started`, so attach can leave stale state.

Fix:
- On successful attach, ensure execution state becomes paused (or remains paused) with a meaningful stop reason.
- Preserve target-loaded and attached PID metadata.

### 5. `gdb_continue` on already-running inferior times out

Root cause:
- No precheck for already-running state.
- A second continue may wait for responses that never arrive and degrade to timeout.

Fix:
- Add deterministic precheck in continue path:
  - If already running, return immediate actionable error with guidance to use `gdb_wait_for_stop` or `gdb_interrupt`.

### 6. Interactive CLI command dead-ends and timeout ambiguity

Root cause:
- Interactive-confirm commands (for example `kill`) can block automation if confirmation is on.

Fix:
- Set `set confirm off` by default during session startup.
- Keep risky commands allowed (no blanket denylist).
- Surface startup warning if confirm-disable fails unexpectedly.

Decision note:
- We explicitly keep risky commands available per product requirement.
- We improve default non-interactive behavior rather than restricting command surface.

### 7. Multi-inferior semantics after child exit are confusing

Root cause:
- Runtime tracks one global execution state, which is insufficient for concurrent inferior lifecycles.

Fix (phased):
- Introduce per-inferior state tracking.
- Report selected-inferior state plus full inferior-state summary.
- Refresh inferior inventory on key fork/exit transitions.

### 8. `gdb_get_registers` payload volume is too high

Root cause:
- Command always requests full register set in hex, including large vector state.

Fix (phased):
- Add optional register filtering parameters (IDs/names/group presets).
- Keep full dump available for forensic mode.

## Implementation Phases

### Phase 1: Correctness and contract alignment (immediate)
- Update startup args schema + normalization.
- Add numeric-string compatibility for inspection context args.
- Fix catchpoint temporary parsing false-negative.
- Fix attach paused-state normalization.
- Add already-running precheck for continue.
- Default to `set confirm off` at startup.
- Add/adjust unit tests for each behavior above.
- Update `TOOLS.md` for parameter and startup defaults.

### Phase 2: Multi-inferior model hardening
- Add per-inferior execution-state model in runtime.
- Update selection/status/list responses to report inferiors more explicitly.
- Add tests for fork/child-exit workflows.

### Phase 3: Register payload ergonomics
- Add optional register filters to schema and inspection service.
- Update tests and docs with lightweight and full-capture examples.

## Validation Checklist

- `uv run mypy src`
- `uv run pytest -q`
- `git diff --check`

When iterating quickly, run targeted tests first, then full suite before finalizing.
