# Shared Contract Source Design

**Date:** 2026-04-08

**Status:** Draft for review

**Owner:** Codex

## Goal

Reduce duplicated public contract definitions by introducing one shared source for MCP tool names and public enum-like value sets, while preserving the existing MCP schemas, CLI flags, tool names, and runtime behavior.

## Context

The client and MCP schema layers currently repeat the same public strings in several places:

- `src/gdb_mcp/client/inputs.py` defines action and value `Literal[...]` aliases.
- `src/gdb_mcp/client/specs.py` repeats many of the same values as `argparse` `choices=[...]`.
- `src/gdb_mcp/mcp/schemas.py` repeats those values again inside Pydantic `Literal[...]` fields and local tool-name allowlists.

This duplication is manageable only because the test suite continuously catches drift. The project already has sync checks like:

- `tests/mcp/test_client_cli.py::test_client_tool_specs_cover_public_tool_inventory`
- `tests/mcp/test_client_cli.py::test_batch_step_tool_models_match_server_workflow_allowlist`

Those tests are useful, but they also demonstrate that the repo still pays an ongoing maintenance tax for repeated public contract values. The next cleanup should remove the duplication rather than only test around it.

## Problem Statement

The current structure has three concrete costs:

1. Public value changes require touching multiple files with no single canonical owner.
2. `client/specs.py` carries avoidable hardcoded parser choices even though those values are part of the public contract.
3. `mcp/schemas.py` owns both structural validation and repeated contract strings, making the schema layer larger and noisier than necessary.

The goal of this cleanup is not to auto-generate the entire schema tree. It is to move repeated public values into one shared contract source and have the client and schema layers consume that source.

## Requirements

### Functional Requirements

- Preserve the current public MCP wire contract.
- Preserve the current 17-tool public inventory.
- Preserve all existing CLI flags and subcommand names.
- Preserve the current schema validation behavior for public action names and shared enum-like values.
- Preserve the current batch-step tool allowlist behavior.

### Internal Design Requirements

- Introduce one canonical module for shared public contract values.
- Remove duplicated hardcoded `choices=[...]` lists in `src/gdb_mcp/client/specs.py` where they overlap with shared public values.
- Remove duplicated public value aliases in `src/gdb_mcp/client/inputs.py` where the shared contract source can be imported instead.
- Replace local tool-name and public-value duplication in `src/gdb_mcp/mcp/schemas.py` with imports from the shared contract source where practical.
- Keep the Pydantic request model structure hand-written and explicit.

### Process Requirements

- Keep the change staged and reviewable.
- Prefer characterization coverage and sync checks over new behavior.
- Finish with full repo verification: `ruff`, `mypy`, `pytest`, and `git diff --check`.

## Non-Goals

- Generating Pydantic models from metadata
- Replacing the current schema class layout
- Reworking handler dispatch logic
- Moving tool descriptions out of `src/gdb_mcp/mcp/schemas.py`
- Changing any public request/response shape

## Proposed Architecture

### New Shared Contract Module

Add a new module at:

- `src/gdb_mcp/contracts.py`

This module becomes the canonical owner of shared public contract values.

It should define:

- public tool names
- batch-step tool names
- domain action name sets
- shared enum-like value sets:
  - breakpoint kinds
  - breakpoint access modes
  - breakpoint events
  - location kinds
  - register value formats
  - disassembly modes
  - inferior follow-fork modes
  - execution wait modes

The module should expose both:

- runtime tuples for parser/schema reuse
- typing aliases for internal typed inputs

Example shape:

```python
SESSION_QUERY_ACTIONS = ("list", "status")
SessionQueryAction = Literal["list", "status"]
```

For tuple-backed `Literal[...]` aliases, use explicit `Literal[...]` definitions rather than dynamic construction so the resulting types stay simple and mypy-friendly.

### Client Layer Consumption

#### `src/gdb_mcp/client/inputs.py`

This file should stop owning independent copies of shared public value aliases. Instead, it should import the shared typing aliases from `src/gdb_mcp/contracts.py`.

Its responsibility stays the same:

- define typed CLI input dataclasses
- reuse shared action/value aliases from the canonical contract module

#### `src/gdb_mcp/client/specs.py`

This file should stop hardcoding shared parser choice lists like:

- action names
- location kind choices
- access mode choices
- wait mode choices
- register/disassembly mode choices

Instead, it should import runtime tuples from `src/gdb_mcp/contracts.py` and feed them into `argparse`.

This reduces duplication while keeping the parser assembly explicit.

### Schema Layer Consumption

`src/gdb_mcp/mcp/schemas.py` should import shared public values from `src/gdb_mcp/contracts.py` where the value duplication is purely contractual.

This applies to:

- batch-step tool-name tuples / aliases
- shared enum-like fields such as access modes, location kinds, register formats, disassembly modes, follow-fork modes, and repeated action-name sets

However, the Pydantic model structure should remain hand-written:

- request models stay explicit
- discriminated unions stay explicit
- validation methods stay local to the schema models

This preserves readability and avoids a riskier schema-generation refactor.

## Migration Plan

### Stage 1: Introduce Shared Contracts And Move The Lowest-Risk Values

Create `src/gdb_mcp/contracts.py` and move:

- tool-name tuples
- batch-step tool names
- common enum-like value sets used by both client and schema layers

Then update:

- `src/gdb_mcp/client/specs.py`
- `src/gdb_mcp/client/inputs.py`
- `src/gdb_mcp/mcp/schemas.py`

to consume the shared values.

This stage should avoid changing the overall model layout or CLI orchestration.

### Stage 2: Add Focused Sync Tests

Add a small focused test module, likely under `tests/mcp/`, that checks the shared contract source stays aligned with:

- `CLIENT_TOOL_SPECS`
- `BATCH_STEP_TOOL_MODELS`
- selected schema-backed public value sets

This is not a replacement for existing tests. It is a new, lower-noise place to assert the canonical relationship directly.

### Stage 3: Run Full Verification

Run:

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
git diff --check
```

If this lands cleanly, later cleanup can optionally move descriptions or other metadata into a shared source. That is explicitly out of scope for this slice.

## Alternatives Considered

### Option 1: Single Shared Contract Module

Recommendation.

Pros:

- lowest-risk deduplication
- one canonical owner for public values
- minimal churn in existing schemas
- compatible with current tests and typing

Cons:

- does not eliminate all duplication
- keeps descriptions and structural schema declarations in separate places

### Option 2: Domain-Split Contract Package

Example: `contracts/session.py`, `contracts/breakpoint.py`, `contracts/inspect.py`.

Pros:

- cleaner long-term boundaries
- easier future scaling if many more public values are added

Cons:

- more files and indirection for a relatively small cleanup
- higher migration overhead

### Option 3: Declarative Registry That Generates CLI And Schemas

Pros:

- maximum deduplication
- one metadata source could theoretically drive more of the system

Cons:

- much riskier
- more difficult to review
- easy to change generated schema behavior accidentally
- out of proportion to the current cleanup goal

## Recommendation

Use one shared contract module at `src/gdb_mcp/contracts.py` and limit this slice to deduplicating public values and tool-name inventory.

That gives the repo one canonical contract source without turning the cleanup into a schema-generation project. It directly addresses the remaining duplication noted in the cleanup review while preserving the explicit Pydantic model layout and current runtime structure.

## Testing Strategy

Use existing behavior coverage plus focused sync assertions.

Primary checks:

- existing CLI tests still pass
- existing schema tests still pass
- shared contract tests assert alignment with tool inventory and batch-step allowlists
- full repository verification passes

This is a characterization-preserving refactor, not a behavior addition.

## Risks And Mitigations

### Risk: Pydantic schema generation changes unexpectedly

Mitigation:

- keep the schema model tree explicit
- limit shared imports to public values and tool-name sets
- rely on existing schema tests plus full-suite verification

### Risk: Client parser choices drift from shared values during migration

Mitigation:

- move parser `choices=` lists onto shared runtime tuples in the same change
- keep inventory alignment tests in place

### Risk: Typing becomes more obscure than the duplication it replaces

Mitigation:

- prefer explicit `Literal[...]` aliases over dynamic type construction
- keep `contracts.py` readable and flat

## Success Criteria

This cleanup is successful when:

- one shared module owns the repeated public contract values
- `client/inputs.py`, `client/specs.py`, and `mcp/schemas.py` consume that shared source
- no public behavior changes
- the full verification suite remains green
