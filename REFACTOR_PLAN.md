# GDB MCP Refactor Plan

This document supersedes the previous test-refactor status note. That work is
done. The current priority is to fix the audited defects and remove the design
patterns that made them likely.

The plan below assumes internal breaking changes are acceptable when they
improve the long-term architecture. The external MCP surface should remain
stable unless a specific contract change is deliberately approved.

## Goals

- Fix the audited bugs without layering more string-based patches on top.
- Centralize GDB/MI command construction so escaping and framing are correct by
  construction.
- Replace implicit mixin contracts with explicit composition and typed
  dependencies.
- Introduce a single authoritative session runtime model instead of scattered
  booleans and controller checks.
- Make the domain layer meaningfully typed instead of passing raw GDB payloads
  through `Any`-heavy wrappers.
- Tighten MCP validation so invalid requests fail at the boundary, not deep in
  GDB.
- End with a codebase that passes `pytest -q` and `mypy src` cleanly.

## What Is Wrong Today

### Isolated defects

- CLI passthrough commands are not safely escaped for MI framing.
- `gdb_evaluate_expression` interpolates raw expressions directly into MI
  strings.
- transport timeouts are activity-based instead of wall-clock bounded.
- `run(args)` flattens argv into a single unescaped string.
- environment setup failures during startup can still produce a successful
  session.
- MCP schemas accept invalid negative IDs and indices.
- `remove_session` can drop a live session without stopping its GDB process.
- `target_loaded` can be set by unrelated commands that merely contain the word
  `file`.

### Structural issues behind those defects

- command construction is scattered across session methods instead of being
  owned by transport;
- session state is split across `controller`, `is_running`, `target_loaded`,
  `state`, and config, with no single source of truth;
- mixins depend on undeclared attributes and helpers, which makes the design
  hard to reason about and blocks useful static analysis;
- the typed domain boundary is shallow and mostly carries raw GDB dictionaries;
- MCP tool routing duplicates validation logic and relies heavily on `Any`;
- `server.py` still creates global runtime state at import time.

## Refactor Principles

1. One owner per responsibility.
   Command encoding belongs in transport. Session lifecycle belongs in session.
   Request validation belongs in MCP.

2. No raw MI string interpolation outside the command builder layer.
   If a method needs to express a debugger action, it should build a typed
   command object or call a transport helper designed for that command shape.

3. One authoritative session runtime object.
   All observable state should derive from one mutable runtime model rather than
   manually synchronized booleans.

4. External stability, internal freedom.
   Keep MCP tool names and response shape stable where practical, but do not
   preserve internal structure just to avoid moving code.

5. Type errors are design feedback.
   The current `mypy` failures are not cleanup noise; they indicate implicit
   contracts that should be made explicit.

## Target Architecture

The recommended end state is explicit composition instead of mixins.

### 1. Transport owns protocol details

Suggested modules:

- `transport/commands.py`
- `transport/encoding.py`
- `transport/client.py`
- `transport/parsing.py`
- `transport/models.py`

Responsibilities:

- `commands.py` defines typed command specifications for the operations this
  codebase uses, for example `CliCommand`, `EvaluateExpressionCommand`,
  `SetBreakpointCommand`, `ExecArgumentsCommand`, `ThreadSelectCommand`.
- `encoding.py` converts those command objects into legal MI strings and owns
  all escaping rules, including quotes, backslashes, control characters, and
  argv quoting semantics.
- `client.py` sends already-encoded commands, enforces absolute deadlines, and
  collects raw response records plus async notifications.
- `parsing.py` converts raw MI records into typed or strongly shaped response
  helpers for the operations the session layer cares about.

Rule:

- no module outside `transport/` should hand-assemble MI strings except for
  deliberate raw passthrough APIs that are explicitly marked and validated.

### 2. Session owns orchestration through explicit collaborators

Suggested modules:

- `session/runtime.py`
- `session/lifecycle_service.py`
- `session/execution_service.py`
- `session/inspection_service.py`
- `session/breakpoint_service.py`
- `session/service.py`

Responsibilities:

- `SessionRuntime` becomes the authoritative mutable state object.
- lifecycle, execution, inspection, and breakpoint operations become concrete
  classes with explicit constructor dependencies.
- `SessionService` remains the façade used by the registry and MCP layer, but
  it delegates to collaborators instead of inheriting behavior through mixins.

This is the main structural change. It is worth doing because the current mixin
design hides required fields and helpers, which is exactly why the code composes
poorly with typing and static analysis.

### 3. Domain models become actual contracts

The domain layer should define tool-facing structures, not generic wrappers
around raw payloads.

Expected changes:

- replace `Any` where a stable shape is known;
- introduce typed payload models for frames, threads, breakpoints, registers,
  variables, and expression results;
- keep a raw payload escape hatch only where GDB output is genuinely open-ended;
- keep `OperationSuccess` and `OperationError`, but tighten how result payloads
  are represented and serialized.

### 4. MCP becomes a thin boundary over typed services

The MCP layer should:

- validate and normalize external requests;
- resolve a session;
- invoke one typed session façade method;
- serialize the resulting typed payload.

It should not need to re-validate the same model inside each handler, and it
should not rely on `BaseModel` plus `Any` to bridge type gaps.

### 5. Server entrypoint becomes lazy and boring

`server.py` should expose a default runtime factory and a thin launch path
without owning import-time singleton state beyond the CLI entrypoint case.

## Workstreams

## Workstream 1: Transport Hardening and Command Model

This workstream closes the highest-risk bugs and establishes the most important
architectural boundary.

### Deliverables

- add typed command objects for all currently supported internal operations;
- add centralized MI encoding helpers;
- reject or escape control characters consistently;
- implement wall-clock command deadlines in `MiClient`;
- separate timeout accounting from unrelated async traffic;
- migrate session call sites away from hand-built command strings.

### Bugs closed by this workstream

- CLI newline and carriage-return framing bug;
- expression interpolation bug;
- `run(args)` argv flattening bug;
- command-shape inconsistencies across breakpoints, env setup, and function
  calls.

### Acceptance criteria

- no raw MI string assembly remains outside the allowed transport builder
  modules, except for one explicit raw passthrough path if intentionally kept;
- tests cover quotes, backslashes, newlines, carriage returns, and multi-arg
  argv cases;
- transport timeouts are bounded by total elapsed wall-clock time.

## Workstream 2: Session Runtime and Lifecycle Redesign

This workstream fixes the scattered-state problem.

### Deliverables

- introduce a `SessionRuntime` model that owns:
  - lifecycle state;
  - controller/process handle;
  - launch config;
  - target load status;
  - last known health/failure metadata;
  - optionally current thread/frame context if the code chooses to track it;
- make `get_status()` derive from runtime state instead of manually maintained
  booleans;
- centralize startup, failure, and cleanup transitions;
- define clear startup policy for init command and env command failures.

### Policy decisions to make explicit

- startup should fail if an environment command fails;
- non-fatal init command failures should either:
  - fail startup, or
  - mark the session as degraded and report warnings explicitly.

The plan recommendation is:

- fail startup on env command failure;
- fail startup on any init command failure unless there is a documented reason
  to allow partial startup.

### Bugs closed by this workstream

- startup success after failed env setup;
- incorrect `target_loaded` transitions;
- state drift between `controller`, `is_running`, `target_loaded`, and `state`.

### Acceptance criteria

- one runtime object is the source of truth for externally visible session
  state;
- startup and shutdown paths are represented as explicit transitions;
- tests cover env failure, init failure, fatal transport failure, and cleanup.

## Workstream 3: Replace Mixins with Explicit Composition

This is the main design cleanup step.

### Deliverables

- remove `SessionLifecycleMixin`, `SessionExecutionMixin`,
  `SessionBreakpointMixin`, and `SessionInspectionMixin`;
- introduce concrete collaborator classes or modules with explicit
  dependencies;
- keep `SessionService` as a façade if a single public session object remains
  desirable.

### Expected benefits

- clearer ownership of dependencies;
- easier unit testing without patching private helpers;
- meaningful static analysis;
- fewer implicit invariants hidden across multiple files.

### Acceptance criteria

- `mypy` no longer reports missing attributes caused by mixin assumptions;
- tests primarily exercise public collaborators rather than monkeypatching
  hidden helpers;
- the public session API is easier to trace from one entrypoint to one owner.

## Workstream 4: Type-System Repair

The current type failures are broad enough that they should be treated as a
refactor track, not cleanup at the end.

### Deliverables

- define a minimal protocol for the controller object used by transport/session;
- narrow `Any` in domain and transport models where response shapes are known;
- replace handler patterns that force repeated Pydantic round-trips;
- make serialization helpers type-safe;
- get `mypy src` to zero errors.

### Acceptance criteria

- `mypy src` passes with no errors;
- the session and MCP layers do not rely on undeclared attributes;
- payload serialization does not require broad `Any` escape hatches for normal
  control flow.

## Workstream 5: MCP Boundary Cleanup

### Deliverables

- add bounds to `session_id`, `thread_id`, `frame_number`, `breakpoint number`,
  and `max_frames`;
- decide whether empty strings and control characters should be rejected at the
  schema level for relevant user inputs;
- simplify handler dispatch so each tool is validated once;
- make invalid input errors deterministic and user-facing.

### Bugs closed by this workstream

- negative and nonsensical IDs reaching GDB;
- redundant validation logic in handlers;
- type noise caused by `BaseModel` plus repeated `model_validate()` calls.

### Acceptance criteria

- schema tests cover invalid negative values and edge cases;
- handler code path validates once, invokes once, serializes once.

## Workstream 6: Registry and Runtime Ownership

### Deliverables

- replace ambiguous `remove_session()` semantics with explicit lifecycle
  methods, for example:
  - `close_session(session_id)` to stop and remove;
  - `discard_session(session_id)` only for already-stopped sessions, if needed;
- make MCP stop operations use the stop-and-remove path;
- ensure registry shutdown semantics are explicit and consistent.

### Bugs closed by this workstream

- silent process leak when a live session is removed without stop.

### Acceptance criteria

- there is no public path that drops a live session without an explicit choice;
- tests verify stop-and-remove behavior, not just map deletion.

## Workstream 7: Entry Point and Compatibility Cleanup

### Deliverables

- make `server.py` a thin launcher around a default runtime factory;
- keep compatibility exports only where they are intentionally part of the
  public package surface;
- remove stale comments and completed-refactor assumptions from docs.

### Acceptance criteria

- integration tests run primarily through constructed runtimes, not global
  module state;
- import-time global state is minimized and well-contained.

## Suggested Phase Order

## Phase 0: Guardrails First

- add regression tests for every audited bug before large movement;
- add a small set of characterization tests for the current MCP response shape;
- decide which internal APIs may change freely and which must remain stable.

Exit criteria:

- the failing scenarios are reproducible in tests;
- the external MCP contract is pinned by tests.

## Phase 1: Transport Command Builder

- introduce typed command specs and centralized encoding;
- migrate `execute_command`, `call_function`, `evaluate_expression`, breakpoint
  creation, env setup, and exec-arguments to the new layer;
- add wall-clock deadlines to `MiClient`.

Exit criteria:

- the audited escaping and timeout defects are fixed;
- no new command paths are added through ad hoc string concatenation.

## Phase 2: Session Runtime Object

- add `SessionRuntime`;
- route lifecycle transitions through it;
- remove duplicated state flags or derive them from runtime;
- fix init/env failure semantics.

Exit criteria:

- session state is coherent and testable from one place.

## Phase 3: Replace Mixins

- convert session capabilities into explicit collaborators;
- keep `SessionService` as façade during migration;
- update tests to target public collaborators instead of private patch points.

Exit criteria:

- mixins are gone;
- `mypy` attribute errors from hidden mixin dependencies are gone.

## Phase 4: MCP and Type Cleanup

- tighten schemas;
- simplify handler dispatch;
- replace weakly typed payloads where practical;
- drive `mypy src` to green.

Exit criteria:

- `mypy src` passes;
- MCP handlers are shorter and more direct.

## Phase 5: Registry and Entrypoint Cleanup

- rename or redesign session removal semantics;
- reduce import-time globals in `server.py`;
- update docs after architecture settles.

Exit criteria:

- runtime ownership is explicit end to end.

## Test Strategy

### Add targeted tests for audited defects

- CLI command with newline and carriage return;
- expression containing quotes, backslashes, and newline;
- transport timeout under continuous unrelated async notifications;
- `run(args)` with spaces, quotes, and backslashes;
- startup failure when env command fails;
- invalid negative IDs and frame indices rejected at schema boundary;
- removing a live session must stop or reject.

### Add structural regression tests

- session status derives from runtime transitions;
- command builders produce one canonical encoding path per command family;
- no direct MI string interpolation outside transport command modules;
- handler dispatch validates arguments exactly once.

### Tooling gates

- `pytest -q`
- `mypy src`

Optional but recommended after the architecture stabilizes:

- add a lightweight lint or grep-based test that rejects new direct MI string
  interpolation outside approved transport files.

## Compatibility Guidance

Internal breakage is acceptable in:

- session helper function signatures;
- registry method names and semantics;
- module layout under `session/` and `transport/`;
- how tests construct fakes and fixtures.

External compatibility should be preserved for:

- MCP tool names;
- top-level tool purpose;
- normal success/error payload shape unless explicitly versioned.

If an external payload change is unavoidable, it should be done once and
documented as an intentional contract revision rather than leaking out of the
refactor accidentally.

## Definition of Done

This refactor is complete when all of the following are true:

- audited bugs are covered by tests and fixed;
- transport owns command encoding;
- session state has one authoritative runtime model;
- mixins have been replaced by explicit composition;
- registry lifecycle ownership is explicit;
- MCP validation rejects invalid inputs at the boundary;
- `pytest -q` passes;
- `mypy src` passes;
- `REFACTOR_PLAN.md` can be retired because the architecture matches this
  document rather than merely pointing toward it.
