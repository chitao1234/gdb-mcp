# GDB MCP Remediation Plan

This document replaces the previous refactor plan.

The old plan mixed broader cleanup with bugs that are no longer the current
priority. The immediate work should be driven by the audit findings reproduced
against the live runtime on 2026-03-27.

## Scope

This plan addresses these concrete defects:

- dead GDB processes remain visible as healthy, running sessions;
- read-only inspection calls mutate selected thread or frame state;
- startup can report `target_loaded=true` even when no target was loaded;
- breakpoint locations are not MI-quoted, so paths with spaces fail;
- backtrace limits are off by one relative to the public `max_frames`
  contract.

This plan also closes the related test and documentation gaps that allowed
those issues to survive.

## Goals

- make session status reflect transport reality after process death;
- separate read-only inspection from explicit state-changing selection tools;
- make startup status truthful when program or core loading fails;
- make MI command construction safe for real-world file paths;
- align the backtrace implementation with the documented API contract;
- add regression coverage for each defect before or alongside the code change.

## Non-Goals

- adding new debugger features;
- broad architectural reshuffling unrelated to the audited issues;
- changing MCP tool names or the overall response envelope shape;
- redesigning the domain model for style reasons.

## Delivery Order

1. Fix session liveness and status truth first.
2. Fix startup target-loading truth next.
3. Remove hidden state mutation from inspection APIs.
4. Fix MI command construction and backtrace limit behavior.
5. Update tests and docs to lock the contract down.

The first two items come first because they affect whether callers can trust
the session at all. The later items are correctness and contract issues inside
an otherwise live session.

## Workstream 1: Session Liveness And Status Truth

### Problem

When the GDB child process exits unexpectedly, the current flow can return a
plain transport error without clearing the controller or transitioning the
session out of the running state. `gdb_get_status` can then continue to report
`is_running=true`, `target_loaded=true`, and `has_controller=true` even though
the process is gone.

### Implementation plan

1. Define one terminal state policy for dead transports.
   Recommended policy:
   - if the GDB process is known dead, the session is not runnable anymore;
   - runtime state must not continue to advertise `is_running=true`;
   - controller references should be cleared as part of terminal cleanup.

2. Make transport death explicit in `MiClient`.
   Update the transport so that process-death detection and unrecoverable I/O
   errors are surfaced as terminal transport failures, not soft command
   errors.

3. Centralize runtime transition on terminal transport failure.
   `SessionCommandRunner` should handle both `fatal=True` and explicit dead
   transport responses the same way:
   - clear or invalidate the controller;
   - transition the runtime to a terminal failed or disconnected state;
   - preserve the failure message for later inspection.

4. Tighten `get_status()`.
   Status should be derived from authoritative runtime state and controller
   reality. If a controller exists but the process is dead, `get_status()`
   should not report a healthy running session.

5. Recheck registry close and shutdown behavior for dead sessions.
   `close_session()` and `shutdown_all()` should remove dead sessions cleanly
   without requiring a second transport round-trip that assumes GDB is still
   alive.

### Suggested code changes

- extend runtime state handling in
  [session/runtime.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/runtime.py);
- update dead-process handling in
  [transport/mi_client.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/transport/mi_client.py);
- normalize terminal error handling in
  [session/command_runner.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/command_runner.py);
- review lifecycle and registry cleanup paths in
  [session/lifecycle.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/lifecycle.py) and
  [session/registry.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/registry.py).

### Acceptance criteria

- after the GDB child exits unexpectedly, `gdb_get_status` no longer reports a
  healthy running session;
- follow-up commands fail with a consistent terminal-session error;
- dead sessions can be closed and removed without inconsistent status fields.

## Workstream 2: Startup Truth And Target Loading

### Problem

Startup currently treats `program` or `core` input as sufficient proof that a
target is loaded, even when GDB did not actually load the file. This produces
false-positive `target_loaded=true` states and hides missing-target failures.

### Implementation plan

1. Separate "request asked for a target" from "GDB loaded a target".
   The runtime should only set `target_loaded=true` after a successful,
   confirmed target-load action.

2. Replace the unconditional target-loaded assignment with explicit evidence.
   Recommended evidence sources:
   - successful startup console or MI output from initial program/core load;
   - successful `file` or `core-file` init commands;
   - successful attach or equivalent explicit target-loading operations.

3. Promote missing-target startup cases from warning-only to truthful state.
   If GDB starts but the requested target was not found or was not a valid
   executable/core, return either:
   - a startup error; or
   - a success result with `target_loaded=false` and a clear warning.

4. Make the policy explicit and consistent.
   Pick one contract and use it across code, tests, and docs:
   - recommended contract: starting GDB itself may succeed even if target
     loading fails, but session status must still report `target_loaded=false`.

5. Audit the startup warning extraction path.
   The current readiness probe may not include target-load diagnostics for all
   cases. The plan should verify exactly which startup output contains file
   load errors and adjust parsing accordingly.

### Suggested code changes

- refine startup parsing and state transitions in
  [session/lifecycle.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/lifecycle.py);
- introduce a small helper for target-load state decisions instead of writing
  directly to `runtime.target_loaded` from multiple branches.

### Acceptance criteria

- starting with a missing executable no longer yields `target_loaded=true`;
- startup responses clearly distinguish "GDB started" from "target loaded";
- init commands that successfully load a target flip `target_loaded` to true;
- docs describe the final policy precisely.

## Workstream 3: Inspection APIs Must Not Mutate Selection Implicitly

### Problem

`gdb_get_backtrace(thread_id=...)` and `gdb_get_variables(thread_id=..., frame=...)`
currently change the selected thread or frame as a side effect. That violates
the intent of the dedicated `gdb_select_thread` and `gdb_select_frame` tools
and makes later commands run in a surprising context.

### Implementation plan

1. Declare the contract explicitly.
   Read-only inspection tools should not leave debugger selection changed after
   they return, unless the tool is specifically a selector.

2. Choose one of two implementation strategies.
   Recommended strategy:
   - capture the current selection;
   - switch temporarily if the underlying GDB command requires it;
   - restore the prior selection before returning.

   Alternative strategy:
   - use MI commands that accept explicit thread or frame inputs without
     changing global selection, where GDB supports them.

3. Make runtime bookkeeping match the visible contract.
   `mark_thread_selected()` and `mark_frame_selected()` should only be called
   from explicit selection tools or from temporary-switch logic after the
   original selection has been restored.

4. Review related methods for the same smell.
   `get_backtrace`, `get_variables`, and any future helpers that issue
   `-thread-select` or `-stack-select-frame` should be audited together.

### Suggested code changes

- refactor selection handling in
  [session/inspection.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/inspection.py);
- keep runtime selection tracking authoritative and explicit in
  [session/runtime.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/runtime.py).

### Acceptance criteria

- `gdb_get_backtrace(thread_id=...)` does not permanently change the current
  thread;
- `gdb_get_variables(frame=...)` does not permanently change the current
  frame;
- `gdb_select_thread` and `gdb_select_frame` remain the only intentional
  selection-changing tools from the caller’s perspective.

## Workstream 4: Safe MI Command Construction For Breakpoints

### Problem

Breakpoint conditions are quoted, but breakpoint locations are not. Paths,
function signatures, or other valid locations containing spaces are split into
multiple MI arguments and rejected by GDB.

### Implementation plan

1. Treat breakpoint location as a structured MI argument, not raw text.
   Apply MI quoting to `location` the same way expressions and conditions are
   quoted elsewhere.

2. Review other command builders for the same pattern.
   Check for additional MI commands that append user-controlled strings without
   quoting.

3. Preserve existing behavior for common simple locations.
   The quoting fix should be transparent for cases like `main` or `foo.c:42`.

### Suggested code changes

- update breakpoint command assembly in
  [session/breakpoints.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/breakpoints.py);
- optionally add a dedicated helper in
  [transport/mi_commands.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/transport/mi_commands.py)
  if the project wants one consistent place for MI argument encoding.

### Acceptance criteria

- breakpoint locations that include spaces succeed;
- current simple breakpoint tests still pass unchanged;
- there is regression coverage for path-with-spaces input.

## Workstream 5: Backtrace Limit Contract

### Problem

The public `max_frames` argument is documented as the maximum number of frames
to retrieve, but the implementation currently requests `0..max_frames`
inclusive from GDB. A request for `1` can therefore return `2` frames.

### Implementation plan

1. Decide the contract boundary precisely.
   Recommended contract:
   - `max_frames` means an upper bound on returned frame count.

2. Adjust the GDB request accordingly.
   Convert the user-facing count to the correct inclusive end index before
   building `-stack-list-frames`.

3. Verify edge cases.
   `max_frames=1` is the key regression case; default handling should still
   return the prior default-sized window.

### Suggested code changes

- fix frame-range calculation in
  [session/inspection.py](/home/chi/ddev/gdb-mcp/src/gdb_mcp/session/inspection.py);
- keep the schema and docs as-is if the implementation is corrected to match
  the current public wording.

### Acceptance criteria

- `max_frames=1` returns at most one frame;
- larger limits return no more than the requested number of frames;
- tests cover both the boundary case and a representative multi-frame case.

## Workstream 6: Test Coverage And Documentation

### Problem

The current suite passes, but it does not pin the reproduced failures. The
docs also describe selector semantics and status behavior too loosely to catch
drift.

### Test plan

Add or update tests for:

- dead-GDB status after forced child-process exit;
- startup with a missing program path;
- successful target loading via `file` or `core-file` init commands;
- `get_backtrace(thread_id=...)` preserving original selection;
- `get_variables(frame=...)` preserving original frame;
- breakpoint locations containing spaces;
- `max_frames=1` returning one frame at most.

Use both unit tests and integration tests:

- unit tests for state transitions and command construction;
- integration tests for real GDB behavior and path-handling edge cases.

### Documentation plan

Update:

- [TOOLS.md](/home/chi/ddev/gdb-mcp/TOOLS.md);
- [README.md](/home/chi/ddev/gdb-mcp/README.md).

Document:

- what `gdb_get_status` means after transport death;
- when `target_loaded` is true versus false;
- that `gdb_select_thread` and `gdb_select_frame` are the explicit tools that
  change debugger context;
- that `max_frames` is a true upper bound on returned frames.

## Suggested Execution Sequence

1. Add failing regression tests for process-death status and missing-target
   startup.
2. Implement session liveness cleanup and truthful status transitions.
3. Add failing tests for inspection side effects.
4. Implement selection-preserving inspection behavior.
5. Add failing tests for breakpoint path quoting and `max_frames=1`.
6. Implement MI quoting and frame-limit correction.
7. Update docs to match final behavior.
8. Run the full suite plus targeted integration coverage.

## Verification Checklist

Before merging, verify:

- `uv run pytest -q` passes;
- targeted integration tests cover all five audit findings;
- `uv run mypy src` still passes;
- `git diff --check` is clean;
- the docs and tests agree on the final contract wording.
