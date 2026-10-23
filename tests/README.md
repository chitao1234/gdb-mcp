# GDB MCP Server Tests

This directory contains the test suites for the GDB MCP server.

## Running Tests

### Install Development Dependencies

```bash
# Install the package with dev dependencies
pip install -e ".[dev]"
```

### Run Quality Checks

```bash
# Format (Black is the canonical formatter)
.venv/bin/black src tests

# Format check
.venv/bin/black --check src tests

# Lint
.venv/bin/ruff check src tests

# Type-check
.venv/bin/mypy src tests
```

### Run All Tests

```bash
# Run all tests
.venv/bin/pytest

# Run with verbose output
.venv/bin/pytest -v

# Run with coverage
.venv/bin/pytest --cov=gdb_mcp --cov-report=html
```

### Run Integration Coverage

```bash
uv run pytest -q tests/integration
```

### Run Specific Tests

Delve integration tests use `dlv` on `PATH`, `DLV_PATH`, or a path-separated
`DELVE_TEST_BINARIES` list. `GO_PATH` selects the Go compiler; `GOTOOLCHAIN`
can select a matching toolchain. For example:

```bash
GO_PATH=/opt/go/1.16.4/bin/go DLV_PATH=/opt/delve/1.6.1/dlv \
  .venv/bin/pytest -q tests/integration/test_v2_session_delve.py -rs
```

The compatibility workflow runs Delve 1.6.1 / Go 1.16.4 as its oldest baseline,
plus sampled releases 1.8.2, 1.20.2, 1.22.1, 1.24.2, and 1.27.2 with
matching Go versions.
Delve 1.6.0 predates breakpoint disabling and is below the supported floor.
Tests assert `unsupported_operation` for watchpoints before 1.7.0,
`next_instruction` before 1.23.0, and `jump` before 1.27.2. Unix listener tests
probe the selected binary's advertised support (introduced in 1.22.1).
Memory reads cross the 1,000-byte RPC limit used before 1.24.0. Attach tests
require permission to trace sibling processes; on Linux the CI job sets
`kernel.yama.ptrace_scope=0`.

The Go inspection integration workflow checks frame-local deferred-call order,
argument values before Go 1.17, and the modern register-ABI limitation. Its
channel fixture has 1,003 senders and three receivers to cover pagination,
result truncation, empty/nil channels, and preserved selection. To verify the
channel-filter boundary, run it with matching Go 1.21 binaries for Delve
1.21.0 and 1.21.1:

```bash
GOTOOLCHAIN=go1.21.13 \
DELVE_TEST_BINARIES=/opt/delve/1.21.0/dlv:/opt/delve/1.21.1/dlv \
  .venv/bin/pytest -q tests/integration/test_v2_session_delve.py -k deferred -rs
```

```bash
# Run only unit tests (excluding integration tests)
.venv/bin/pytest -m "not integration"

# Run only the session-layer tests
.venv/bin/pytest tests/session

# Run the split session API tests
.venv/bin/pytest tests/session/test_lifecycle_api.py
.venv/bin/pytest tests/session/test_execution_api.py

# Run only a specific test
.venv/bin/pytest tests/mcp/test_handlers.py::TestHandlerDispatch::test_start_session_returns_session_id
```

## Test Structure

- `domain/` - Narrow result-mapping tests that are not better owned by the MCP serializer layer
- `mcp/` - Public MCP tool inventory, schemas, routing, envelopes, and runtime dispatch
- `session/` - Session service semantics and transport-normalized behavior
- `transport/` - Low-level GDB/MI transport and parser behavior
- `integration/` - Representative direct-v2 real-GDB workflows

## Ownership Rules

1. Prefer the true ownership boundary.
2. `tests/mcp/` owns request and response shape plus action routing.
3. `tests/session/` owns debugger semantics and error details.
4. `tests/integration/` proves only representative cross-layer workflows.
5. Integration tests must not use legacy tool names or response-flattening helpers.

## Test Categories

Tests are marked with pytest markers:

- `@pytest.mark.integration` - Tests that require GDB to be installed
- `@pytest.mark.slow` - Tests that take significant time to run

## Writing New Tests

When adding new tests:

1. Follow the existing naming conventions (`test_*.py`, `Test*`, `test_*`)
2. Use descriptive test names that explain what is being tested
3. Mock external dependencies when possible
4. Prefer testing the true ownership boundary instead of routing through `server.py`
5. Keep integration coverage representative rather than exhaustive
6. Mark integration tests appropriately
7. Probe for environment capabilities instead of wrapping work in try/skip: skip when the capability is absent (for example `rr` cannot record in this sandbox), but let the operation fail once the capability is present. A real failure must never be reported as a skip.

## Continuous Integration

Tests run automatically on GitHub Actions for:
- Six sampled Nix profiles covering Python 3.10-3.14, GDB 7.1a/9.2/11.1/13.2/
  15.2/17.2, LLDB 4.0.1/7.1/11.1/14.0/18.1/21.1, and Delve 1.6.1/1.8.2/
  1.20.2/1.22.1/1.24.2/1.27.2 (see `flake.nix`)
- An oldest baseline with GDB 7.1a plus Python 2.6, LLDB 4.0.1, and
  Delve 1.6.1 / Go 1.16.4
- Every push to main/master
- Every pull request

`nix eval --json .#ciProfileNames` lists the exact profiles CI runs. Python and
the debugger packages use independent pins. The `oldest-` and `legacy-` rows
run unit tests plus `tests/integration/test_compatibility_baseline.py` and
`tests/integration/test_v2_session_delve.py`; the `full-` rows run the
complete suite.

See `.github/workflows/test.yml` for the CI configuration.
