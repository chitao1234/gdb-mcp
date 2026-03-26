# Test Refactor Plan

## Purpose

The production refactor is largely complete. The remaining structural debt is now
concentrated in the test suite.

This document replaces the old architecture plan with a concrete test-refactor
plan. The goal is to make the tests match the current boundaries:

- `domain/` owns typed result contracts.
- `transport/` owns GDB/MI protocol behavior.
- `session/` owns debugger orchestration.
- `mcp/` owns request validation, routing, and serialization.
- `server.py` is only a thin entrypoint and compatibility surface.

The test suite should reflect those boundaries directly instead of routing core
coverage through compatibility paths or monolithic files.

## Current Problems

### 1. Session tests are still monolithic

[tests/session/test_session_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_session_api.py)
is over 1000 lines and mixes:

- lifecycle startup/shutdown;
- execution control;
- breakpoints;
- inspection/navigation;
- error handling;
- parser-adjacent assertions.

This increases maintenance cost and makes failures noisy.

### 2. Session tests duplicate setup and patch scaffolding

Repeated patterns currently appear across the session suite:

- constructing a default session;
- marking it as running;
- injecting a mock controller;
- patching `_execute_command_result`;
- patching `_send_command_and_wait_for_prompt`;
- building small MI result payloads inline.

This obscures intent and makes structural edits expensive.

### 3. Too many tests are coupled to internals

Many tests still patch private methods instead of using public behavior with
realistic doubles at the transport boundary. That makes the suite brittle
against internal refactors.

### 4. Integration tests use duplicated harnesses and compatibility entrypoints

The integration suite currently:

- duplicates `call_gdb_tool(...)`;
- duplicates program compilation helpers;
- relies on `gdb_mcp.server.call_tool`;
- implicitly shares global singleton server state.

That is no longer the right architectural boundary.

### 5. MCP and schema tests still lean on compatibility surfaces

Some tests still import models or exercise behavior through `server.py` even
though the real ownership now lives under `mcp/`.

### 6. Typed-result contract coverage is still shallow

The typed result migration is now central to the codebase, but tests still cover
only the basic happy path in some places.

## Goals

After this plan is complete:

- session tests are split by responsibility;
- shared session fixtures/builders replace duplicated scaffolding;
- parser tests live under transport-facing coverage, not session API tests;
- integration tests use a shared harness and isolated runtime/registry setup;
- MCP handler tests target handler/runtime layers directly;
- schema tests import from `gdb_mcp.mcp.schemas`;
- typed result serialization and mapping behavior has explicit contract tests.

## Constraints

- Keep the suite green after each commit.
- Prefer small structural slices over one broad rewrite.
- Do not preserve unnecessary compatibility-only test patterns.
- Keep integration coverage real; reduce duplication rather than reducing rigor.
- Continue to use `.venv/bin/pytest`.

## Work Plan

### Phase 1: Split the session API monolith

Replace [tests/session/test_session_api.py](/home/chi/ddev/gdb-mcp/tests/session/test_session_api.py)
with focused modules such as:

- `tests/session/test_lifecycle_api.py`
- `tests/session/test_execution_api.py`
- `tests/session/test_inspection_api.py`
- `tests/session/test_breakpoints_api.py`
- `tests/session/test_error_paths_api.py`

Keep ownership clear:

- `test_service.py` covers dependency injection and service construction.
- `test_types.py` covers config/state invariants.
- split API tests cover user-visible session behavior.

Acceptance criteria:

- no single session test file remains a grab bag;
- lifecycle, execution, inspection, breakpoints, and error paths each have a
  clear owner;
- parser-specific assertions no longer live in the session API tests.

### Phase 2: Add shared session fixtures and builders

Add test helpers, likely in:

- `tests/session/conftest.py`
- optionally `tests/session/builders.py`

Extract shared helpers for:

- `running_session`;
- startup-success transport responses;
- command execution payloads;
- common MI result fragments;
- controller doubles.

Acceptance criteria:

- repeated setup in session tests is removed;
- tests read in terms of intent, not transport-shape boilerplate.

### Phase 3: Move parser/protocol assertions to the transport boundary

Relocate parser-adjacent checks now living in session tests into:

- [tests/transport/test_mi_client.py](/home/chi/ddev/gdb-mcp/tests/transport/test_mi_client.py)
- or a new transport parser test module if needed.

This includes:

- MI response parsing expectations;
- command wrapping expectations that belong to transport/protocol behavior.

Acceptance criteria:

- session tests assert session behavior;
- transport tests assert parser/protocol behavior.

### Phase 4: Refactor MCP tests to target the real boundary

Reduce reliance on `gdb_mcp.server.call_tool` for primary MCP behavior tests.

Shift primary coverage toward:

- `dispatch_tool_call(...)`;
- runtime injection tests;
- serializer tests.

Keep only thin smoke coverage for the server entrypoint.

Acceptance criteria:

- handler tests no longer depend on patching the global server singleton for
  ordinary routing coverage;
- schema tests import from `gdb_mcp.mcp.schemas`, not `gdb_mcp.server`.

### Phase 5: Extract a shared integration harness

Add shared integration helpers, likely in:

- `tests/integration/conftest.py`

Extract:

- `call_gdb_tool(...)`;
- source compilation helpers;
- session startup helpers;
- robust teardown helpers;
- isolated runtime/registry fixtures where practical.

Acceptance criteria:

- no duplicated tool-call helper across integration files;
- start/stop behavior is centralized;
- integration tests are less vulnerable to shared global state.

### Phase 6: Tighten contract coverage and test hygiene

Add explicit contract coverage for:

- `OperationSuccess` warning propagation;
- nested payload normalization through `result_to_mapping(...)`;
- `OperationError` detail serialization;
- serializer behavior for typed payload objects.

Also clean up:

- stale imports in schema tests;
- outdated instructions in [tests/README.md](/home/chi/ddev/gdb-mcp/tests/README.md).

Acceptance criteria:

- typed result behavior is tested as a stable contract;
- test docs match the current repo workflow.

## Commit Strategy

Each phase should land as its own commit when practical:

1. replace this plan and commit it;
2. split session tests and extract shared session fixtures;
3. move parser checks out of session tests;
4. refactor MCP tests to real ownership boundaries;
5. extract integration harness;
6. tighten typed-result coverage and docs.

## Immediate Next Slice

Start with Phase 1 and Phase 2 together:

- split `tests/session/test_session_api.py`;
- add shared session fixtures/builders;
- keep behavior unchanged;
- run `tests/session` and commit the slice.
