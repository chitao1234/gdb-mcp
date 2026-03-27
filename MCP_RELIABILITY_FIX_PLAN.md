# MCP Reliability Fix Plan

## Goal

Fix the issues found by external MCP tester agents while keeping compatibility for existing clients where practical. This plan covers code, tests, and documentation updates.

## Phase 1: Repro Coverage and Guardrails

- [ ] Add regression tests that reproduce:
  - [ ] `gdb_start_session(core=...)` symbol-resolution loss.
  - [ ] `gdb_continue` timeout behavior on long-running inferiors.
  - [ ] `gdb_set_watchpoint(access="access")` false-negative status.
  - [ ] Missing structured `exit_code` despite stop payload containing `exit-code`.
  - [ ] Schema-shape friction around `gdb_batch.steps`.
  - [ ] Schema-shape friction around `gdb_capture_bundle.memory_ranges`.
  - [ ] `gdb_run_until_failure.capture.bundle_name` rejection.

## Phase 2: Core Startup Correctness

- [ ] Update startup argv construction for `program + core` so symbols and locals resolve reliably.
- [ ] Keep `core`-only mode working and document expected symbol-quality differences.
- [ ] Add integration assertion that symbolized frames and locals are available for `program + core`.

## Phase 3: Continue/Wait Semantics for Long-Running Targets

- [ ] Make `gdb_continue` return success when execution is acknowledged as `running` and no stop occurs within timeout.
- [ ] Keep `gdb_wait_for_stop` as the blocking API for stop detection.
- [ ] Ensure runtime/transcript metadata clearly represent this state.
- [ ] Add/adjust tests for running-ack success plus follow-up wait behavior.

## Phase 4: Watchpoint Result Correctness

- [ ] Expand watchpoint-number extraction to handle MI payload variants used by read/access watchpoints.
- [ ] Ensure `gdb_set_watchpoint` returns success when watchpoint is created.
- [ ] Add regression tests for write/read/access watchpoint payloads.

## Phase 5: Exit Code Parsing and Propagation

- [ ] Make exit-code parsing robust for decimal, zero-padded decimal, and hex-like encodings.
- [ ] Propagate parsed values consistently through:
  - [ ] `gdb_get_status`
  - [ ] `gdb_wait_for_stop`
  - [ ] stop history / last stop event
  - [ ] `gdb_run_until_failure`
- [ ] Add tests that cover representative `exit-code` payload strings.

## Phase 6: Compatibility Aliases for Common Client Friction

- [ ] Accept `gdb_run.args` as either `list[str]` or `string` (normalize string safely).
- [ ] Support shorthand-friendly input for `gdb_batch.steps` while preserving structured form.
- [ ] Support shorthand-friendly input for `gdb_capture_bundle.memory_ranges` while preserving structured form.
- [ ] Accept `gdb_run_until_failure.capture.bundle_name` and map it to deterministic naming behavior.
- [ ] Define and enforce clear precedence rules when alias fields conflict.

## Phase 7: Documentation and Contract Sync

- [ ] Update `README.md`:
  - [ ] Current tool count.
  - [ ] Multi-session behavior (replace outdated single-session wording).
  - [ ] `gdb_run` / `gdb_continue` / `gdb_wait_for_stop` expected workflow.
- [ ] Update `TOOLS.md`:
  - [ ] Add/refresh entries for batch/capture/campaign tools.
  - [ ] Document accepted input shapes (including aliases) with valid JSON examples.
  - [ ] Clarify typed numeric fields and expected JSON number usage.
- [ ] Add a short “MCP context model” section describing exactly what clients receive from `list_tools` and `call_tool`.

## Phase 8: Cleanup and Validation

- [ ] Prevent confusion from stale local artifacts in developer workflows (especially old `build/lib` trees).
- [ ] Run full validation:
  - [ ] `uv run mypy src`
  - [ ] `uv run pytest -q`
  - [ ] `git diff --check`
- [ ] Prepare concise migration notes for client maintainers.

## Delivery Sequence

1. Phase 1 tests for critical bugs.
2. Phases 2-5 bug fixes with targeted commits.
3. Phase 6 compatibility improvements.
4. Phase 7 documentation sync.
5. Phase 8 final validation and cleanup.
