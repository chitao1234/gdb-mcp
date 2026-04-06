# Selectable MCP Transport Design

**Date:** 2026-04-07

## Goal

Add streamable HTTP server support without breaking the existing stdio entrypoint. The server should continue to default to stdio for MCP clients such as Claude Desktop, while allowing operators to explicitly select a streamable HTTP transport from the command line.

## Background

The current server has one startup path:

- `gdb-mcp-server` configures logging
- builds a `ServerRuntime`
- runs the MCP app over stdio

That shape is intentionally thin and works well for stdio-based MCP clients, but it does not let HTTP-capable clients connect to the same tool surface. The installed `mcp` SDK already includes `StreamableHTTPSessionManager`, Starlette integration, and uvicorn-based serving helpers, so this feature should extend the existing server rather than replace it.

## Scope

This design covers:

- command-line transport selection for `gdb-mcp-server`
- a new streamable HTTP serving path alongside stdio
- transport-specific CLI validation and defaults
- runtime and app-layer lifecycle handling for HTTP mode
- tests for CLI selection and HTTP runner wiring
- user-facing docs and skill guidance for the new transport option

## Non-Goals

- changing the default transport away from stdio
- adding environment-variable-based transport selection
- changing MCP tool schemas, handlers, or debugger behavior
- adding OAuth, auth middleware, event-store resumability, SSE transport, or websocket transport
- redesigning the session registry or `SessionService` beyond shutdown integration required for HTTP lifespan management

## Design Principles

1. Preserve zero-argument backward compatibility for `gdb-mcp-server`.
2. Keep transport selection explicit and deterministic.
3. Share one runtime, one tool surface, and one session registry across all transports.
4. Fail fast on invalid CLI combinations instead of silently ignoring flags.
5. Keep the HTTP implementation minimal by using the MCP SDK’s supported streamable HTTP primitives directly.

## User-Facing Behavior

### Command-Line Interface

The server will expose these flags:

- `--transport {stdio,streamable-http}` with default `stdio`
- `--host` for the HTTP bind address
- `--port` for the HTTP bind port
- `--path` for the HTTP MCP endpoint path

Examples:

```bash
gdb-mcp-server
```

```bash
gdb-mcp-server --transport streamable-http
```

```bash
gdb-mcp-server --transport streamable-http --host 0.0.0.0 --port 9000 --path /mcp
```

### Defaults

When `--transport streamable-http` is selected, the defaults will be:

- host: `127.0.0.1`
- port: `8000`
- path: `/mcp`

These defaults match the MCP SDK’s streamable HTTP expectations and keep the server private to localhost unless the operator explicitly broadens exposure.

### Invalid Flag Combinations

`--host`, `--port`, and `--path` are HTTP-only options. If any of them are supplied while `--transport` is left at `stdio`, startup should fail with a direct CLI usage error. The implementation should reject these combinations during argument parsing or immediately after parsing, before the runtime starts.

### Startup Logging

Startup logging remains controlled by `GDB_MCP_LOG_LEVEL`.

- In stdio mode, the current startup message remains unchanged.
- In streamable HTTP mode, startup logs should include the effective bind address and path, for example `http://127.0.0.1:8000/mcp`, so operators can configure clients without guessing the endpoint.

## Architecture

### High-Level Structure

The runtime stays transport-agnostic for tool behavior and session ownership. Transport selection is a thin layer above it.

Responsibilities by module:

- `src/gdb_mcp/server.py`
  - parse CLI flags
  - validate transport-specific arguments
  - configure logging
  - construct the default runtime
  - dispatch to stdio or streamable HTTP serving
- `src/gdb_mcp/mcp/runtime.py`
  - keep `ServerRuntime` as the composition root
  - expose explicit stdio and streamable HTTP run methods
  - centralize shared shutdown behavior
- `src/gdb_mcp/mcp/app.py`
  - continue to build the low-level MCP app
  - add streamable HTTP ASGI construction and uvicorn serving helpers
- `src/gdb_mcp/__main__.py`
  - continue delegating to the main CLI entrypoint

### Transport Settings

Add a small typed configuration model for transport selection rather than passing unstructured dictionaries through the stack. The model should capture:

- transport kind
- HTTP host
- HTTP port
- HTTP path

The stdio path should ignore HTTP-specific values because CLI validation prevents them from being used there.

### Stdio Flow

The stdio serving path remains the current one:

1. Build the low-level MCP `Server`
2. Open stdio streams via `mcp.server.stdio.stdio_server()`
3. Run the server with initialization options
4. On shutdown, call `SessionRegistry.shutdown_all()`

This path should preserve the current behavior and test expectations as closely as possible.

### Streamable HTTP Flow

The HTTP serving path should:

1. Build the same low-level MCP `Server`
2. Wrap it in a Starlette ASGI app using `StreamableHTTPSessionManager`
3. Register the selected HTTP route at the configured `--path`
4. Run the ASGI app with uvicorn using the configured host and port
5. Use the ASGI lifespan hook to guarantee session cleanup on process shutdown

The design intentionally uses the MCP SDK’s built-in streamable HTTP machinery instead of reimplementing the protocol by hand.

## Lifecycle And Shutdown

### Shared Session Ownership

The `SessionRegistry` remains process-global for a given server instance. Transport selection must not create multiple registries or split session ownership across layers.

### Shutdown Semantics

`ServerRuntime.shutdown_sessions()` remains the single cleanup hook that stops live debugger sessions.

- stdio mode should call it in the existing `finally` path after app execution ends
- HTTP mode should call it from the ASGI lifespan shutdown path so uvicorn termination also cleans up live GDB sessions

This keeps debugger cleanup consistent regardless of transport.

### Per-Request Behavior

Transport changes must not alter MCP handler semantics:

- tool registration remains identical
- tool input and output schemas remain identical
- session ids remain explicit in tool arguments
- multi-session behavior remains registry-driven rather than transport-driven

## Error Handling

### CLI Errors

Transport misuse should produce deterministic startup failures:

- HTTP-only flags with `stdio` should fail immediately
- unsupported transport values should be rejected by the parser
- invalid HTTP path values should fail validation before the server starts if the path is malformed for routing purposes

Using a standard CLI parser error with a non-zero exit status is acceptable and preferable to partial startup.

### Runtime Errors

The runtime should continue surfacing transport startup failures directly rather than swallowing them. This includes:

- uvicorn binding failures such as address already in use
- invalid host or port values that reach the server layer
- unexpected ASGI startup failures

The design does not add custom retry or recovery logic for HTTP transport startup.

## Testing Strategy

### Entry Point Tests

Extend `tests/mcp/test_server_entrypoint.py` to cover:

- default invocation still dispatches to stdio without requiring flags
- `--transport streamable-http` selects the HTTP path
- HTTP flags are forwarded correctly
- invalid flag combinations fail with a CLI usage error
- repeated invocations still build fresh runtime instances

### Runtime And App Tests

Add or update tests in `tests/mcp/test_runtime.py` and, if needed, a dedicated app-layer test module to cover:

- stdio run path still delegates through the stdio helper
- HTTP run path delegates through the HTTP helper with the expected host, port, and path
- session shutdown remains wired into both transports
- HTTP ASGI app construction uses the MCP SDK session manager and lifespan cleanup hooks

These tests should stay lightweight and mock uvicorn and Starlette wiring rather than attempting full network integration.

### Verification Expectations

For implementation, the minimum relevant checks are:

- `ruff check src tests`
- `mypy src`
- focused `pytest` coverage for touched MCP server tests
- `git diff --check`

If the implementation changes broaden beyond the entrypoint and MCP runtime surface, the full `pytest -q` run should also be considered.

## Documentation Impact

Update these files in the same change set:

- `README.md`
  - mention selectable transports
  - keep stdio as the default config path
  - add a streamable HTTP launch example
- `INSTALL.md`
  - document CLI transport flags
  - show stdio and HTTP client configuration examples where relevant
- `TOOLS.md`
  - add a short transport note if needed so users know tool semantics are transport-independent
- `examples/README.md`
  - explain when HTTP mode is useful
- `examples/USAGE_GUIDE.md`
  - clarify that the tool workflows are identical across stdio and streamable HTTP
- `skills/debug-with-gdb-mcp/SKILL.md`
  - keep operational guidance aligned with the updated startup story

## Dependency Notes

No new first-party transport library should be introduced for this feature. The installed `mcp` package already depends on `uvicorn`, `starlette`, and `sse-starlette`, so the implementation should use those existing transitive dependencies through the SDK-supported streamable HTTP path.

## Recommended Implementation Shape

Implement the feature as an additive transport extension:

1. Add a parsed CLI surface in `server.py`
2. Introduce a typed transport configuration object
3. Split runtime execution into explicit stdio and streamable HTTP methods
4. Add the HTTP app/runner helper in `mcp/app.py`
5. Extend server and runtime tests
6. Update docs and skill guidance in the same change set

This keeps the change focused, backward-compatible, and easy to verify.
