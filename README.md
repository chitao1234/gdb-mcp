# GDB MCP Server

`gdb-mcp-server` gives AI assistants and other MCP clients structured access to
GDB, LLDB, and Delve. GDB is driven over GDB/MI; LLDB uses a native SB API
bridge; Delve uses its JSON-RPC API v2. Every call answers
with machine-readable state — session status, stop reasons, breakpoints, frames,
variables, registers, memory — instead of terminal text that a client has to
screen-scrape.

`gdb-mcp-client` is the companion CLI. It drives the same tools from a shell and
keeps one background server per project, so a debug session survives between
commands and reads like an ordinary debugger.

- Structured GDB, LLDB, and Delve control with machine-readable success and error payloads
- Native `jump` (relocate and resume) and frame-scoped `until` with explicit
  function, source-line, or address-expression destinations on GDB and LLDB
- Several concurrent sessions, addressed explicitly by `session_id`
- Debugger termination and cleanup share a 10-second budget, including recovery
  from failed startup
- Multi-inferior workflows, including fork-follow controls
- LLDB remote targets through `gdb_session_connect` with `debugger="lldb"`
- Go debugging with Delve, including goroutine contexts and deferred attach
- Read/write split by domain instead of one tool per operation
- Workflow helpers: locked batches, capture bundles, run-until-failure campaigns
- Dedicated privileged tools for attaching to processes and calling functions
- `gdb_execute_command` escape hatch for anything without a structured tool
- Reverse execution over an [rr](https://rr-project.org/) recording
- Language-aware debugging: the pretty-printers and helpers a toolchain ships
  for GDB (Rust, Go, kernel scripts) are reachable, and startup says so when GDB
  refuses to load one

## How It Works

GDB/MI is the protocol family IDEs such as VS Code and CLion use to drive GDB.
The LLDB backend instead compiles a small bridge against the stable SB API and
loads the selected `liblldb` at runtime. Delve connects through a streaming RPC
socket and needs no native bridge. All three feed the same domain tools, so
callers see one contract.

- strict JSON-schema validation for every tool input
- explicit success/error envelopes for automation clients
- inspection results group context, location, and returned ranges; frame records
  include a typed source line when the debugger provides one
- tagged debugger data and console output where a command has backend-specific results
- stable, domain-oriented tool names
- structured session snapshots instead of parsed terminal output

Debugger records preserve additional fields and their original keys, including
hyphenated GDB/MI keys. A field the server cannot decode returns a
`protocol_error` for that operation; invalid caller input returns
`validation_error`. Unfamiliar debugger error messages keep their text and use
the fallback `error` code. These failures do not end an otherwise live session.
Unexpected server implementation failures return `internal_error` and are
logged. A failed startup cleans up its debugger resources and does not publish
a session id.

Workflow batches return `requested_steps` and the completed `steps`. Each step
contains the complete tool envelope under `result`; read its `status`, `action`,
and error `code` there. The CLI exits nonzero if any step returned an error.
Each step captures its new stop event even when the retained history is full.
Invalid batch or campaign setup steps report every field error with its step path.

Collection results expose their arrays directly; use the array length for the
number returned. Memory reads report `requested_bytes` and `captured_bytes` so
partial reads remain explicit.

## Requirements

- Python 3.10 or newer
- GDB 7.1 or newer on `PATH` (GDB with Python support is recommended, so
  toolchain pretty-printers can load)
- Optional: LLDB 4.0.1 or newer with headers for the native bridge; a C++17
  compiler is used to build it on first use
- Optional: Delve on `PATH`, or selected through `DLV_PATH` / `debugger_path`;
  compatibility tests cover v1.20.2, v1.24.2, and v1.27.2 with matching Go toolchains
- Optional: `rr` for reverse debugging (GDB), `rust-gdb` or similar wrappers for
  language-specific pretty-printers

## Install

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath

git clone https://github.com/chitao1234/gdb-mcp.git
cd gdb-mcp
pipx install .
```

That puts `gdb-mcp-server` and `gdb-mcp-client` on your `PATH`.

Virtual environments, editable installs, Windows notes, and MCP client
configuration for clients other than Claude Desktop are covered in
[INSTALL.md](INSTALL.md).

## Quick Start

### Run It As An MCP Server

`gdb-mcp-server` speaks MCP over stdio, which is what most clients expect:

```json
{
  "mcpServers": {
    "gdb": {
      "command": "gdb-mcp-server"
    }
  }
}
```

Typical Claude Desktop configuration paths:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### Pick A Transport

stdio is the default and the right choice for local clients. Use streamable HTTP
when a client wants an endpoint instead of a spawned process:

```bash
gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

The MCP endpoint is then `http://127.0.0.1:8000/mcp`. Keep it on loopback, or set
a bearer token when it is reachable by anything else (see
[Remote Access](#remote-access-and-authentication)).

### Drive It From The CLI

`gdb-mcp-client` connects to an MCP endpoint and gives the tools a debugger-like
front end. When you do not pass `--server-url`, it starts a background server for
the current project (the nearest `.git` or `.hg` root, otherwise the working
directory), remembers it in a per-user cookie, and reuses it for later commands. Debugging
commands use the session you started most recently unless `--session-id` says
otherwise:

```bash
gdb-mcp-client start ./build/app     # start a session and make it the default
gdb-mcp-client break add main
gdb-mcp-client run
gdb-mcp-client bt
gdb-mcp-client locals
gdb-mcp-client until 60
gdb-mcp-client continue
gdb-mcp-client exec "info files"     # anything without a dedicated command
gdb-mcp-client stop
```

The command set covers sessions, execution, breakpoints and watchpoints, threads
and frames, locals/registers/memory/disassembly, batches, campaigns, and capture
bundles. `gdb-mcp-client --help` lists it; `--json` prints raw response
envelopes. `gdb-mcp-client session current` prints this project's default session
and `session use <id>` switches it; stopping that session clears the default. The
[gdb-mcp-cli skill](skills/gdb-mcp-cli/SKILL.md) is the guided tour for scripted
and agent-driven use.

### Debug Go With Delve

```bash
go build -gcflags='all=-N -l' -o ./build/app .
gdb-mcp-client start ./build/app --debugger delve --debugger-path /opt/delve/dlv
gdb-mcp-client break add main.main
gdb-mcp-client run
gdb-mcp-client threads
gdb-mcp-client bt
gdb-mcp-client locals
gdb-mcp-client stop
```

For attach, start an empty session, then call `attach PID`. The empty session
stores configuration without spawning `dlv`; attach starts the selected Delve
binary against that PID. Stopping an attached session leaves the target alive.

```bash
gdb-mcp-client start --debugger delve
gdb-mcp-client attach 12345
gdb-mcp-client bt
gdb-mcp-client continue --wait-until acknowledged
gdb-mcp-client interrupt
gdb-mcp-client stop
```

For Delve, thread IDs in the tools are goroutine IDs. Each session can select a
different `dlv` binary. `gdb_session_connect` also accepts external Delve RPC
servers over TCP or Unix sockets where the selected release supports them.
Execution timeouts leave the command active; `interrupt`, `wait-stop`, and
status queries remain available. Delve has no terminal-command RPC, so use the
structured tools instead of `exec` or `init_commands`.

## Where To Go Next

- [TOOLS.md](TOOLS.md) — the tool contract: every tool, action, request field,
  response field, and error code
- [skills/debug-with-gdb-mcp](skills/debug-with-gdb-mcp/SKILL.md) — how to work a
  real debugging problem through the MCP tools (playbooks for crashes, hangs,
  core dumps, attach, rr replay, and more)
- [skills/gdb-mcp-cli](skills/gdb-mcp-cli/SKILL.md) — the same work through
  `gdb-mcp-client`

## Operating Notes

### Background Servers

Servers started by `gdb-mcp-client` bind `127.0.0.1` on an ephemeral port and
require a bearer token that only the client cookie holds. They exit after 15
minutes without requests or active debug sessions; set
`GDB_MCP_DAEMON_IDLE_SEC=0` to disable that. A server you start by hand never
idle-exits.

```bash
gdb-mcp-client daemon status
gdb-mcp-client daemon stop            # refuses while debug sessions are live
gdb-mcp-client daemon stop --force
```

Sessions live in the server process, so stopping the background server ends the
debug sessions it hosts.

### Remote Access And Authentication

Set a token on the server and hand the same token to the client. The client also
passes it to background servers it spawns, so they never carry it on a command
line:

```bash
export GDB_MCP_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
gdb-mcp-server --transport streamable-http --port 8000
gdb-mcp-client --server-url http://127.0.0.1:8000/mcp status
```

`--auth-token` on the server and `--server-url` / `GDB_MCP_SERVER_URL` on the
client cover the same ground without environment variables.

### Logging

Server logs go to stderr, which keeps stdout clean for stdio transport.
`GDB_MCP_LOG_LEVEL=DEBUG` adds the GDB/MI traffic, which is the fastest way to
see what the server actually asked GDB.

## Troubleshooting

- **`gdb` not found** — check `which gdb` and `gdb --version`, then point
  `GDB_PATH` at the binary you want.
- **A call times out while the program is running** — GDB is usually busy because
  the inferior is still running. Check the session state, wait for a stop, or
  interrupt the inferior; the
  [debug-with-gdb-mcp skill](skills/debug-with-gdb-mcp/SKILL.md) shows the exact
  calls.
- **Breakpoints or locals look wrong** — read the top-level `warnings` array
  returned by `gdb_session_start`. Missing debug symbols mean the target was not built with
  `-g`; a refused auto-load script means the language's GDB helpers stayed
  inactive.
- **The background server will not start** — the client prints the tail of the
  daemon log; the full log is under the state directory.

More installation and platform troubleshooting: [INSTALL.md](INSTALL.md).

## Development

```bash
.venv/bin/black --check src tests
.venv/bin/ruff check src tests
.venv/bin/mypy src tests
.venv/bin/pytest -q     # includes tests/integration/, which needs gdb and g++
git diff --check
```

See [AGENTS.md](AGENTS.md) for repository conventions.

## License

MIT

## References

- [GDB Machine Interface (MI)](https://sourceware.org/gdb/current/onlinedocs/gdb/GDB_002fMI.html)
- [pygdbmi](https://github.com/cs01/pygdbmi)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [rr](https://rr-project.org/)

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GDB_PATH` | `gdb` | GDB binary to spawn. `gdb_session_start.debugger_path` overrides it per session. |
| `DLV_PATH` | `dlv` | Delve binary to spawn. `debugger_path` overrides it per session. |
| `LLDB_PREFIX` / `LLDB_PATH` | discovered | LLDB installation used by `debugger="lldb"`. |
| `LLDB_LIBRARY` | discovered | Explicit `liblldb` path or directory for the LLDB backend. |
| `LLDB_BRIDGE_CXX` | `c++`/`g++`/`clang++` | C++ compiler used to build the LLDB SB API bridge and its layers. |
| `LLDB_BRIDGE_EXECUTABLE` | unset | Prebuilt LLDB bridge executable. |
| `LLDB_BRIDGE_DIR` | unset | Directory containing a prebuilt bridge and layers. |
| `LLDB_BRIDGE_LAYER_DIR` | bridge directory | Override the directory searched for LLDB API layers. |
| `LLDB_BRIDGE_CACHE` | platform cache dir | Where built LLDB bridge artifacts are cached. |
| `RR_PATH` | `rr` | `rr` binary used by `rr_replay` sessions. |
| `GDB_MCP_LOG_LEVEL` | `INFO` | Server log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `GDB_MCP_AUTH_TOKEN` | unset | Bearer token for the streamable HTTP transport, used by the server and sent by the client. |
| `GDB_MCP_SERVER_URL` | unset | Endpoint for `gdb-mcp-client`; same as `--server-url`. |
| `GDB_MCP_STATE_DIR` | platform cache dir | Where the client keeps background-server cookies and logs (`%LOCALAPPDATA%`, `~/Library/Caches`, `$XDG_CACHE_HOME`, or `~/.cache`, under `gdb-mcp/`). |
| `GDB_MCP_DAEMON_IDLE_SEC` | `900` | Idle seconds before a client-started background server exits; `0` disables idle exit. |
| `GDB_MCP_CLIENT_READ_TIMEOUT_SEC` | `1800` | How long the client waits for one tool response. Raise it for legitimately long calls, such as a large run-until-failure campaign. |
