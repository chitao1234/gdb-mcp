# GDB MCP Server

An MCP (Model Context Protocol) server that provides AI assistants with programmatic access to GDB debugging sessions. This allows AI models to interact with debuggers in the same way IDEs like VS Code and CLion do, using the GDB/MI (Machine Interface) protocol.

## Features

- **Full GDB Control**: Start sessions, execute commands, control program execution
- **Thread Analysis**: Inspect threads, get backtraces, analyze thread states
- **Breakpoint Management**: Set conditional breakpoints, temporary breakpoints
- **Variable Inspection**: Evaluate expressions, inspect variables and registers
- **Core Dump Analysis**: Load and analyze core dumps with custom initialization
- **Flexible Initialization**: Run GDB scripts or commands on startup

## Architecture

This server uses the **GDB/MI (Machine Interface)** protocol, which is the same interface used by professional IDEs. It provides:

- Structured, machine-parseable output
- Full access to GDB's debugging capabilities
- Reliable command execution and response handling

## Installation

### Prerequisites

- Python 3.10 or higher
- GDB installed and available in PATH

### Quick Start

```bash
# Install pipx if needed
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# Install gdb-mcp-server
cd /path/to/gdb-mcp
pipx install .
```

**For alternative installation methods (virtual environment, manual setup), see [INSTALL.md](INSTALL.md).**

## Configuration

### Claude Desktop

Add this to your Claude Desktop configuration file:

**Location:**
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

**Configuration:**
```json
{
  "mcpServers": {
    "gdb": {
      "command": "gdb-mcp-server"
    }
  }
}
```

**For other installation methods and MCP clients, see [INSTALL.md](INSTALL.md#step-5-configure-your-mcp-client).**

## Environment Variables

The GDB MCP Server supports the following environment variables:

### `GDB_PATH`

Specify the path to the GDB executable to use. This is useful when:
- You have multiple GDB versions installed
- GDB is installed in a non-standard location
- You want to use a custom or patched GDB build

**Default**: `gdb` (resolved via system PATH)

**Example**:
```bash
export GDB_PATH=/usr/local/bin/gdb-13.2
gdb-mcp-server
```

**Note**: The `gdb_path` parameter in the `gdb_start_session` tool overrides this environment variable if both are specified.

### `GDB_MCP_LOG_LEVEL`

Set the logging level for the server.

**Default**: `INFO`
**Options**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

**Example**:
```bash
export GDB_MCP_LOG_LEVEL=DEBUG
gdb-mcp-server
```

## Available Tools

The GDB MCP Server provides 37 tools for controlling GDB debugging sessions:

**Session Management:**
- `gdb_start_session` - Start a new GDB session with optional initialization
- `gdb_list_sessions` - List all active sessions with structured metadata
- `gdb_execute_command` - Execute GDB commands (CLI or MI format)
- `gdb_run` - Start the loaded program with optional argv overrides
- `gdb_attach_process` - Attach GDB to a running process by PID
- `gdb_list_inferiors` - List inferiors in the current debugger session
- `gdb_select_inferior` - Select the active inferior
- `gdb_set_follow_fork_mode` - Configure fork-follow behavior (`parent`/`child`)
- `gdb_set_detach_on_fork` - Configure detach-on-fork behavior
- `gdb_batch` - Execute a structured sequence of session-scoped tools
- `gdb_capture_bundle` - Capture structured forensic artifacts to disk
- `gdb_run_until_failure` - Repeat fresh runs until failure criteria match
- `gdb_call_function` - Call a function in the target process (dedicated tool for separate permissioning)
- `gdb_get_status` - Get current session status, including target load state, execution state, and per-inferior state summaries when available
- `gdb_stop_session` - Stop the current session

**Thread & Frame Navigation:**
- `gdb_get_threads` - List all threads
- `gdb_select_thread` - Select a specific thread
- `gdb_get_backtrace` - Get stack trace for a thread without changing the selected thread
- `gdb_select_frame` - Select a specific stack frame
- `gdb_get_frame_info` - Get information about the current frame

**Breakpoint Management:**
- `gdb_set_breakpoint` - Set breakpoints with optional conditions, including source paths with spaces
- `gdb_set_watchpoint` - Set write/read/access watchpoints
- `gdb_delete_watchpoint` - Delete watchpoints by number
- `gdb_set_catchpoint` - Set catchpoints for debugger events (fork, signal, syscall, etc.)
- `gdb_list_breakpoints` - List all breakpoints with structured data
- `gdb_delete_breakpoint` - Delete a breakpoint by number
- `gdb_enable_breakpoint` - Enable a breakpoint
- `gdb_disable_breakpoint` - Disable a breakpoint

**Execution Control:**
- `gdb_continue` - Continue execution
- `gdb_wait_for_stop` - Wait for the next stop event without polling
- `gdb_step` - Step into functions
- `gdb_next` - Step over functions
- `gdb_interrupt` - Pause a running program

**Data Inspection:**
- `gdb_evaluate_expression` - Evaluate expressions, optionally in a specific thread/frame
- `gdb_read_memory` - Read raw memory bytes using MI memory-read
- `gdb_get_variables` - Get local variables without changing the selected thread/frame
- `gdb_get_registers` - Get CPU registers, optionally in a specific thread/frame, with selector/filter options for large payloads

**For detailed documentation of each tool including parameters, return values, and examples, see [TOOLS.md](TOOLS.md).**

`gdb_get_status` reports `target_loaded=false` when GDB started but the requested
executable or core file did not load successfully. If the underlying GDB process
dies unexpectedly, later status checks report the session as no longer running.
The `gdb_start_session` response also includes `target_loaded` so callers can tell
immediately whether startup loaded a usable target.
It also includes the initial `execution_state` when startup leaves the inferior
in a known state, such as `not_started` for a loaded executable or `paused` for
a loaded core dump.
When multiple sessions are active, `gdb_list_sessions` provides an inventory view
with session IDs, lifecycle/execution state, and basic target metadata so MCP
clients can recover or render session state without maintaining all bookkeeping
out of band.
Both `gdb_get_status` and `gdb_list_sessions` include `inferior_states` when known,
so clients can reason about forked/multi-inferior state without extra polling glue.
`gdb_get_status` also reports the inferior execution state as `not_started`,
`running`, `paused`, `exited`, or `unknown`.

## Usage Examples

### Example 1: Analyzing a Core Dump

**User**: "Load the core dump at /tmp/core.12345, set the sysroot to /opt/sysroot, and tell me how many threads there were when it crashed."

**AI Actions**:
1. Start session with init commands:
```json
{
  "program": "/path/to/executable",
  "core": "/tmp/core.12345",
  "init_commands": [
    "set sysroot /opt/sysroot"
  ]
}
```
2. Get threads: `gdb_get_threads`
3. Report: "There were 8 threads when the program crashed."

### Example 2: Conditional Breakpoint Investigation

**User**: "Set a breakpoint at process_data but only when the count variable is greater than 100, then continue execution."

**AI Actions**:
1. Set conditional breakpoint:
```json
{
  "location": "process_data",
  "condition": "count > 100"
}
```
2. Continue execution: `gdb_continue`
3. When hit, inspect state

**For more detailed usage examples and workflows, see [examples/USAGE_GUIDE.md](examples/USAGE_GUIDE.md) and [examples/README.md](examples/README.md).**

## Advanced Usage

### Custom GDB Initialization Scripts

Create a `.gdb` file with your setup commands:

```gdb
# setup.gdb
file /path/to/myprogram
core-file /path/to/core

# Set up symbol paths
set sysroot /opt/sysroot
set solib-search-path /opt/libs:/usr/local/lib

# Convenience settings
set print pretty on
set print array on
set pagination off
```

Then use it:
```json
{
  "init_commands": ["source setup.gdb"]
}
```

If you provide `env`, those environment variables are applied before any `init_commands` run.

`args` and `core` are intentionally mutually exclusive in one startup request: use `args` for a live program launch, or `core` for post-mortem analysis. `args` accepts either an explicit list (`["--mode","fast"]`) or a shell-style string (`"--mode fast"`).

By default, startup applies `set confirm off` so CLI commands stay non-interactive in automation workflows.

### Python Initialization Scripts

You can also use GDB's Python API:

```python
# init.py
import gdb
gdb.execute("file /path/to/program")
gdb.execute("core-file /path/to/core")
# Custom analysis
```

Use with:
```json
{
  "init_commands": ["source init.py"]
}
```

### Working with Running Processes

While this server primarily works with core dumps and executables, you can also
attach to running processes with the dedicated `gdb_attach_process` tool:

```json
{
  "session_id": 1,
  "pid": 12345
}
```

Note: This requires appropriate permissions (usually root or same user).

## Troubleshooting

### Common Issues

**GDB Not Found**
```bash
which gdb
gdb --version
```

**Timeout Errors / Commands Not Responding**

The program is likely still running! When a program is running, GDB is busy and won't respond to other commands.

**Solution:** Use `gdb_interrupt` to pause the running program, then other commands will work.

**Program States:**
- **Not started**: Use `gdb_run`
- **Running**: Program is executing. `gdb_continue` may return success with a running state if no stop event occurred yet; use `gdb_wait_for_stop` to block for the next stop, or `gdb_interrupt` to force a pause.
- **Paused** (at breakpoint): Use `gdb_continue`, `gdb_step`, `gdb_next`, inspect variables
- **Finished**: Program has exited - restart with "run" if needed

**Missing Debug Symbols**

Always check the `warnings` field in `gdb_start_session` response! Compile your programs with the `-g` flag.

**For detailed troubleshooting, installation issues, and more solutions, see [INSTALL.md](INSTALL.md#troubleshooting).**

## How It Works

1. **GDB/MI Protocol**: The server communicates with GDB using the Machine Interface (MI) protocol, the same interface used by IDEs.

2. **pygdbmi Library**: We use the excellent `pygdbmi` library to handle the low-level protocol details and response parsing.

3. **MCP Integration**: The server exposes GDB functionality as MCP tools, allowing AI assistants to:
   - Understand the available debugging operations
   - Execute commands with proper parameters
   - Interpret structured responses

4. **Session Management**: The registry supports multiple concurrent GDB sessions per server instance. Each tool call uses an explicit `session_id` for isolation.

## MCP Client Contract

This server currently exposes MCP context to clients via:

- `list_tools`: Returns tool name, description, and input JSON schema.
- `call_tool`: Returns one JSON payload encoded in MCP `TextContent`.

The response payload always contains:

- `status`: `"success"` or `"error"`
- `message`: Present on errors and many success payloads
- Tool-specific fields for structured results

The server does not currently publish MCP `outputSchema`, resource endpoints, or event streams. Clients should treat tool responses as the authoritative runtime state and keep track of `session_id` explicitly.

## Contributing

Contributions welcome! Areas for improvement:
- Additional GDB commands (e.g., watchpoints, memory inspection)
- Better error handling and recovery
- Enhanced output formatting

## License

MIT

## References

- [GDB Machine Interface (MI)](https://sourceware.org/gdb/current/onlinedocs/gdb/GDB_002fMI.html)
- [pygdbmi Documentation](https://github.com/cs01/pygdbmi)
- [Model Context Protocol](https://modelcontextprotocol.io/)
