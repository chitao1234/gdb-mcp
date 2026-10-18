# GDB MCP Server

`gdb-mcp-server` gives AI assistants and other MCP clients structured access to
GDB. It talks to GDB over GDB/MI and answers every call with machine-readable
state — session status, stop reasons, breakpoints, frames, variables, registers,
memory — instead of terminal text that a client has to screen-scrape.

`gdb-mcp-client` is the companion CLI. It drives the same tools from a shell and
keeps one background server per project, so a debug session survives between
commands and reads like an ordinary debugger.

- Structured GDB control with machine-readable success and error payloads
- Several concurrent sessions, addressed explicitly by `session_id`
- Multi-inferior workflows, including fork-follow controls
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
This server sits on top of it and adds:

- strict JSON-schema validation for every tool input
- explicit success/error envelopes for automation clients
- stable, domain-oriented tool names
- structured session snapshots instead of parsed terminal output

## Requirements

- Python 3.10 or newer
- GDB installed and available on `PATH` (GDB with Python support is recommended,
  so toolchain pretty-printers can load)
- Optional: `rr` for reverse debugging, `rust-gdb` or similar wrappers for
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

## Where To Go Next

- [TOOLS.md](TOOLS.md) — the tool contract: every tool, action, request field,
  response field, and error code
- [skills/debug-with-gdb-mcp](skills/debug-with-gdb-mcp/SKILL.md) — how to work a
  real debugging problem through the MCP tools (playbooks for crashes, hangs,
  core dumps, attach, rr replay, and more)
- [skills/gdb-mcp-cli](skills/gdb-mcp-cli/SKILL.md) — the same work through
  `gdb-mcp-client`
- [examples/USAGE_GUIDE.md](examples/USAGE_GUIDE.md) — end-to-end example
  prompts against a sample program

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
- **Breakpoints or locals look wrong** — read the `warnings` array returned by
  `gdb_session_start`. Missing debug symbols mean the target was not built with
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
| `GDB_PATH` | `gdb` | GDB binary to spawn. `gdb_session_start.gdb_path` overrides it per session. |
| `RR_PATH` | `rr` | `rr` binary used by `rr_replay` sessions. |
| `GDB_MCP_LOG_LEVEL` | `INFO` | Server log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `GDB_MCP_AUTH_TOKEN` | unset | Bearer token for the streamable HTTP transport, used by the server and sent by the client. |
| `GDB_MCP_SERVER_URL` | unset | Endpoint for `gdb-mcp-client`; same as `--server-url`. |
| `GDB_MCP_STATE_DIR` | platform cache dir | Where the client keeps background-server cookies and logs (`%LOCALAPPDATA%`, `~/Library/Caches`, `$XDG_CACHE_HOME`, or `~/.cache`, under `gdb-mcp/`). |
| `GDB_MCP_DAEMON_IDLE_SEC` | `900` | Idle seconds before a client-started background server exits; `0` disables idle exit. |
| `GDB_MCP_CLIENT_READ_TIMEOUT_SEC` | `1800` | How long the client waits for one tool response. Raise it for legitimately long calls, such as a large run-until-failure campaign. |
