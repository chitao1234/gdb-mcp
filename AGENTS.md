# AGENTS Guide

Applies to the whole repository; a deeper `AGENTS.md` overrides it for its subtree.

## What This Is

`gdb-mcp-server` exposes 18 MCP tools over GDB/MI. `gdb-mcp-client` is a CLI for
the same tools and can keep one server per project running in the background.

## Layout

- `src/gdb_mcp/contracts.py` — tool names, action literals, shared enums.
- `src/gdb_mcp/mcp/` — `schemas/` request models and published schemas,
  `handlers/` dispatch and handlers (`TOOL_SPECS` is the wiring), `serializer.py`
  response envelope, `schema_normalizer.py` schema flattening.
- `src/gdb_mcp/session/` — `registry.py` session ids and lifecycle, `service.py`
  facade over collaborators that share one `SessionRuntime`.
- `src/gdb_mcp/transport/` — the GDB/MI client and externally owned streams.
- `src/gdb_mcp/client/` — `cli.py`, per-family `specs/`, `runtime.py` transport,
  `daemon.py` per-project background server.
- `tests/` mirrors the packages (`integration/` needs a real `gdb`);
  `skills/` and `examples/` hold agent-facing material.
- Docs: `README.md` is the overview, `TOOLS.md` the per-tool contract.

## Rules

- Dispatch owns normalization, validation, session resolution, workflow locking,
  failure annotation, and serialization. Handlers only call a service and return
  `OperationSuccess` / `OperationError`.
- Never duplicate a tool name or action literal; import it from `contracts.py`.
  `tests/mcp/test_contracts.py` asserts the registries agree.
- Published schemas are combinator-free by policy: no `oneOf`, `anyOf`, `$ref`,
  `$defs`, or `discriminator`. Flattening must preserve semantics, so per-variant
  requirements become `Conditional requirements:` text and per-discriminator
  fields become `Accepted fields depend on ...:` notes.
- Every result carries the same envelope both as `structuredContent` and as text
  in `content[0].text`, with `isError` taken from the result type.
- Errors keep `status`, `code`, `message`; validation errors add
  `details.field_errors` and list every failure instead of the first one. The
  code list lives in `TOOLS.md`.
- Session ids are per process and restart at 1; `invalid_session` errors list the
  live ids.
- Python 3.10 floor, fully annotated code (mypy `disallow_untyped_defs`).

## Before Commit

```bash
.venv/bin/black --check src tests
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q     # includes tests/integration/, which needs gdb and g++
git diff --check
```

CI runs the same checks, so what passes locally passes there. `ruff` uses
upstream's default rule set; `[tool.ruff]` only pins line length and target
version and documents the one deliberate ignore.

Commit one logical change at a time. For small edits run the narrowest relevant
tests and say what you skipped. Do not revert unrelated local changes.

## Docs

Behavior changes update `TOOLS.md` (the contract) and `README.md` (the overview),
plus `examples/` and `skills/` when a workflow changes. Skills stay
interface-only: which tool or command to call and how failures look, not
implementation detail. Use v2 tool names in every example and skill.
