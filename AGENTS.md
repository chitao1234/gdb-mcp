# AGENTS Guide

This file defines repository-specific guidance for agents and contributors working in this project.

## Scope

- Applies to the repository root and all subdirectories.
- If a deeper `AGENTS.md` exists in a subdirectory, the deeper file overrides this one for that subtree.

## Project Layout

- Python package code: `src/gdb_mcp/`
- MCP tool schemas and registration: `src/gdb_mcp/mcp/schemas.py`
- MCP tool handlers: `src/gdb_mcp/mcp/handlers.py`
- Session and GDB/MI orchestration: `src/gdb_mcp/session/`
- Domain models: `src/gdb_mcp/domain/`
- Transport layers: `src/gdb_mcp/transport/`
- Test suites: `tests/`
- API and usage docs: `README.md`, `TOOLS.md`, `examples/README.md`, `examples/USAGE_GUIDE.md`
- Skill definitions and playbooks: `skills/`, especially `skills/debug-with-gdb-mcp/`

## Development Workflow

- Keep changes focused and minimal per task.
- After each completed logical unit of work, create a commit with a clear message.
- Do not revert unrelated local changes you did not make.
- Prefer non-interactive git commands.

## Validation Before Commit

Run these checks for code changes (and run relevant subsets for docs-only changes):

- `ruff check src tests` or `uv run ruff check src tests`
- `mypy src` or `uv run mypy src`
- `pytest -q` or `uv run pytest -q`
- `git diff --check`

If a full run is too expensive for the change size, run the narrowest relevant tests and state what was not run.

## MCP Tool Change Checklist

When adding or changing MCP tools, keep schemas, runtime behavior, tests, and docs in sync in the same change set:

1. Update tool input/output schemas in `src/gdb_mcp/mcp/schemas.py`.
2. Update handler implementation in `src/gdb_mcp/mcp/handlers.py`.
3. Update session/domain code under `src/gdb_mcp/session/` or `src/gdb_mcp/domain/` if behavior requires it.
4. Add or update tests in `tests/mcp/`, `tests/session/`, and `tests/integration/` as needed.
5. Update user-facing docs in `TOOLS.md` and any relevant overview text in `README.md`.
6. Update workflow-facing docs and skills when examples or debugging playbooks change: `examples/USAGE_GUIDE.md`, `examples/README.md`, and `skills/debug-with-gdb-mcp/`.

## Coding Expectations

- Prefer explicit, narrow typing over broad `Any` where practical.
- Preserve backward compatibility unless the change explicitly documents a breaking behavior.
- Return structured, machine-readable outputs for tool APIs whenever feasible.
- Keep error messages actionable and deterministic for automation clients.

## Documentation Expectations

- Keep examples runnable against current APIs.
- When behavior changes, document exact parameter and response shapes.
- Use relative repository paths in documentation and internal guidance.
- Keep public examples and skill docs on v2 tool names; reserve historical names for dedicated migration sections only.
- When updating skill docs under `skills/`, keep them aligned with `README.md`, `TOOLS.md`, and `examples/USAGE_GUIDE.md`.
