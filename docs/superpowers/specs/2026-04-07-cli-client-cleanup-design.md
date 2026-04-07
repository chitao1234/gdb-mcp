# CLI Client Cleanup Design

**Date:** 2026-04-07

**Status:** Draft for review

**Owner:** Codex

## Goal

Refactor the internal `gdb-mcp-client` implementation to replace dynamic `argparse.Namespace`-driven payload construction with typed internal command inputs, while preserving the existing MCP interface, CLI flags, tool names, payload shapes, transport behavior, and human/`--json` output.

## Context

The newly added CLI client works and is fully verified, but the implementation has accumulated too much dynamic namespace inspection and duck typing, especially in `src/gdb_mcp/client/specs.py`. The dominant pattern today is:

1. `argparse` writes flags onto a dynamic `Namespace`
2. payload builders inspect the namespace with `hasattr()` / `getattr()`
3. builders infer intent from missing attributes, implicit defaults, and ad hoc branching
4. the final dict is validated only at the MCP schema boundary

This creates several problems:

- internal invariants are hard to see and reason about
- optionality is represented by field presence instead of explicit types
- payload builders mix parsing, normalization, validation, and business rules
- tests must often exercise large CLI flows to catch internal regressions
- `specs.py` has become too large and too branch-heavy to serve as a pattern for future work

The user explicitly wants this cleanup to be **pattern-setting**, not a one-off local tidy-up, and explicitly approved a design where internal code is reshaped aggressively as long as the MCP interface is preserved.

## Requirements

### Functional Requirements

- Preserve the current MCP wire interface exactly:
  - same 17 public tool subcommands
  - same CLI flags
  - same validated MCP request payload shapes
  - same streamable HTTP transport behavior
  - same human-oriented output and `--json` output
- Preserve the current workflow/setup-step semantics for grouped step flags.
- Preserve all current test-observed behavior unless a current behavior is clearly an internal bug uncovered during refactor.

### Internal Design Requirements

- Remove `argparse.Namespace` as the primary internal data model for payload construction.
- Replace dynamic field-presence checks with typed internal command inputs.
- Make payload builders operate on typed objects only.
- Reduce `hasattr()` / `getattr()` usage in `src/gdb_mcp/client/` to a small, justified boundary layer.
- Break the current monolithic logic into smaller units with clearer responsibilities.
- Establish a reusable internal pattern that can be applied to similar code elsewhere in the repo.

### Process Requirements

- Deliver the cleanup in staged slices rather than one giant refactor.
- Keep the current full test suite green throughout.
- Use the existing subagent-driven workflow and per-task review gates.

## Non-Goals

- Changing any public MCP schema in `src/gdb_mcp/mcp/schemas.py`
- Changing any public CLI flags or tool names
- Adding a second client transport
- Reworking server runtime or handler behavior unrelated to the client cleanup
- Performing unrelated repo-wide refactors with weak payoff

## Proposed Architecture

The refactor introduces a typed internal layer between `argparse` and MCP payload building.

### Current Shape

Today the dominant flow is:

`argv -> argparse.Namespace -> dynamic builder logic -> MCP payload dict -> schema validation`

### Target Shape

The new flow becomes:

`argv -> argparse.Namespace -> typed internal input -> pure payload builder -> schema validation`

This preserves the public surface while making the internal boundary explicit and type-driven.

### Layer Responsibilities

#### `src/gdb_mcp/client/cli.py`

- top-level parser creation
- top-level argument parsing
- dispatch to one tool spec
- runtime invocation
- output rendering

`cli.py` remains the CLI edge, but it should stop depending on dynamic builder internals.

#### `src/gdb_mcp/client/parsers.py`

- low-level `argparse` actions and scalar parsers
- grouped-event collection
- dotted-assignment utilities
- a small set of generic parser helpers

This module should remain parser-focused, not become a second business-logic layer.

#### New typed input modules

Introduce typed internal inputs under `src/gdb_mcp/client/`, likely in one of these shapes:

- `src/gdb_mcp/client/inputs.py`
- or `src/gdb_mcp/client/inputs/` split by family when needed

These types represent **parsed CLI intent**, not transport payloads. They should encode explicit optionality with `None`, booleans, and typed collections instead of missing namespace attributes.

Examples of likely reusable value objects:

- `InvocationOptions`
- `ContextSelectorInput`
- `ExecutionWaitInput`
- `LocationInput`
- `WorkflowStepInput`
- `CaptureOptionsInput`

Examples of likely per-tool-family inputs:

- `SessionStartInput`
- `SessionQueryInput`
- `SessionManageInput`
- `InferiorQueryInput`
- `InferiorManageInput`
- `ExecutionManageInput`
- `BreakpointQueryInput`
- `BreakpointManageInput`
- `ContextQueryInput`
- `ContextManageInput`
- `InspectQueryInput`
- `WorkflowBatchInput`
- `RunUntilFailureInput`

#### New builder modules

Move payload construction out of `specs.py` into pure builder functions, likely grouped by family:

- `src/gdb_mcp/client/builders/session.py`
- `src/gdb_mcp/client/builders/inferior.py`
- `src/gdb_mcp/client/builders/execution.py`
- `src/gdb_mcp/client/builders/context.py`
- `src/gdb_mcp/client/builders/breakpoint.py`
- `src/gdb_mcp/client/builders/inspect.py`
- `src/gdb_mcp/client/builders/workflow.py`

Each builder should accept one typed input object and return exactly one payload dict that is then validated by the shared MCP schema model.

#### `src/gdb_mcp/client/specs.py`

Shrink `specs.py` into orchestration:

- static spec registry
- parser wiring for each tool
- conversion hook from `Namespace -> TypedInput`
- builder hook from `TypedInput -> payload`
- renderer selection

After refactor, `specs.py` should no longer be the place where most of the business logic lives.

## Typed Input Design

The key rule is:

**Payload builders never inspect `argparse.Namespace` directly.**

### Why dataclasses first

Use internal dataclasses for command inputs and reusable value types unless a specific case benefits from an internal Pydantic model.

Reasons:

- lightweight and explicit
- easy to construct from parser output
- easy to unit test
- avoids duplicating validation frameworks where MCP schemas already perform the final contract validation

### Optionality model

Optional user input should be represented explicitly:

- missing scalar flag -> `None`
- missing boolean override -> `None` when the distinction matters
- repeated values -> `list[str]` / `list[int]`
- grouped step arguments -> structured typed step objects, not partial dicts

This is better than current behavior where absence is represented by missing namespace attributes and interpreted with `hasattr()`.

### Action families

Action-family tools should be represented by typed variants rather than large dynamic builders that inspect both `action` and field presence.

Two acceptable shapes:

1. one top-level input dataclass with action-specific optional fields
2. discriminated internal variants per action

Recommendation:

- prefer top-level family input types with action-specific nested dataclasses when that keeps the code compact
- use discriminated variants for more complex families like breakpoint, inspect, workflow, and campaign

### Workflow and campaign special handling

`gdb_workflow_batch` and `gdb_run_until_failure` are the most complex client paths and should become the reference example for why this cleanup matters.

The refactor should keep the current grouped-event capture at the parser layer, but convert events into typed internal structures before payload building:

- raw `--step*` / `--setup-step*` event sequence
- parsed `WorkflowStepInput` objects
- step validation against the allowed tool models
- final payload dict generation

This preserves current semantics while removing dict-first assembly and dynamic post hoc correction.

## Migration Strategy

The cleanup should land in staged slices.

### Stage 1: Establish the pattern with shared primitives and one reference family

Create:

- shared typed input/value objects
- one family-specific builder module
- one `Namespace -> TypedInput` conversion path

Recommended reference family:

- session or execution

Reason:

- broad enough to demonstrate the pattern
- simpler than inspect/workflow
- already heavily tested

This stage defines the house style for the rest of the migration.

### Stage 2: Migrate the core action families

Move the simpler action families onto the new pattern:

- session
- inferior
- execution
- context

This stage should materially reduce `specs.py` size and make the pattern routine before tackling the most complex families.

### Stage 3: Migrate the complex families

Move the remaining high-branch logic:

- breakpoint
- inspect
- workflow
- run-until-failure

This stage likely yields the largest maintainability win because it eliminates the most dynamic logic and the most nested `hasattr()` / `getattr()` use.

### Stage 4: Cleanup and consolidation

After all families are migrated:

- remove obsolete namespace helper patterns
- delete dead builder helpers
- simplify `specs.py`
- ensure helper modules have one clear responsibility each

### Stage 5: Pattern-setting follow-through

Identify adjacent dynamic/duck-typed code elsewhere in the repo that would benefit from the same typed-boundary pattern.

This stage should stay targeted:

- apply the new style where payoff is high
- avoid broad unrelated cleanup
- favor hotspots near the client or other parser/builder boundaries

## File Structure Proposal

The likely end state is:

- `src/gdb_mcp/client/cli.py`
- `src/gdb_mcp/client/parsers.py`
- `src/gdb_mcp/client/renderers.py`
- `src/gdb_mcp/client/runtime.py`
- `src/gdb_mcp/client/specs.py`
- `src/gdb_mcp/client/inputs.py` or `src/gdb_mcp/client/inputs/*.py`
- `src/gdb_mcp/client/builders/__init__.py`
- `src/gdb_mcp/client/builders/session.py`
- `src/gdb_mcp/client/builders/inferior.py`
- `src/gdb_mcp/client/builders/execution.py`
- `src/gdb_mcp/client/builders/context.py`
- `src/gdb_mcp/client/builders/breakpoint.py`
- `src/gdb_mcp/client/builders/inspect.py`
- `src/gdb_mcp/client/builders/workflow.py`

The exact split should follow code size and cohesion, but the core rule is:

**`specs.py` orchestrates; builders build; inputs type the boundary.**

## Testing Strategy

Keep the current CLI end-to-end tests as the safety net, but add more focused tests around the new internal boundaries.

### Tests to keep

- `tests/mcp/test_client_cli.py`
- `tests/mcp/test_client_runtime.py`
- `tests/integration/test_client_streamable_http.py`

### Tests to add or reshape

Add focused tests for:

- `Namespace -> TypedInput` conversion
- `TypedInput -> payload` builders
- grouped workflow/setup-step normalization
- action-variant conversion behavior for inspect and breakpoint families

Possible structure:

- keep end-to-end CLI assertions in `tests/mcp/test_client_cli.py`
- add focused internal tests such as:
  - `tests/mcp/test_client_inputs.py`
  - `tests/mcp/test_client_builders.py`
  - or split by family if needed

### Verification gates

At minimum, each stage should run the narrowest relevant checks plus the final repo gates:

- `uv run ruff check src tests`
- `uv run mypy src`
- `uv run pytest -q`
- `git diff --check`

## Risks and Mitigations

### Risk: behavior drift during migration

Mitigation:

- preserve end-to-end CLI tests as the compatibility safety net
- migrate by family rather than rewriting the whole layer at once
- keep schema validation at the same boundary

### Risk: over-engineering the typed layer

Mitigation:

- keep inputs narrowly scoped to CLI intent
- avoid mirroring every MCP schema one-for-one if a smaller internal type is sufficient
- do not introduce extra abstraction layers without repeated use

### Risk: two patterns coexist too long

Mitigation:

- define the new pattern clearly in the first migration stage
- retire old builder helpers as soon as a family is fully moved
- keep `specs.py` orchestration-only as the target

### Risk: workflow/campaign logic becomes harder before it gets easier

Mitigation:

- refactor shared typed step modeling before touching the full campaign builder
- keep step parsing and step payload building separate
- add focused tests around list coercion, nested arguments, and validation errors

## Success Criteria

The refactor is successful when:

- `src/gdb_mcp/client/specs.py` is materially smaller and less branch-heavy
- `hasattr()` / `getattr()` use in `src/gdb_mcp/client/` is limited to parser-edge or compatibility-edge code
- payload builders only accept typed inputs
- grouped workflow/setup-step logic is represented with typed internal objects instead of partially assembled dicts
- the full CLI behavior remains unchanged at the MCP boundary
- the full verification suite remains green
- the resulting structure is clear enough to serve as the default pattern for future CLI and parser-boundary work in the repo

## Recommended Next Step

Write a staged implementation plan that:

1. establishes the typed input + builder pattern with one family
2. migrates remaining tool families in logical slices
3. removes obsolete dynamic namespace access patterns
4. runs full verification and review after each logical unit

