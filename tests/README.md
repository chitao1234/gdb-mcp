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
# Lint
.venv/bin/ruff check src tests

# Type-check
.venv/bin/mypy src
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

## Continuous Integration

Tests run automatically on GitHub Actions for:
- Python 3.10, 3.11, and 3.12
- Every push to main/master
- Every pull request

See `.github/workflows/test.yml` for the CI configuration.
