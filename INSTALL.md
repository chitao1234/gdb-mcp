# Installing gdb-mcp

One package installs two commands:

- `gdb-mcp-server` — the MCP server that drives GDB
- `gdb-mcp-client` — the CLI client that talks to a server and gives the tools a
  debugger-like front end

Both come from the same distribution, so installing once covers both. For what
the server and client do, see [README.md](README.md); for the tool contract, see
[TOOLS.md](TOOLS.md).

## Requirements

- **Python 3.10 or newer** (`python3 --version`)
- **GDB on `PATH`** (`gdb --version`). Install it if needed:

  ```bash
  sudo apt install gdb        # Debian/Ubuntu
  sudo dnf install gdb        # Fedora/RHEL
  brew install gdb            # macOS
  ```

  A GDB built with Python support is recommended, so toolchain pretty-printers
  (Rust, Go, kernel scripts) can load.

- **Optional:** LLDB 4.0.1 or newer and a C++14 compiler for
  `debugger="lldb"`. The server builds a small SB API bridge on first use; set
  `LLDB_PREFIX` or `LLDB_LIBRARY` when the installation is not discovered
  automatically. The LLDB headers are not required for a prebuilt bridge.
- **Optional:** `rr` for reverse debugging, and a wrapper such as `rust-gdb` if
  you want Rust's pretty-printers.

## Install With pipx (Recommended)

pipx installs the commands in an isolated environment and puts them on your
`PATH`, with no virtualenv to manage.

**1. Install pipx:**

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath        # restart your shell afterwards
```

Or use a package manager: `sudo apt install pipx`, `brew install pipx`,
`sudo dnf install pipx`.

**2. Get the source and install:**

```bash
git clone https://github.com/chitao1234/gdb-mcp.git
cd gdb-mcp
pipx install .
```

Developers who want the checkout to be the live install use `pipx install -e .`
instead.

**3. Verify:** see [Verifying The Installation](#verifying-the-installation).

**Update:** `cd gdb-mcp && git pull && pipx install --force .`

**Uninstall:** `pipx uninstall gdb-mcp-server`

## Install With pip

Use a virtual environment so the server's dependencies stay out of your system
Python:

```bash
git clone https://github.com/chitao1234/gdb-mcp.git
cd gdb-mcp
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install .
```

MCP clients that spawn the server need an absolute path rather than an activated
shell. Point the client at the environment's interpreter:

```json
{
  "mcpServers": {
    "gdb": {
      "command": "/absolute/path/to/gdb-mcp/venv/bin/python",
      "args": ["-m", "gdb_mcp"]
    }
  }
}
```

On Windows the command is
`C:\\absolute\\path\\to\\gdb-mcp\\venv\\Scripts\\python.exe`.

A user-level install without a virtual environment also works, but the commands
land in the user script directory (`~/.local/bin` on Linux), which must be on
`PATH`:

```bash
python3 -m pip install --user .
```

## Install With uv

```bash
git clone https://github.com/chitao1234/gdb-mcp.git
cd gdb-mcp
uv tool install .          # same idea as pipx: isolated, on PATH
```

For a development environment in the checkout, `uv sync --extra dev` builds
`.venv` from `uv.lock`.

## Editable Or Development Install

To work on gdb-mcp itself, install the checkout in editable mode with the test
and lint tooling:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`.venv/bin/pytest -q` then runs the suite (the integration tests need `gdb` and
`g++`). The full check list and repository conventions live in
[AGENTS.md](AGENTS.md).

## Configure Your MCP Client

### stdio (default)

Most clients spawn the server themselves. With a pipx install the command is
simply on `PATH`:

```json
{
  "mcpServers": {
    "gdb": {
      "command": "gdb-mcp-server"
    }
  }
}
```

Claude Desktop configuration paths:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Any client that can run a command over stdio takes the same shape; with a
virtualenv install, use the absolute interpreter path and `["-m", "gdb_mcp"]` as
shown above.

### Streamable HTTP

When the client expects an HTTP endpoint instead of a spawned process, start the
server yourself:

```bash
gdb-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Keep it on loopback, or set a
bearer token when it is reachable by anything else:

```bash
export GDB_MCP_AUTH_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
gdb-mcp-server --transport streamable-http --port 8000
```

### Using the CLI client instead

`gdb-mcp-client` needs no separate install and no pre-started server: when you do
not pass `--server-url`, it starts a background server for the current project
and reuses it for later commands.

```bash
gdb-mcp-client start ./build/app
gdb-mcp-client break add main
gdb-mcp-client run
gdb-mcp-client bt
gdb-mcp-client stop
```

The [gdb-mcp-cli skill](skills/gdb-mcp-cli/SKILL.md) covers the command set,
default-session handling, and background-server management.

## Verifying The Installation

**Commands exist:**

```bash
which gdb-mcp-server gdb-mcp-client      # Windows: where ...
gdb-mcp-client --version
```

**The server starts** (Ctrl+C to stop; stdio mode waits for a client):

```bash
gdb-mcp-server
```

You should see a line like:

```
2026-01-01 12:00:00,000 - gdb_mcp.mcp.app - INFO - GDB MCP Server starting...
```

**The client reaches a server end to end:**

```bash
gdb-mcp-client status
```

With no session yet it reports an empty list, which still proves the client
started a background server and completed a round trip.

**GDB is usable:** `gdb --version`, and `which gdb` for the binary the server
will spawn.

## Troubleshooting

### `gdb-mcp-server: command not found`

The script directory is not on `PATH`. For pipx run `pipx ensurepath` and restart
your shell; for a user install add `~/.local/bin` (Linux) or
`~/Library/Python/3.x/bin` (macOS) to `PATH`.

### `ModuleNotFoundError: No module named 'mcp'` or similar

The install did not finish. Re-run it — `pipx reinstall gdb-mcp-server`, or
`pip install -e .` inside the virtualenv.

### The server warns that it was imported from a `build/lib` path

A stale `build/` tree in the checkout is shadowing `src/`. Remove it
(`rm -rf build/`) or install in editable mode.

### `GDB not found` when a session starts

GDB is missing or not on the `PATH` of the process that runs the server. Install
it (above) or point `GDB_PATH` at the binary you want.

### The MCP client shows no GDB tools

Check that the JSON configuration is valid, that the command path is absolute
when a virtualenv is involved, that backslashes are escaped on Windows
(`C:\\path\\...`), and restart the client afterwards. Client-side logs usually
list the reason a server failed to launch.

### The background server will not start

`gdb-mcp-client` prints the tail of the daemon log; the full log is under the
state directory (`GDB_MCP_STATE_DIR`, default the platform cache directory).
`gdb-mcp-client daemon status` shows what is registered, and
`gdb-mcp-client daemon stop` removes it.

### A call times out

GDB is usually busy because the inferior is still running. See the troubleshooting
section in [README.md](README.md).

## Updating

```bash
cd gdb-mcp
git pull
pipx install --force .        # or: .venv/bin/pip install -e ".[dev]"
```

Restart any MCP client that spawns the server, and stop background servers
(`gdb-mcp-client daemon stop`) so the next command picks up the new code.

## Uninstalling

```bash
pipx uninstall gdb-mcp-server        # pipx or uv tool install
rm -rf /path/to/gdb-mcp              # virtualenv or editable install
```

Then remove the server entry from your MCP client configuration and restart it.
Background servers and cookies live under the state directory and can be removed
with `gdb-mcp-client daemon stop` followed by deleting that directory.

## Getting Help

- [README.md](README.md) — overview, quick start, operating notes, environment
  variables
- [TOOLS.md](TOOLS.md) — the tool contract
- [skills/debug-with-gdb-mcp](skills/debug-with-gdb-mcp/SKILL.md) and
  [skills/gdb-mcp-cli](skills/gdb-mcp-cli/SKILL.md) — worked workflows
- [AGENTS.md](AGENTS.md) — repository conventions and the check list
