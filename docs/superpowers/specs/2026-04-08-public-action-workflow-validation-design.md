# Public Action And Workflow Validation Cleanup Design

## Goal

Centralize the remaining duplicated public action discriminator/result strings and the shared workflow-step validation rules at the public `client/` and `mcp/` boundary, without changing tool names, request shapes, response shapes, CLI flags, or runtime behavior.

## Current State

The earlier shared-contract cleanup introduced `src/gdb_mcp/contracts.py` as the canonical source for public tool names and many public enum-like value sets. That removed the highest-value duplication, but two public-boundary cleanup targets still remain:

1. Public action/kind discriminator strings are still duplicated in `src/gdb_mcp/mcp/schemas.py` and repeated again in `src/gdb_mcp/mcp/handlers.py` result wrapping.
2. Workflow-step admissibility rules are still implemented independently in `src/gdb_mcp/client/input_parsers.py` and `src/gdb_mcp/mcp/handlers.py`.

The repo already has tests that detect drift after the fact, but those tests do not remove the duplicated logic or vocabulary.

## Scope

This pass stays at the public contract boundary.

Included:

- `src/gdb_mcp/contracts.py`
- `src/gdb_mcp/mcp/schemas.py`
- `src/gdb_mcp/mcp/handlers.py`
- `src/gdb_mcp/client/input_parsers.py`
- `tests/mcp/test_contracts.py`
- relevant workflow validation tests in `tests/mcp/test_client_cli.py` and `tests/mcp/test_handlers.py`

Explicitly out of scope:

- refactoring internal `src/gdb_mcp/session/` and `src/gdb_mcp/domain/` literal aliases
- changing the public MCP inventory, action names, or response envelope format
- broad tool-registry redesign beyond what is necessary to remove the targeted duplication
- moving full Pydantic validation out of existing CLI or MCP call sites

## Design

### 1. Extend the shared contract module with canonical public action names

`src/gdb_mcp/contracts.py` will remain the single shared contract module and will gain canonical single-value aliases/constants for the public action discriminator values and related schema discriminator values that are still repeated inline today.

This includes:

- action names used by public action root models such as `list`, `status`, `current`, `create`, `threads`, `evaluate`, and similar values across the session, inferior, execution, breakpoint, context, and inspect tool families
- location discriminator values such as `current`, `function`, `address`, `address_range`, `file_line`, and `file_range`
- breakpoint-kind discriminator values such as `code`, `watch`, and `catch` where single-value discriminator reuse is still needed

The grouped tuples and grouped `Literal[...]` aliases already in the module remain in place. The new single-value aliases exist to remove the final inline public-boundary copies that cannot naturally share the grouped aliases.

### 2. Make MCP schema discriminators consume the shared public contract source

`src/gdb_mcp/mcp/schemas.py` will stop hardcoding standalone public discriminator literals for:

- action fields on public root-model branches
- `kind` fields on location and breakpoint discriminator branches where duplication still exists

The schema layer will continue to expose the same JSON Schema and accept the same request payloads. The change is only where the canonical literal values come from.

This keeps `contracts.py` as the public vocabulary source while preserving the current model layout and field descriptions in `schemas.py`.

### 3. Make handler result wrapping use validated public actions instead of restated strings

`src/gdb_mcp/mcp/handlers.py` currently repeats public action strings when calling `_wrap_action_result(...)`.

Where a validated action object already carries the canonical public action value, the handler should pass that validated action through instead of restating the string. That keeps the runtime result envelope aligned with the same contract source that drives the schemas.

This change does not alter response payload structure. Successful responses must still return:

```json
{
  "action": "<public action>",
  "result": { ... }
}
```

and error details must still preserve the current `action` field behavior.

### 4. Centralize workflow-step admissibility rules in one shared helper

The workflow/setup-step restrictions currently enforced in both `src/gdb_mcp/client/input_parsers.py` and `src/gdb_mcp/mcp/handlers.py` will move behind one shared pure helper exposed from the public contract/shared-boundary layer.

The shared helper will own only the rule decisions that both sides must agree on:

- step arguments must not include `session_id`
- `gdb_session_query(action=list)` is not valid inside workflow/setup steps
- `gdb_session_manage` is not valid inside workflow/setup steps
- `gdb_workflow_batch` and `gdb_run_until_failure` are not valid as nested workflow/setup steps

The helper will not absorb full model validation, payload coercion, or caller-specific exception/result creation.

Caller behavior stays split:

- the CLI path still raises `CliUsageError`
- the MCP server path still returns `OperationError`

The difference is that both callers will derive their behavior from one shared rule result instead of encoding the same rules twice.

### 5. Keep verification aligned with the public boundary

Verification should prove two things:

1. public action vocabularies are now single-sourced across contracts, schemas, and handler envelopes
2. workflow-step validation behavior is still unchanged while now being derived from shared rule logic

The cleanup does not require changes to docs such as `README.md` or `TOOLS.md` because the public interface remains unchanged.

## File Responsibilities After The Change

- `src/gdb_mcp/contracts.py`
  Owns the canonical public tool names, public action/kind/location vocabulary, and shared workflow-step admissibility metadata/helper logic.

- `src/gdb_mcp/mcp/schemas.py`
  Owns MCP request structure and field descriptions, but not the canonical source of public discriminator strings.

- `src/gdb_mcp/mcp/handlers.py`
  Owns request routing and runtime response assembly, but should derive action labels from validated request objects/shared rules instead of restating them.

- `src/gdb_mcp/client/input_parsers.py`
  Owns CLI parsing and caller-facing error reporting, but should use the shared workflow-step rule helper before model validation.

- `tests/mcp/test_contracts.py`
  Owns the explicit cross-layer drift checks for public contract synchronization.

## Error Handling

The cleanup preserves current caller-visible behavior:

- CLI workflow/setup-step misuse still exits through `CliUsageError` with the existing message intent.
- Server-side workflow/setup-step misuse still returns structured `OperationError` responses with existing `code` behavior.
- Unsupported action branches still continue to return the current deterministic validation errors.

Any new helper introduced for workflow-step admissibility should return structured decision data rather than directly raising or serializing errors, so both callers can map the same rule outcome into their own existing error path.

## Testing Strategy

Focused verification:

- strengthen or extend `tests/mcp/test_contracts.py` so it checks remaining public action discriminator/result string synchronization
- keep or extend CLI workflow-step validation tests in `tests/mcp/test_client_cli.py`
- keep or extend server workflow-step validation tests in `tests/mcp/test_handlers.py`

Repository validation before completion:

- `PYTHONPATH=src uv run ruff check src tests`
- `PYTHONPATH=src uv run mypy src`
- `PYTHONPATH=src uv run pytest -q`
- `git diff --check`

## Success Criteria

This cleanup is complete when all of the following are true:

1. Public action discriminator/result strings no longer have independent hardcoded copies in the MCP schema and handler layers where a shared contract source can be used instead.
2. Workflow-step admissibility rules are derived from one shared helper used by both CLI and MCP paths.
3. Existing public request and response behavior remains unchanged.
4. The full repository validation set passes in the worktree.
