"""Request models for dedicated (non-action) tools."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import (
    Field,
    field_validator,
)

from ...contracts import DEBUGGER_GDB, DebuggerName
from .common import (
    SessionId,
    StrictArgsModel,
)

# GDB options that would fight this server's own target wiring or replace the MI
# interpreter it speaks, plus the value-taking options whose operand is allowed
# to be a bare word (for example ``-iex "set X"``).
_GDB_ARG_CONFLICTING_OPTIONS = frozenset(
    {"-i", "--interpreter", "--args", "-c", "--core", "-p", "--pid", "--attach"}
)
_GDB_ARG_CONFLICTING_PREFIXES = ("--interpreter=", "--core=", "--pid=")
_GDB_ARG_CONFLICTING_REASONS = {
    "-i": "would replace the MI interpreter this server speaks",
    "--interpreter": "would replace the MI interpreter this server speaks",
    "--args": "conflicts with the 'program' and 'args' parameters",
    "-c": "conflicts with the 'core' parameter",
    "--core": "conflicts with the 'core' parameter",
    "-p": "conflicts with gdb_attach_process",
    "--pid": "conflicts with gdb_attach_process",
    "--attach": "conflicts with gdb_attach_process",
}
_GDB_ARG_VALUE_OPTIONS = frozenset(
    {
        "-iex",
        "--init-eval-command",
        "-ix",
        "--init-command",
        "-ex",
        "--eval-command",
        "-x",
        "--command",
        "-d",
        "--directory",
        "-cd",
        "--cwd",
        "-s",
        "-symbols",
        "--symbols",
        "-tty",
        "--tty",
    }
)


def _gdb_args_problems(values: Sequence[str]) -> list[str]:
    """Return one message per ``gdb_args`` entry this server cannot pass through."""

    problems: list[str] = []
    previous = ""
    for index, raw in enumerate(values):
        entry = raw.strip()
        if not entry:
            problems.append(f"gdb_args[{index}] must not be empty")
        elif entry in _GDB_ARG_CONFLICTING_OPTIONS:
            problems.append(f"gdb_args[{index}] {entry!r} {_GDB_ARG_CONFLICTING_REASONS[entry]}")
        elif entry.startswith(_GDB_ARG_CONFLICTING_PREFIXES):
            option = entry.split("=", 1)[0]
            problems.append(f"gdb_args[{index}] {entry!r} {_GDB_ARG_CONFLICTING_REASONS[option]}")
        elif not entry.startswith("-") and previous not in _GDB_ARG_VALUE_OPTIONS:
            problems.append(
                f"gdb_args[{index}] {entry!r} is a bare argument: pass it as the operand of "
                "a preceding option such as '-iex' or '-d', or use 'program'/'args'"
            )
        previous = entry
    return problems


class StartSessionArgs(StrictArgsModel):
    program: str | None = Field(None, description="Path to executable to debug")
    debugger: DebuggerName = Field(
        DEBUGGER_GDB,
        description=(
            "Debugger backend to drive: 'gdb' (GDB/MI) or 'lldb' (native SB API). "
            "The value is validated against the implemented set; operations a backend "
            "cannot perform answer with code='unsupported_operation'."
        ),
    )
    args: list[str] | str | None = Field(
        None,
        description=(
            "Command-line arguments for the program. "
            "Accepts either an explicit argv list or one shell-style string. "
            "Cannot be combined with core."
        ),
    )
    init_commands: list[str] | None = Field(
        None,
        description=(
            "Debugger commands to run on startup after environment variables have been "
            "applied (for example 'core-file /path/to/core' or 'settings set target.sysroot /path')"
        ),
    )
    env: dict[str, str] | None = Field(
        None,
        description=(
            "Environment variables to set for the debugged program before init_commands run "
            "(e.g., {'LD_LIBRARY_PATH': '/custom/libs'})"
        ),
    )
    gdb_path: str | None = Field(
        None,
        description=(
            "Path to the GDB executable (default: from GDB_PATH env var or 'gdb'). "
            "With debugger='lldb', path to an LLDB binary or installation instead."
        ),
    )
    gdb_args: list[str] | None = Field(
        None,
        description=(
            "Extra GDB command-line arguments, passed verbatim and inserted before the "
            "program is loaded. Use this for settings that must apply before GDB reads the "
            "target, for example '--directory=<dir>' and "
            "'-iex add-auto-load-safe-path <dir>' to let GDB run the scripts a binary embeds "
            "for its language (Rust pretty-printers, the Go runtime helpers, a kernel's "
            "vmlinux-gdb.py). With 'rr_replay' the entries are forwarded to the debugger rr "
            "starts."
        ),
    )

    @field_validator("gdb_args")
    @classmethod
    def validate_gdb_args(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        problems = _gdb_args_problems(value)
        if problems:
            raise ValueError("; ".join(problems))
        return value

    working_dir: str | None = Field(
        None,
        description=(
            "Working directory to use when starting GDB. "
            "Use this when debugging programs that need to be run from a specific directory, "
            "or when the program expects to find files (config, data, etc.) relative to its working directory. "
            "GDB will be started in this directory. "
            "Example: If debugging a server that loads config from './config.json', set working_dir to the server's directory."
        ),
    )
    rr_replay: str | None = Field(
        None,
        description=(
            "GDB only. Path to an rr trace directory (or 'latest-trace') to replay "
            "instead of launching a program. Starts 'rr replay --interpreter=mi', which "
            "brings reverse execution. The trace owns the target, so a 'program' here is "
            "ignored with a warning; 'args' and 'core' are rejected. LLDB rejects this "
            "field: connect LLDB to an externally started 'rr replay -s' server with "
            "gdb_session_connect, but that gives up rr's reverse stepping. Use debugger='gdb' "
            "when reverse execution matters."
        ),
    )
    core: str | None = Field(
        None,
        description=(
            "Path to core dump file for post-mortem debugging. "
            "When specified, GDB is started with --core flag which properly initializes symbol resolution. "
            "Cannot be combined with args. "
            "IMPORTANT: When using a sysroot with core dumps, set sysroot AFTER the core is loaded "
            "(either via this parameter or core-file command) for symbols to resolve correctly."
        ),
    )


class ConnectSessionArgs(StrictArgsModel):
    endpoint: str = Field(
        ...,
        description=(
            "Endpoint of an externally owned target. For 'gdb', a GDB/MI stream: "
            "'tcp://host:port', 'unix:///path/to/socket', or 'pty:///dev/pts/N'. "
            "For 'lldb', a remote stub URL accepted by SBTarget::ConnectRemote, "
            "for example 'connect://host:port'. An 'rr replay -s PORT TRACE' server is "
            "also such a stub; connecting LLDB to it gives up rr's reverse stepping."
        ),
    )
    debugger: DebuggerName = Field(
        DEBUGGER_GDB,
        description="Debugger backend that owns the endpoint: 'gdb' or 'lldb'.",
    )
    program: str | None = Field(
        None,
        description=(
            "Executable to associate with an LLDB remote target. The GDB/MI endpoint "
            "path rejects it because the external process already owns its target."
        ),
    )
    gdb_path: str | None = Field(
        None,
        description=(
            "LLDB binary or installation path for the LLDB remote path. With GDB this is "
            "ignored because the external GDB/MI process is already running."
        ),
    )
    init_commands: list[str] | None = Field(
        None,
        description="Debugger commands to run immediately after connecting",
    )

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("endpoint is required")
        return normalized


class ExecuteCommandArgs(StrictArgsModel):
    session_id: SessionId
    command: str = Field(..., description="GDB command to execute")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class AttachProcessArgs(StrictArgsModel):
    session_id: SessionId
    pid: int = Field(..., gt=0, description="PID of the process to attach to")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class CallFunctionArgs(StrictArgsModel):
    session_id: SessionId
    function_call: str = Field(
        ...,
        description="Function call expression (e.g., 'printf(\"hello\\n\")' or 'my_func(arg1, arg2)')",
    )
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")
