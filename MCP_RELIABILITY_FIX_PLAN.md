# MCP Reliability Fix Plan

## Goal

Fix the issues found by external MCP tester agents while keeping compatibility for existing clients where practical. This plan covers code, tests, and documentation updates.

## Phase 1: Repro Coverage and Guardrails

- [x] Add regression tests that reproduce:
  - [x] `gdb_start_session(core=...)` symbol-resolution loss.
  - [x] `gdb_continue` timeout behavior on long-running inferiors.
  - [x] `gdb_set_watchpoint(access="access")` false-negative status.
  - [x] Missing structured `exit_code` despite stop payload containing `exit-code`.
  - [x] Schema-shape friction around `gdb_batch.steps`.
  - [x] Schema-shape friction around `gdb_capture_bundle.memory_ranges`.
  - [x] `gdb_run_until_failure.capture.bundle_name` rejection.

## Phase 2: Core Startup Correctness

- [x] Update startup argv construction for `program + core` so symbols and locals resolve reliably.
- [x] Keep `core`-only mode working and document expected symbol-quality differences.
- [x] Add integration assertion that symbolized frames and locals are available for `program + core`.

## Phase 3: Continue/Wait Semantics for Long-Running Targets

- [x] Make `gdb_continue` return success when execution is acknowledged as `running` and no stop occurs within timeout.
- [x] Keep `gdb_wait_for_stop` as the blocking API for stop detection.
- [x] Ensure runtime/transcript metadata clearly represent this state.
- [x] Add/adjust tests for running-ack success plus follow-up wait behavior.

## Phase 4: Watchpoint Result Correctness

- [x] Expand watchpoint-number extraction to handle MI payload variants used by read/access watchpoints.
- [x] Ensure `gdb_set_watchpoint` returns success when watchpoint is created.
- [x] Add regression tests for write/read/access watchpoint payloads.

## Phase 5: Exit Code Parsing and Propagation

- [x] Make exit-code parsing robust for decimal, zero-padded decimal, and hex-like encodings.
- [x] Propagate parsed values consistently through:
  - [x] `gdb_get_status`
  - [x] `gdb_wait_for_stop`
  - [x] stop history / last stop event
  - [x] `gdb_run_until_failure`
- [x] Add tests that cover representative `exit-code` payload strings.

## Phase 6: Compatibility Aliases for Common Client Friction

- [x] Accept `gdb_run.args` as either `list[str]` or `string` (normalize string safely).
- [x] Support shorthand-friendly input for `gdb_batch.steps` while preserving structured form.
- [x] Support shorthand-friendly input for `gdb_capture_bundle.memory_ranges` while preserving structured form.
- [x] Accept `gdb_run_until_failure.capture.bundle_name` and map it to deterministic naming behavior.
- [x] Define and enforce clear precedence rules when alias fields conflict.

## Phase 7: Documentation and Contract Sync

- [x] Update `README.md`:
  - [x] Current tool count.
  - [x] Multi-session behavior (replace outdated single-session wording).
  - [x] `gdb_run` / `gdb_continue` / `gdb_wait_for_stop` expected workflow.
- [x] Update `TOOLS.md`:
  - [x] Add/refresh entries for batch/capture/campaign tools.
  - [x] Document accepted input shapes (including aliases) with valid JSON examples.
  - [x] Clarify typed numeric fields and expected JSON number usage.
- [x] Add a short “MCP context model” section describing exactly what clients receive from `list_tools` and `call_tool`.

## Phase 8: Cleanup and Validation

- [x] Prevent confusion from stale local artifacts in developer workflows (especially old `build/lib` trees).
- [x] Run full validation:
  - [x] `uv run mypy src`
  - [x] `uv run pytest -q`
  - [x] `git diff --check`
- [x] Prepare concise migration notes for client maintainers.

## Delivery Sequence

1. Phase 1 tests for critical bugs.
2. Phases 2-5 bug fixes with targeted commits.
3. Phase 6 compatibility improvements.
4. Phase 7 documentation sync.
5. Phase 8 final validation and cleanup.
