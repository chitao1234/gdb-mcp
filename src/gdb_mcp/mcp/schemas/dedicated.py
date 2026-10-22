"""Request models for dedicated (non-action) tools."""

from __future__ import annotations

from pydantic import (
    Field,
    ValidationInfo,
    field_validator,
)

from ...contracts import DEBUGGER_GDB, DebuggerName
from ...session.debugger_args import debugger_args_problems
from .common import (
    SessionId,
    StrictArgsModel,
)


class StartSessionArgs(StrictArgsModel):
    program: str | None = Field(None, description="Path to executable to debug")
    debugger: DebuggerName = Field(
        DEBUGGER_GDB,
        description=(
            "Debugger backend: 'gdb' (GDB/MI), 'lldb' (native SB API), or 'delve' (JSON-RPC v2). "
            "An empty Delve session defers spawning dlv until gdb_attach_process. "
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
    debugger_path: str | None = Field(
        None,
        description=(
            "Path to the debugger executable or installation. For debugger='gdb', the "
            "default is from GDB_PATH or 'gdb'; for debugger='lldb', provide an LLDB "
            "binary or installation instead. Delve uses this path, DLV_PATH, or 'dlv'."
        ),
    )
    debugger_args: list[str] | None = Field(
        None,
        description=(
            "Extra startup arguments for the selected debugger, passed verbatim before the "
            "program is loaded. GDB/MI sessions validate and forward GDB options such as "
            "'--directory=<dir>' and '-iex add-auto-load-safe-path <dir>'; with 'rr_replay' "
            "they are forwarded to the debugger rr starts. The native LLDB backend currently "
            "rejects this field. Delve accepts flags that do not override session wiring."
        ),
    )

    @field_validator("debugger_args")
    @classmethod
    def validate_debugger_args(
        cls, value: list[str] | None, info: ValidationInfo
    ) -> list[str] | None:
        if value is None:
            return None
        problems = debugger_args_problems(value, info.data.get("debugger", DEBUGGER_GDB))
        if problems:
            raise ValueError("; ".join(problems))
        return value

    working_dir: str | None = Field(
        None,
        description=(
            "Working directory to use when starting the debugger. "
            "Use this when debugging programs that need to be run from a specific directory, "
            "or when the program expects to find files (config, data, etc.) relative to its working directory. "
            "The debugger will be started in this directory. "
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
            "also such a stub; connecting LLDB to it gives up rr's reverse stepping. "
            "For 'delve', use tcp://host:port or unix:///path for a headless Delve RPC server."
        ),
    )
    debugger: DebuggerName = Field(
        DEBUGGER_GDB,
        description="Debugger backend that owns the endpoint: 'gdb', 'lldb', or 'delve'.",
    )
    program: str | None = Field(
        None,
        description=(
            "Executable to associate with an LLDB remote target. The GDB/MI endpoint "
            "path rejects it because the external process already owns its target."
        ),
    )
    debugger_path: str | None = Field(
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
    command: str = Field(..., description="Debugger CLI or machine-interface command to execute")
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
