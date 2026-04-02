# GDB MCP Server

An MCP (Model Context Protocol) server that gives AI assistants structured access to GDB debugging sessions. The server speaks to GDB over GDB/MI and exposes a domain-oriented MCP surface for session lifecycle, execution control, breakpoints, context navigation, inspection, and higher-level workflows.

## Features

- Structured GDB control with machine-readable success and error payloads
- Multi-session support with explicit `session_id` routing
- Multi-inferior workflows, including inferior creation/selection and fork-follow controls
- Read/write split by domain instead of one tool per operation
- Workflow helpers for batch execution, capture bundles, and repeat-until-failure campaigns
- Dedicated privileged tools for attach and function-call operations
- `gdb_execute_command` escape hatch for CLI and MI commands that do not yet have a dedicated structured tool

## Architecture

This server uses the GDB Machine Interface (GDB/MI), the same protocol family used by IDEs such as VS Code and CLion. The MCP layer sits on top of that transport and provides:

- strict JSON-schema validation for tool inputs
- explicit success/error envelopes for automation clients
- stable domain-oriented tool names
- structured session state snapshots instead of screen-scraped terminal output

## Installation

### Prerequisites

- Python 3.10 or higher
- GDB installed and available on `PATH`

### Quick Start

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath

cd /path/to/gdb-mcp
pipx install .
```

For virtual-environment and manual-install variants, see [INSTALL.md](INSTALL.md).

## Configuration

### Claude Desktop

Add this to your Claude Desktop configuration file:

```json
{
  "mcpServers": {
    "gdb": {
      "command": "gdb-mcp-server"
    }
  }
}
```

Typical config file locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\\Claude\\claude_desktop_config.json`

For other MCP clients, see [INSTALL.md](INSTALL.md#step-5-configure-your-mcp-client).

## Environment Variables

### `GDB_PATH`

Overrides the default GDB binary. This is useful when multiple GDB versions are installed or when GDB is not on the default `PATH`.

```bash
export GDB_PATH=/usr/local/bin/gdb-13.2
gdb-mcp-server
```

If both are present, `gdb_session_start.gdb_path` overrides `GDB_PATH`.

### `GDB_MCP_LOG_LEVEL`

Controls server logging.

```bash
export GDB_MCP_LOG_LEVEL=DEBUG
gdb-mcp-server
```

Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

## Tool Surface

The current public interface exposes 17 tools:

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

The interface follows three rules:

- Domain consolidation: related operations share one tool family.
- Read/write split: query-style actions and mutating actions are separate tools.
- Action-scoped payloads: action-based tools take `action` plus one nested payload object such as `query`, `context`, `execution`, `inferior`, or `breakpoint`.

Examples:

- `gdb_session_query(action="list")`
- `gdb_execution_manage(action="continue")`
- `gdb_breakpoint_manage(action="create")`
- `gdb_context_query(action="backtrace")`
- `gdb_inspect_query(action="registers")`

`gdb_session_start` remains separate because startup has a unique request shape. `gdb_execute_command`, `gdb_attach_process`, and `gdb_call_function` also remain separate so escape-hatch and privileged operations are easy to permission independently.

Detailed request and response documentation lives in [TOOLS.md](TOOLS.md).

## Response Model

There are two success shapes:

### Dedicated Tools Without `action`

`gdb_session_start`, `gdb_workflow_batch`, `gdb_capture_bundle`, `gdb_run_until_failure`, `gdb_execute_command`, `gdb_attach_process`, and `gdb_call_function` return direct structured success payloads:

```json
{
  "status": "success",
  "session_id": 7,
  "message": "GDB session started successfully",
  "target_loaded": true,
  "execution_state": "not_started"
}
```

### Action-Based Tools

Action-based tools return a uniform envelope:

```json
{
  "status": "success",
  "action": "status",
  "result": {
    "is_running": true,
    "target_loaded": true,
    "execution_state": "paused"
  }
}
```

Errors always use a machine-readable envelope:

```json
{
  "status": "error",
  "code": "validation_error",
  "message": "breakpoint.location is required for kind=code",
  "action": "create",
  "details": {
    "field_errors": [
      {
        "field": "breakpoint.location",
        "issue": "missing"
      }
    ]
  }
}
```

## Usage Examples

### Example 1: Core Dump Inspection

Start a post-mortem session:

```json
{
  "program": "/path/to/executable",
  "core": "/tmp/core.12345",
  "init_commands": [
    "set sysroot /opt/sysroot"
  ]
}
```

Then inspect thread inventory:

```json
{
  "session_id": 7,
  "action": "threads",
  "query": {}
}
```

The second payload is a `gdb_context_query` call.

### Example 2: Set A Conditional Breakpoint And Continue

Create a code breakpoint:

```json
{
  "session_id": 7,
  "action": "create",
  "breakpoint": {
    "kind": "code",
    "location": "process_data",
    "condition": "count > 100",
    "temporary": false
  }
}
```

Continue execution:

```json
{
  "session_id": 7,
  "action": "continue",
  "execution": {
    "wait": {
      "until": "stop",
      "timeout_sec": 30
    }
  }
}
```

The first payload is `gdb_breakpoint_manage`. The second is `gdb_execution_manage`.

### Example 3: One Locked Batch

Execute a breakpoint, run, and backtrace flow under one workflow lock:

```json
{
  "session_id": 7,
  "steps": [
    {
      "tool": "gdb_breakpoint_manage",
      "label": "break main",
      "arguments": {
        "action": "create",
        "breakpoint": {
          "kind": "code",
          "location": "main"
        }
      }
    },
    {
      "tool": "gdb_execution_manage",
      "label": "run",
      "arguments": {
        "action": "run",
        "execution": {}
      }
    },
    {
      "tool": "gdb_context_query",
      "label": "stack",
      "arguments": {
        "action": "backtrace",
        "query": {}
      }
    }
  ]
}
```

That payload is a `gdb_workflow_batch` call. Step arguments never include `session_id`; the batch injects it automatically.

## Advanced Usage

### Custom Initialization Commands

Use `init_commands` to configure GDB before normal debugging begins:

```json
{
  "program": "/path/to/myprogram",
  "init_commands": [
    "set print pretty on",
    "set pagination off"
  ]
}
```

If you provide `env`, those variables are applied before any `init_commands` run.

`args` and `core` are intentionally mutually exclusive in one startup request. Use `args` for live launches and `core` for post-mortem analysis.

### Working With Running Processes

Attach with the dedicated privileged tool:

```json
{
  "session_id": 7,
  "pid": 12345,
  "timeout_sec": 30
}
```

That payload is a `gdb_attach_process` call.

## Troubleshooting

### GDB Not Found

```bash
which gdb
gdb --version
```

### Commands Time Out While The Program Is Running

GDB is usually busy because the inferior is still running.

- Inspect state with `gdb_session_query(action="status")`
- Wait for a stop with `gdb_execution_manage(action="wait_for_stop")`
- Force a pause with `gdb_execution_manage(action="interrupt")`

Common execution states:

- `not_started`
- `running`
- `paused`
- `exited`
- `unknown`

### Missing Debug Symbols

Always inspect the `warnings` array returned by `gdb_session_start`. Build targets with `-g` if you need source-level breakpoints, locals, and meaningful backtraces.

For additional troubleshooting and installation help, see [INSTALL.md](INSTALL.md#troubleshooting).

## How It Works

1. The server talks to GDB over GDB/MI.
2. Session services normalize GDB behavior into structured Python domain models.
3. The MCP layer validates input against Pydantic schemas and returns JSON payloads through `call_tool`.
4. Clients keep track of `session_id` and use the returned state snapshots as the source of truth.

## MCP Client Contract

This server currently exposes MCP context through:

- `list_tools`
- `call_tool`

Every `call_tool` response is returned as one JSON payload encoded in MCP `TextContent`.

Clients should expect:

- `status` on every response
- `action` plus nested `result` on action-based tools
- direct structured success payloads on dedicated tools without `action`
- `code` plus `message` on every error payload

The server does not currently publish MCP `outputSchema`, resources, prompts, or event streams. Clients should treat tool responses as authoritative runtime state and manage `session_id` explicitly.

## Migration

This interface is a clean break from the earlier one-tool-per-operation surface.

- `gdb_start_session` is now `gdb_session_start`
- `gdb_get_status` is now `gdb_session_query(action="status")`
- `gdb_run` is now `gdb_execution_manage(action="run")`
- `gdb_set_breakpoint` is now `gdb_breakpoint_manage(action="create", breakpoint.kind="code")`
- `gdb_get_backtrace` is now `gdb_context_query(action="backtrace")`
- `gdb_batch` is now `gdb_workflow_batch`

The full migration appendix is in [TOOLS.md](TOOLS.md#migration-appendix).

## Contributing

Before opening a PR for code changes, run:

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
git diff --check
```

## License

MIT

## References

- [GDB Machine Interface (MI)](https://sourceware.org/gdb/current/onlinedocs/gdb/GDB_002fMI.html)
- [pygdbmi Documentation](https://github.com/cs01/pygdbmi)
- [Model Context Protocol](https://modelcontextprotocol.io/)
