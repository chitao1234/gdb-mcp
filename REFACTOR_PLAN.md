# GDB MCP Refactor Plan

## Purpose

This document turns the code-quality audit into an implementation plan that improves structure, not just individual bug behavior.

The goal is to separate transport, session lifecycle, domain logic, and MCP wiring so that:

- GDB/MI protocol handling has one owner.
- Session state changes are explicit and testable.
- MCP tool handlers validate inputs before touching session state.
- The external MCP tool surface remains stable while internals are replaced incrementally.

## Constraints

- Preserve existing tool names and the current user-facing feature set during the refactor.
- Avoid a big-bang rewrite. Each phase must leave the repository in a releasable state.
- Keep the current integration tests running throughout the transition.
- Prefer additive migrations first, then remove old code after the new path is proven.

## Current Structural Problems

The current shape concentrates too much behavior in two files:

- [src/gdb_mcp/gdb_interface.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/gdb_interface.py)
  - Owns process startup, protocol transport, command serialization, parsing, lifecycle state, and debugger convenience methods.
- [src/gdb_mcp/server.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/server.py)
  - Owns MCP server creation, tool schemas, session registry, routing, validation, response serialization, and lifecycle decisions.

This causes the main risks found in the audit:

- protocol errors can be flattened into fake success;
- same-session concurrency has no single owner;
- process-global cwd is used to model per-session config;
- failed startup leaves partially registered sessions;
- handler validation order is inconsistent.

## Target Architecture

The codebase should move toward the following module boundaries:

```text
src/gdb_mcp/
  __init__.py
  __main__.py
  mcp/
    app.py
    handlers.py
    schemas.py
    serializer.py
  domain/
    models.py
    errors.py
    results.py
  session/
    config.py
    registry.py
    service.py
    state.py
  transport/
    mi_client.py
    mi_commands.py
    mi_models.py
    mi_parser.py
```

### Boundary Rules

- `transport/` talks to `pygdbmi` and the GDB subprocess.
- `session/` orchestrates debugger actions and session lifecycle.
- `domain/` defines typed data and error contracts.
- `mcp/` handles MCP schemas, routing, and JSON serialization only.
- The MCP layer must not write to GDB directly.
- The transport layer must not know about MCP or tool names.

## Migration Strategy

The refactor is split into six phases. Each phase ends with tests and a small cleanup pass.

### Phase 1: Introduce typed models and result contracts

Objective: stop passing arbitrary dictionaries across every layer.

Add:

- `src/gdb_mcp/domain/models.py`
- `src/gdb_mcp/domain/errors.py`
- `src/gdb_mcp/domain/results.py`
- `src/gdb_mcp/session/config.py`
- `src/gdb_mcp/session/state.py`

Define:

- `SessionConfig`
  - `program`
  - `args`
  - `init_commands`
  - `env`
  - `gdb_path`
  - `working_dir`
  - `core`
- `SessionState`
  - `CREATED`
  - `STARTING`
  - `READY`
  - `FAILED`
  - `STOPPED`
- typed result wrappers such as:
  - `OperationSuccess`
  - `OperationError`
  - `FatalTransportError`
  - `ValidationFailure`

Rules for this phase:

- No behavior rewrite yet.
- Existing code can still return dicts at the MCP boundary, but internal helpers should begin returning typed values.
- `gdb_interface.py` can import these types before extraction starts.

Acceptance criteria:

- No new tool behavior.
- A new unit test file validates state transitions and model defaults.

### Phase 2: Extract GDB/MI transport into a dedicated client

Objective: isolate protocol correctness and same-session serialization in one place.

Add:

- `src/gdb_mcp/transport/mi_client.py`
- `src/gdb_mcp/transport/mi_models.py`
- `src/gdb_mcp/transport/mi_commands.py`
- `src/gdb_mcp/transport/mi_parser.py`

Move out of `gdb_interface.py`:

- token generation;
- stdin writes;
- response polling;
- liveness checks;
- fatal error detection;
- MI response parsing;
- CLI wrapping and escaping helpers.

`MiClient` responsibilities:

- spawn and own the GDB process;
- use subprocess `cwd` instead of `os.chdir`;
- serialize one in-flight command per session using a lock;
- return a typed `MiCommandResult` that preserves:
  - result class;
  - payload;
  - console output;
  - async notifications;
  - timeout status;
  - fatal status.

Important implementation requirements:

- Treat MI `error` as an error result, not a success result.
- Keep result-class available all the way to the session service.
- Replace the current liveness timebase bug with monotonic timing.

Acceptance criteria:

- new transport unit tests cover:
  - `done` vs `error`;
  - timeout;
  - process death during wait;
  - CLI command wrapping;
  - quoted input handling.

### Phase 3: Turn `GDBSession` into a session service

Objective: make session behavior orchestration-only.

Add:

- `src/gdb_mcp/session/service.py`

Transform:

- `GDBSession` becomes either:
  - a compatibility wrapper around `SessionService`, or
  - a renamed class moved into `session/service.py`.

Responsibilities for `SessionService`:

- manage `SessionState`;
- own `SessionConfig`;
- call `MiClient`;
- map MI responses to domain models;
- expose debugger operations:
  - `start`
  - `stop`
  - `get_status`
  - `get_threads`
  - `select_thread`
  - `get_backtrace`
  - `select_frame`
  - `get_frame_info`
  - `set_breakpoint`
  - `list_breakpoints`
  - `delete_breakpoint`
  - `enable_breakpoint`
  - `disable_breakpoint`
  - `continue_execution`
  - `step`
  - `next`
  - `interrupt`
  - `evaluate_expression`
  - `get_variables`
  - `get_registers`
  - `call_function`

Behavior changes intentionally introduced here:

- startup only reaches `READY` after transport initialization succeeds;
- failure paths move to `FAILED` without mutating process-global cwd;
- invalid transport results cannot be silently promoted to success.

Acceptance criteria:

- parity tests for all existing debugger operations;
- startup and shutdown state transitions are explicit in tests.

### Phase 4: Replace the global session manager with a registry

Objective: make session lifecycle and server shutdown explicit.

Add:

- `src/gdb_mcp/session/registry.py`

`SessionRegistry` responsibilities:

- allocate session IDs;
- create sessions without exposing them as ready until startup succeeds;
- remove failed sessions automatically on startup failure;
- stop all sessions on application shutdown;
- expose `get(session_id)` and `shutdown_all()`.

Structural change:

- remove the current global mutable `session_manager` from [server.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/server.py);
- inject a registry into the MCP app factory instead.

Acceptance criteria:

- failed start does not leave an addressable dead session;
- a shutdown hook exists and is covered by tests.

### Phase 5: Replace the monolithic MCP router with handler functions

Objective: make MCP behavior boring and predictable.

Add:

- `src/gdb_mcp/mcp/app.py`
- `src/gdb_mcp/mcp/handlers.py`
- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/serializer.py`

Refactor approach:

- move Pydantic argument models out of `server.py` into `schemas.py`;
- register a dispatch table:
  - `tool name -> handler function`;
- validate input before session lookup;
- serialize typed domain results in one place.

Handler rules:

- unknown tool names fail before any registry lookup;
- non-dict payloads fail as validation errors;
- session lookup happens only after successful argument parsing;
- MCP responses are produced only in the serializer layer.

Compatibility requirement:

- keep the current tool names and JSON response shape unless a specific response bug is being corrected.

Acceptance criteria:

- routing tests cover:
  - unknown tool;
  - missing `session_id`;
  - wrong payload type;
  - failed startup rollback;
  - stop-session removal.

### Phase 6: Simplify tests around the new boundaries

Objective: reduce brittle mocking and test the correct layer.

Target test layout:

```text
tests/
  domain/
  mcp/
  session/
  transport/
  integration/
```

Test split:

- `transport/`
  - protocol and parsing behavior with fake raw responses;
- `session/`
  - state transitions and command orchestration using a fake `MiClient`;
- `mcp/`
  - schema validation, routing, and serialization;
- `integration/`
  - real GDB workflows only.

Coverage additions required by this refactor:

- MI `error` record handling;
- `working_dir` launch behavior without `os.chdir`;
- same-session concurrency serialization;
- startup rollback in the registry;
- server shutdown cleanup.

Acceptance criteria:

- fast unit tests cover structural guarantees;
- integration tests remain focused on end-to-end debugger behavior, not internal branching.

## File-by-File Transition Plan

The refactor should be landed in a sequence that keeps diff size manageable.

### Commit 1

- add `domain/` and `session/config.py`, `session/state.py`;
- add tests for models and state.

### Commit 2

- add `transport/mi_models.py`, `transport/mi_parser.py`;
- move pure parsing code first;
- add parser tests.

### Commit 3

- add `transport/mi_client.py`;
- move command send/wait logic and serialize command execution;
- add transport tests.

### Commit 4

- add `session/service.py`;
- migrate one narrow vertical slice first:
  - `get_status`
  - `execute_command`
  - `stop`
- keep compatibility wrappers in old code.

### Commit 5

- migrate breakpoint, thread, frame, and expression methods;
- delete duplicated parsing/helpers from the old file.

### Commit 6

- add `session/registry.py`;
- remove startup registration leak;
- add shutdown cleanup.

### Commit 7

- add `mcp/schemas.py`, `mcp/handlers.py`, `mcp/serializer.py`, `mcp/app.py`;
- convert `server.py` into a thin compatibility entrypoint.

### Commit 8

- move and trim tests into boundary-focused layout;
- remove dead compatibility code once green.

## Execution Notes

### Preserve external behavior where practical

The refactor should not rename tools or require client updates. Structural changes are internal unless a response is currently wrong or ambiguous.

### Prefer adapters over edits in place

During the migration, old modules can delegate to new modules. This keeps the external surface stable while reducing the risk of a broad regression.

### Use explicit compatibility shims

Examples:

- keep `run_server()` in [server.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/server.py) but make it call the new app factory;
- keep `GDBSession` importable while it wraps `SessionService` during the transition.

## Risks and Mitigations

- Risk: broad refactor destabilizes integration tests.
  - Mitigation: migrate one vertical slice at a time and keep end-to-end tests green after each commit.

- Risk: response-shape cleanup breaks clients.
  - Mitigation: centralize serialization and treat output shape as a compatibility contract.

- Risk: transport extraction becomes another large god object.
  - Mitigation: split command building, parsing, and process management into separate transport modules from the start.

## Definition of Done

The refactor is complete when all of the following are true:

- `server.py` is a thin entrypoint, not the application core.
- `gdb_interface.py` no longer mixes transport, parsing, lifecycle, and business logic.
- same-session command execution is serialized by design.
- startup failure cannot leak a registered dead session.
- per-session configuration does not mutate process-global cwd.
- MI `error` results are represented explicitly and cannot be mistaken for success.
- unit tests are organized around transport, session, and MCP boundaries.
- integration tests still pass against a real GDB process.

## Recommended First Implementation Slice

The first implementation PR after this document should do only this:

1. add domain result types and session config/state;
2. add `transport/mi_models.py` and `transport/mi_parser.py`;
3. move pure response parsing out of `gdb_interface.py`;
4. add tests for MI result-class preservation.

That slice is small enough to review and large enough to establish the new architecture.
