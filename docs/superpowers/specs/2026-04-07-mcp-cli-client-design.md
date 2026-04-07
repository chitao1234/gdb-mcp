# MCP CLI Client Design

**Date:** 2026-04-07

## Goal

Add a first-party `gdb-mcp-client` command-line client for the existing GDB MCP server. The client should connect to a streamable HTTP MCP endpoint, expose the same MCP tool inventory as one CLI subcommand per tool, accept command-line flags instead of raw JSON for normal usage, and support both human-oriented output and raw JSON output for scripting.

## Background

The repository currently provides a server entrypoint, `gdb-mcp-server`, that exposes the v2 MCP tool surface over stdio by default and over streamable HTTP when explicitly selected. There is no first-party CLI client in `src/`, which leaves two gaps:

- users cannot invoke the current MCP tool surface directly from a terminal without writing their own MCP client
- there is no maintained CLI mapping that demonstrates how the v2 tool interface should be used from command-line workflows

The server already has a clear, structured tool inventory in `src/gdb_mcp/mcp/schemas.py`, low-level tool dispatch in `src/gdb_mcp/mcp/handlers.py`, and transport support for streamable HTTP. The client should build on those pieces rather than introducing a parallel protocol or a separate ad hoc request schema layer.

## Scope

This design covers:

- a new `gdb-mcp-client` console script
- connection to an existing streamable HTTP MCP server via `--server-url`
- one CLI subcommand per MCP tool in the current public inventory
- flag-based request construction for dedicated tools and action-based tools
- client-side request validation using the existing schema models
- human-oriented output by default, plus `--json` output
- deterministic exit codes for success, tool-level errors, and transport failures
- tests for CLI parsing, adapter behavior, runtime integration, and HTTP invocation
- user-facing documentation for the new client

## Non-Goals

This design does not cover:

- stdio client transport support
- interactive shell or REPL behavior
- persistent client-side session caching or session discovery outside normal tool calls
- changing server-side tool names, schemas, or response envelopes
- replacing the MCP SDK client with handwritten HTTP requests
- generating command-line interfaces dynamically from runtime `tools/list` responses
- adding client-side auth, OAuth, config files, shell completion, or batch command files

## Design Principles

1. Mirror the server’s public MCP tool surface exactly rather than inventing a new client command vocabulary.
2. Keep the CLI usable by humans without forcing users to hand-author raw JSON for common operations.
3. Reuse the existing schema models as the validation source of truth wherever possible.
4. Prefer shared helpers and small adapters over handwritten per-tool parsing logic.
5. Preserve scriptability with a raw JSON mode and stable exit codes.

## User-Facing Behavior

### Global CLI Shape

The new script is:

```bash
gdb-mcp-client --server-url http://127.0.0.1:8000/mcp <tool-name> [tool flags...]
```

Global flags:

- `--server-url`: required base URL for the streamable HTTP MCP endpoint
- `--json`: optional switch to print the parsed tool response payload as JSON instead of rendering human-oriented output

The client should fail fast if `--server-url` is missing or malformed enough to prevent a connection attempt.

### Tool Subcommands

Each public MCP tool gets one same-named CLI subcommand:

- `gdb_session_start`
- `gdb_session_query`
- `gdb_session_manage`
- `gdb_inferior_query`
- `gdb_inferior_manage`
- `gdb_execution_manage`
- `gdb_breakpoint_query`
- `gdb_breakpoint_manage`
- `gdb_context_query`
- `gdb_context_manage`
- `gdb_inspect_query`
- `gdb_workflow_batch`
- `gdb_capture_bundle`
- `gdb_run_until_failure`
- `gdb_execute_command`
- `gdb_attach_process`
- `gdb_call_function`

The subcommand names intentionally match the MCP tool names exactly so the CLI remains aligned with the public server interface and the documentation stays transport-independent.

### Flag Conventions

Dedicated tools map top-level request fields directly to flags. Examples:

- `--program`
- `--session-id`
- `--command`
- `--pid`
- `--timeout-sec`

For repeated string lists, the CLI uses repeated flags:

- `--arg VALUE`
- `--init-command VALUE`
- `--stop-reason VALUE`
- `--expression VALUE`
- `--register-name VALUE`

For string maps, the CLI uses repeated `KEY=VALUE` flags:

- `--env KEY=VALUE`

For boolean values, the CLI should use explicit paired positive and negative flags when a field needs to be user-settable regardless of its default. Examples:

- `--fail-fast` and `--no-fail-fast`
- `--capture-stop-events` and `--no-capture-stop-events`
- `--include-threads` and `--no-include-threads`

This keeps the help text clear and avoids ambiguous string-to-bool parsing.

For memory-range collections, the CLI should use a repeated shorthand flag that matches the schema’s existing compact form and extends it with an optional name prefix for labeled ranges:

- `--memory-range 0x401000:64`
- `--memory-range 0x401000:64@16`
- `--memory-range stack=0x7fffffffe000:128`

### Action-Based Tools

Action-based tools remain one subcommand each. They take `--action` plus flattened flags that map to the selected nested payload shape.

Example:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_execution_manage \
  --session-id 7 \
  --action run \
  --arg --mode \
  --arg fast \
  --wait-until stop \
  --wait-timeout-sec 30
```

The client should reconstruct the nested envelope expected by the schema, such as:

```json
{
  "session_id": 7,
  "action": "run",
  "execution": {
    "args": ["--mode", "fast"],
    "wait": {
      "until": "stop",
      "timeout_sec": 30
    }
  }
}
```

The same pattern applies to other action families such as:

- `gdb_session_query(action=...)`
- `gdb_session_manage(action=...)`
- `gdb_inferior_manage(action=...)`
- `gdb_breakpoint_query(action=...)`
- `gdb_breakpoint_manage(action=...)`
- `gdb_context_query(action=...)`
- `gdb_context_manage(action=...)`
- `gdb_inspect_query(action=...)`

### Explicit Selector Flags For Union Payloads

Some existing request schemas use discriminated unions, especially for inspection and breakpoint-related selectors. The client should expose explicit selector flags rather than forcing users to write raw nested JSON.

Examples:

- `--location-kind function --function main`
- `--location-kind file-line --file src/main.c --line 42`
- `--location-kind address-range --start-address 0x401000 --end-address 0x401040`
- `--breakpoint-kind code --location main`
- `--breakpoint-kind watch --expression counter --access write`

The adapter layer should then reconstruct the corresponding discriminated object expected by the schema model.

### Workflow Step Flags

The highest-complexity request bodies are `gdb_workflow_batch.steps` and `gdb_run_until_failure.setup_steps`, because they contain repeated structured step definitions with tool-specific nested arguments.

These should still remain flag-based. The client should expose grouped repeated flags rather than raw JSON:

- `--step TOOL_NAME`
- `--step-label LABEL`
- `--step-arg KEY=VALUE`
- `--step-arg PATH.TO.FIELD=VALUE`

Each `--step` starts a new step. Subsequent `--step-label` and `--step-arg` flags apply to the most recent step until another `--step` appears.

Example:

```bash
gdb-mcp-client \
  --server-url http://127.0.0.1:8000/mcp \
  gdb_workflow_batch \
  --session-id 7 \
  --step gdb_context_query \
  --step-label stack \
  --step-arg action=backtrace \
  --step-arg query.max_frames=20 \
  --step gdb_inspect_query \
  --step-arg action=evaluate \
  --step-arg query.expression=counter
```

The adapter layer should validate each step against the referenced tool’s existing schema model, excluding `session_id`, which remains injected by the enclosing batch or campaign workflow.

For `gdb_run_until_failure`, the rest of the nested structure should use explicit prefixes rather than generic JSON:

- `--startup-program`, `--startup-arg`, `--startup-env`
- `--failure-stop-reason`, `--failure-exit-code`
- `--capture-expression`, `--capture-memory-range`

That keeps the command surface shell-friendly while still covering the full structured request.

### Output Modes

Default output should be human-oriented and concise.

Examples by tool family:

- startup and status tools: labeled status lines and key identifiers
- list and query tools: compact records or tables where practical
- mutation tools: success summaries plus changed fields
- inspection tools: readable labeled sections while preserving the important structured values
- error results: code, message, and actionable detail fields

`--json` should bypass human formatting and print the parsed structured payload exactly as returned by the tool result.

### Exit Codes

The client should provide simple, deterministic exit behavior:

- `0` for successful tool calls
- non-zero for tool-level error payloads
- non-zero for connection, initialization, parsing, or transport failures

The design does not require a large exit-code taxonomy. Stable success vs failure signaling is the important contract.

## Architecture

### High-Level Structure

The client should be implemented as a separate entrypoint from the server.

Recommended file structure:

- `src/gdb_mcp/client.py` or a small `src/gdb_mcp/client/` package as the CLI entrypoint
- client-side adapter module(s) that convert flags into tool argument objects
- client-side renderer module(s) for human-oriented output
- shared runtime helper for MCP streamable HTTP connection and tool invocation

The server remains unchanged as the authoritative MCP endpoint. The client is a transport consumer, not a second execution path for debugger logic.

### Runtime Flow

Each invocation should:

1. parse global flags and the selected tool subcommand
2. use the tool-specific adapter to build a structured request object
3. validate that request against the existing Pydantic schema for the tool
4. open a streamable HTTP MCP client connection to `--server-url`
5. initialize an MCP `ClientSession`
6. call the mirrored tool once
7. parse the tool’s JSON text payload into a structured object
8. render human output or emit JSON
9. exit with the appropriate status code

The client is intentionally one-shot and synchronous from a user perspective: one invocation performs one MCP tool call.

### MCP SDK Usage

The runtime should use the installed MCP SDK client for streamable HTTP rather than constructing protocol messages by hand. This keeps session initialization, request framing, and protocol compatibility aligned with the same SDK family already used by the server.

The client should treat the server as an MCP endpoint and make normal `initialize`, `tools/list` when needed, and `tools/call` requests through the SDK session layer. The core request path is `tools/call`.

### Schema Reuse

The schema models in `src/gdb_mcp/mcp/schemas.py` should remain the source of truth for request structure and validation semantics.

The client should not define a second, divergent request schema system. Instead, it should:

- parse flags into a plain Python object structure
- instantiate the same Pydantic model class used by the server
- serialize the validated model into the tool argument payload sent to MCP

This gives the client immediate local validation for missing required fields, invalid unions, numeric coercion issues, and incompatible combinations while preserving server-side validation as the final authority.

### Adapter Registry

The adapter layer is the key abstraction that keeps the CLI maintainable.

Each tool should register a small adapter definition that answers:

- which schema model validates the request
- which arguments are available as flags
- how flat CLI flags map into nested payload shapes
- which shared parser helpers apply for repeated lists, maps, selectors, or action payloads
- which human renderer should be used by default

The adapter registry should support three broad tool categories:

#### Dedicated Flat Tools

Examples:

- `gdb_session_start`
- `gdb_execute_command`
- `gdb_attach_process`
- `gdb_call_function`
- `gdb_capture_bundle`

These mostly map directly from flags to one schema model, with helper parsing for repeated lists and maps.

#### Action-Based Tools

Examples:

- `gdb_session_query`
- `gdb_session_manage`
- `gdb_inferior_query`
- `gdb_inferior_manage`
- `gdb_execution_manage`
- `gdb_breakpoint_query`
- `gdb_breakpoint_manage`
- `gdb_context_query`
- `gdb_context_manage`
- `gdb_inspect_query`

These require `--action` and a tool-specific flattening rule that reconstructs envelopes such as:

- `{"action": "...", "query": {...}}`
- `{"action": "...", "session": {...}}`
- `{"action": "...", "inferior": {...}}`
- `{"action": "...", "execution": {...}}`
- `{"action": "...", "breakpoint": {...}, "changes": {...}}`
- `{"action": "...", "context": {...}}`

#### Union-Heavy Workflow And Selector Tools

Examples:

- location selectors in `gdb_inspect_query`
- breakpoint creation payloads in `gdb_breakpoint_manage`
- structured workflow steps in `gdb_workflow_batch`
- failure/capture criteria in `gdb_run_until_failure`

These need explicit, curated CLI projections so the user can stay in flag-based mode without ambiguity. The adapter layer should support these higher-complexity cases with targeted helpers rather than broad generic magic.

In particular:

- selector unions should use explicit kind flags and kind-specific arguments
- workflow step arrays should use repeated grouped flags with per-step dotted assignments
- nested campaign sections should use stable prefixes like `startup`, `failure`, and `capture`

## Error Handling

### Local CLI Errors

Local request-shape issues should fail before any HTTP connection is attempted when possible:

- missing required flags
- invalid repeated `KEY=VALUE` map entries
- unsupported action values
- incomplete union selectors
- invalid integers, booleans, or enum values caught by local parsing
- schema validation failures raised during local model instantiation

These should be shown as deterministic CLI usage or validation errors.

### Transport And Protocol Errors

The client should surface transport failures directly and clearly, including:

- unreachable server URL
- HTTP connection errors
- MCP initialization failure
- malformed or unexpected tool result content

The design does not add retry logic, offline caching, or alternate transport fallback.

### Tool-Level Errors

If the server returns a normal structured error payload, the client should render that as a tool failure rather than treating it as a transport exception.

In human mode, the rendering should foreground:

- error code
- message
- actionable details when present

In `--json` mode, the payload should print as-is.

## Testing Strategy

### CLI Parsing Tests

Add focused tests for representative tools to cover:

- global `--server-url` requirement
- tool-name dispatch
- repeated list flags
- `KEY=VALUE` map flags
- action-based envelope reconstruction
- discriminated union selector flags
- local validation failures before transport use

The goal is not exhaustive golden coverage of every help string, but strong coverage of the adapter patterns that many tools share.

### Runtime Tests

Add unit tests for the HTTP runtime helper to cover:

- streamable HTTP client connection setup
- MCP session initialization
- tool invocation with the validated payload
- parsed JSON output in `--json` mode
- stable non-zero exit behavior for tool errors and transport failures

These should rely on mocking the MCP client session boundary rather than needing live network traffic for every case.

### Integration Tests

Add at least one narrow integration test that exercises the real streamable HTTP server path and the new client path together. The goal is to prove the client can invoke a real MCP tool over streamable HTTP and decode the returned payload correctly.

This integration should stay small and representative rather than trying to cover the entire tool inventory end-to-end.

### Verification Expectations

For implementation, the relevant verification set is:

- `uv run ruff check src tests`
- `uv run mypy src`
- relevant `uv run pytest -q ...` during development
- final `uv run pytest -q`
- `git diff --check`

## Documentation Impact

Update these files in the same change set:

- `README.md`
  - add a client overview and quick-start examples
- `TOOLS.md`
  - add a short CLI client usage note and examples that show tool-name parity
- `examples/README.md`
  - include client-based invocation examples where useful
- `examples/USAGE_GUIDE.md`
  - show how the same tool workflows map onto the new CLI
- `pyproject.toml`
  - add the new console script entrypoint

The docs should make the split explicit:

- `gdb-mcp-server` exposes the MCP endpoint
- `gdb-mcp-client` calls that endpoint over streamable HTTP

## Recommended Implementation Shape

Implement the client as an additive feature in four layers:

1. add the new CLI entrypoint and shared HTTP runtime helper
2. add the adapter registry plus shared parsers for repeated lists, maps, action envelopes, and selector unions
3. add human-oriented renderers and `--json` passthrough behavior
4. add tests and documentation that cover representative tool families and one real HTTP integration path

This keeps the work incremental while preserving one core invariant: the client mirrors the existing MCP tool surface rather than creating a separate command API.
