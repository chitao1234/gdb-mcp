# V2 Test Suite Cleanup Design

**Date:** 2026-04-02

## Goal

Trim the test suite to a smaller, higher-signal shape that matches the v2 MCP interface end to end:

- remove legacy translation from the integration harness
- migrate surviving tests to direct v2 tool names and payloads
- delete low-signal or redundant tests that duplicate lower-layer coverage
- keep strong behavioral coverage at the true ownership boundaries

## Background

The product surface is now v2-only:

- the public MCP inventory exposes 17 v2 tools
- docs describe only the v2 interface
- runtime routing and schemas are built around `*_query`, `*_manage`, and dedicated workflow tools

The main remaining mismatch is in the tests:

1. `tests/integration/conftest.py` still contains a legacy-to-v2 translation layer.
2. The integration tests still call many legacy tool names and rely on flattened legacy response shapes.
3. The integration suite is overly broad in places, repeating edge cases and permutations that are already tested more directly in `tests/mcp/` and `tests/session/`.

This creates maintenance burden in three ways:

- the suite still encodes an interface we explicitly broke
- integration tests carry low-value duplication instead of focusing on cross-layer proof
- future v2 changes will keep paying a tax to support test-only legacy habits

## Scope

This cleanup covers:

- `tests/integration/conftest.py`
- `tests/integration/test_gdb_integration.py`
- `tests/integration/test_multi_session.py`
- `tests/README.md`
- any stale unit tests that still encode pre-v2 expectations or duplicate already-owned coverage

This cleanup may also simplify or delete tests under:

- `tests/domain/`
- `tests/session/`
- `tests/mcp/`

but only when the deleted coverage is clearly redundant with stronger tests at the correct layer.

## Non-Goals

- reducing coverage by removing ownership-boundary tests in `tests/mcp/` or `tests/session/`
- weakening transport/parser coverage in `tests/transport/`
- changing product behavior as part of the test cleanup
- preserving any legacy tool-name compatibility inside tests
- making the integration suite exhaustive again under v2

## Design Principles

1. Tests should use the same interface the product exposes.
2. Each behavior should have one primary owner in the suite.
3. Integration tests should prove important real-GDB workflows, not restate all unit-level edge cases.
4. Redundant tests are acceptable only when they prove something a lower layer cannot.
5. Delete tests that mostly assert implementation shape rather than user-visible contract.

## Test Ownership Model

### `tests/mcp/`

This layer owns the public MCP boundary:

- tool inventory
- schema validation
- action routing
- envelope shape
- batch-step validation
- runtime dispatch

If a question is “does the MCP surface accept or reject this request shape?” or “does this tool/action route correctly?”, `tests/mcp/` owns it.

### `tests/session/`

This layer owns debugger service behavior:

- execution semantics
- breakpoint semantics
- inferior management
- frame/thread selection
- error details and state transitions
- capture and campaign behavior

If a question is “what does the session service do when GDB behaves like this?”, `tests/session/` owns it.

### `tests/integration/`

This layer owns real-GDB proof:

- representative end-to-end flows
- cross-layer composition
- tool families working against a live debugger
- workflow tools operating against real sessions

If a scenario is already covered exhaustively in `tests/mcp/` or `tests/session/`, integration keeps at most one representative happy-path or one representative failure-path, not all permutations.

### `tests/domain/` and `tests/transport/`

These remain narrow:

- `tests/domain/` covers typed result serialization helpers when that logic is not already more clearly owned by `tests/mcp/test_serializer.py`
- `tests/transport/` covers MI client and parser behavior

## Integration Suite Target Shape

The current integration suite has 45 tests in `test_gdb_integration.py` and 9 tests in `test_multi_session.py`. That is more breadth than is useful for a live-GDB layer once `tests/mcp/` and `tests/session/` already cover routing and semantics in detail.

The target integration suite should be compact and organized around domain workflows.

### Keep And Rewrite

The suite should retain direct-v2 coverage for these workflow categories:

1. Session lifecycle
   - start session with program
   - inspect session status
   - stop session and confirm later calls fail
   - startup failure cases that matter to real users, such as missing program and core/program loading flows

2. Breakpoints
   - one code-breakpoint create/query/run flow
   - one watchpoint flow
   - one catchpoint flow
   - one representative mutation flow for delete or enable/disable, not every permutation

3. Execution
   - run to stop
   - non-blocking run plus `wait_for_stop`
   - representative step/next/finish flow
   - one exit-state flow after continue

4. Context and inspect
   - backtrace
   - frame info or frame selection
   - variables with frame preservation
   - memory
   - source
   - disassembly
   - expression evaluation

5. Inferiors and session inventory
   - create/select/remove inferior
   - follow-fork and detach-on-fork metadata updates
   - multi-session list/status behavior

6. Workflow tools
   - one `gdb_workflow_batch`
   - one `gdb_capture_bundle`
   - one `gdb_run_until_failure`

7. Privileged/specialized tools
   - one `gdb_attach_process` flow if it remains reliable
   - one `gdb_execute_command` flow as an escape hatch

### Delete

The integration suite should delete tests that are mostly permutations of already-owned behavior, especially:

- repeated breakpoint list/count workflows that differ only by one mutation step
- repeated multi-session isolation variants for breakpoints, variables, and execution when one representative isolation flow already proves session routing
- repeated thread/frame navigation variants that are more precisely covered in `tests/session/test_inspection_api.py`
- repeated command-shape edge cases that are already covered in `tests/mcp/test_schemas.py`, `tests/mcp/test_handlers.py`, or `tests/session/test_execution_api.py`
- low-value assertions that exist only because responses were previously flat or legacy-shaped

Concretely, the deletions should come primarily from these current patterns:

- multiple breakpoint workflow permutations in `tests/integration/test_gdb_integration.py`
- several overlapping session-isolation scenarios in `tests/integration/test_multi_session.py`
- repeated frame/thread manipulation flows that all prove the same session behavior

The goal is not to preserve a large test count. The goal is to preserve coverage that would actually catch a real integration regression.

## Legacy Test Harness Removal

`tests/integration/conftest.py` should stop translating legacy tool names and response shapes.

The following helpers should be removed:

- `_flatten_action_result`
- `_translate_location`
- `_translate_context`
- `_translate_batch_steps`
- `_translate_legacy_call`

`call_gdb_tool()` should call the requested MCP tool directly and return the payload as-is.

Fixtures such as `start_session_result` and `stop_session` should use direct v2 calls and direct v2 assertions.

The surviving integration tests should then be rewritten to:

- call only v2 tool names
- send v2 request payloads directly
- assert v2 success envelopes and nested `result` payloads directly

## Unit-Test Trimming Rules

Outside integration, trimming should be selective.

### Keep

- all `tests/mcp/` coverage that asserts public-tool inventory, action validation, envelope shape, and routing
- all `tests/session/` coverage that asserts real service semantics or transport-derived behavior
- transport tests

### Merge Or Delete When Redundant

Candidates for removal or consolidation:

- tiny domain tests that merely restate serializer behavior already covered more completely in `tests/mcp/test_serializer.py`
- session tests that only assert error-detail field placement when another nearby test already covers the same behavior for the same method
- tests whose only purpose was to protect legacy flattened payload shapes

The default bias should still be conservative at lower layers. Most of the actual size reduction should come from integration cleanup, not from hollowing out unit coverage.

## File-Level Outcome

### `tests/integration/conftest.py`

- simplify to a direct runtime harness
- remove legacy compatibility helpers
- keep only fixtures needed for compiling programs, session setup, and cleanup

### `tests/integration/test_gdb_integration.py`

- rewrite surviving tests to direct v2 calls
- delete redundant scenarios
- group coverage by v2 domains instead of legacy tool names

### `tests/integration/test_multi_session.py`

- rewrite surviving tests to direct v2 calls
- reduce to a small set of representative isolation tests

### `tests/README.md`

- document the new ownership model
- state explicitly that integration tests must not use legacy tool names or response flattening
- describe integration as representative workflow coverage, not exhaustive edge-case coverage

## Verification Strategy

The cleanup should be driven by repeated narrow checks and one full pass at the end.

Minimum targeted checks:

- `uv run pytest -q tests/integration/test_gdb_integration.py tests/integration/test_multi_session.py`
- `uv run pytest -q tests/mcp`
- `uv run pytest -q tests/session`

Final verification:

- `uv run ruff check src tests`
- `uv run mypy src`
- `uv run pytest -q`
- `git diff --check`

## Risks And Mitigations

### Risk: Delete too much integration coverage

Mitigation:

- keep one representative workflow per domain and one per workflow tool
- only delete a test if the same behavior is already better owned in `tests/mcp/` or `tests/session/`

### Risk: Rewrite introduces silent shape drift

Mitigation:

- integration tests must assert direct v2 envelopes instead of relying on helper flattening
- preserve `tests/mcp/test_handlers.py` and `tests/mcp/test_schemas.py` as the canonical surface tests

### Risk: Suite shrink makes regressions harder to catch

Mitigation:

- trim primarily at the integration layer where duplication is highest
- keep low-level semantic and routing tests intact unless duplication is obvious and local

## Success Criteria

This cleanup is complete when all of the following are true:

1. No integration helper translates legacy tool names or flattens legacy response shapes.
2. Surviving integration tests call only v2 tool names with direct v2 payloads.
3. The integration suite is materially smaller and more workflow-oriented than the current 54-test shape.
4. `tests/README.md` documents the v2-only rule and the ownership model.
5. The full repo verification passes.

## Summary

The right cleanup is not “rewrite every old test one-for-one.” It is:

- remove the legacy shim
- keep strong coverage at the real ownership boundaries
- preserve only representative end-to-end GDB workflows
- delete duplicated integration permutations that no longer justify their maintenance cost

That yields a test suite that matches the product interface, is easier to evolve, and still protects the behaviors that matter.
