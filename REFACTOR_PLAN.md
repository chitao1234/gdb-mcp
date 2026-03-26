# Test Refactor Status

## Purpose

The production-side refactor is complete enough that the main remaining
structural work has been in the test suite. This document now serves as a
status summary rather than a forward-looking migration plan.

The goal of the test refactor was to align test ownership with the current code
boundaries:

- `domain/` owns typed result contracts.
- `transport/` owns GDB/MI protocol behavior.
- `session/` owns debugger orchestration.
- `mcp/` owns request validation, routing, and serialization.
- `server.py` is only a thin entrypoint and compatibility surface.

## Completed Work

### 1. Session test monolith removed

The old
[tests/session/test_session_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_session_api.py)
has been deleted and replaced with focused test modules:

- [tests/session/test_lifecycle_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_lifecycle_api.py)
- [tests/session/test_execution_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_execution_api.py)
- [tests/session/test_inspection_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_inspection_api.py)
- [tests/session/test_breakpoints_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_breakpoints_api.py)
- [tests/session/test_error_paths_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_error_paths_api.py)

Ownership is now clearer:

- [tests/session/test_service.py](/home/chi/ddev/gdb-mcp/tests/session/test_service.py)
  covers service construction and dependency injection.
- [tests/session/test_types.py](/home/chi/ddev/gdb-mcp/tests/session/test_types.py)
  covers config/state invariants.
- split API tests cover user-visible session behavior.

### 2. Shared session fixtures added

Shared test scaffolding now lives in
[tests/session/conftest.py](/home/chi/ddev/gdb-mcp/tests/session/conftest.py).

This provides:

- reusable session construction;
- running-session setup;
- typed command-result builders;
- prompt-response builders.

That removed a large amount of duplicated setup boilerplate from the session
suite.

### 3. Parser assertions moved to transport coverage

Parser-only checks no longer live in the session API tests. They now live under
transport coverage in
[tests/transport/test_mi_parser.py](/home/chi/ddev/gdb-mcp/tests/transport/test_mi_parser.py).

### 4. MCP tests target the real boundary

Primary MCP routing coverage now exercises
[dispatch_tool_call(...)](/home/chi/ddev/gdb-mcp/src/gdb_mcp/mcp/handlers.py)
directly in
[tests/mcp/test_handlers.py](/home/chi/ddev/gdb-mcp/tests/mcp/test_handlers.py).

Schema tests now import from
[src/gdb_mcp/mcp/schemas.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/mcp/schemas.py)
instead of using the compatibility exports from `server.py`.

A thin compatibility smoke test remains in
[tests/mcp/test_server_entrypoint.py](/home/chi/ddev/gdb-mcp/tests/mcp/test_server_entrypoint.py).

### 5. Shared integration harness extracted

Integration helpers now live in
[tests/integration/conftest.py](/home/chi/ddev/gdb-mcp/tests/integration/conftest.py).

This centralizes:

- runtime construction;
- isolated `SessionRegistry` ownership;
- tool calling;
- program compilation;
- shared session start helpers.

The integration suite no longer depends on the global singleton in `server.py`
for its main execution path.

### 6. Typed-result contract coverage expanded

Typed-result coverage was extended in:

- [tests/domain/test_results.py](/home/chi/ddev/gdb-mcp/tests/domain/test_results.py)
- [tests/mcp/test_serializer.py](/home/chi/ddev/gdb-mcp/tests/mcp/test_serializer.py)

This now covers:

- warning propagation;
- nested typed payload normalization;
- external error payload shape;
- serializer behavior for typed payload objects.

### 7. Integration assertions tightened

Low-signal integration tests were tightened so they assert actual behavioral
contracts instead of mostly checking that commands do not crash.

This includes better checks for:

- expression evaluation;
- temporary breakpoint cleanup;
- invalid breakpoint targets;
- session invalidation after stop;
- frame-sensitive variable inspection.

### 8. Test documentation refreshed

[tests/README.md](/home/chi/ddev/gdb-mcp/tests/README.md) now reflects:

- `.venv/bin/pytest` usage;
- the split session test layout;
- the parser test module;
- the runtime-backed integration harness.

## Remaining Polish

The high-value structural work is done. Remaining items are lower priority:

- some session tests still patch private helpers such as
  `_execute_command_result` or `_send_command_and_wait_for_prompt`;
- some integration tests still call `call_gdb_tool(...)` directly rather than
  always going through the higher-level helper fixtures;
- the suite could be made even more declarative by adding richer fake transport
  objects for certain session tests.

These are worthwhile only if more cleanup is desired. They are no longer major
architectural debt.

## Definition of Mostly Complete

This refactor is now mostly complete because:

- no test monolith remains in the session layer;
- parser tests live with transport tests;
- MCP tests use handler/runtime boundaries directly;
- integration tests use a shared isolated harness;
- typed-result contracts have explicit tests;
- the full suite remains green after each slice.

## Verification

Current state should always be verified with:

```bash
.venv/bin/pytest -q
```
