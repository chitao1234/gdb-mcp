"""Request models for dedicated (non-action) tools."""

from __future__ import annotations

from pydantic import (
    Field,
    field_validator,
)


from .common import (
    SessionId,
    StrictArgsModel,
)


class StartSessionArgs(StrictArgsModel):
    program: str | None = Field(None, description="Path to executable to debug")
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
            "GDB commands to run on startup after environment variables have been applied "
            "(e.g., 'core-file /path/to/core', 'set sysroot /path')"
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
        description="Path to GDB executable (default: from GDB_PATH env var or 'gdb')",
    )
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
            "GDB/MI endpoint of an externally started GDB. Supported forms: "
            "'tcp://host:port', 'unix:///path/to/socket', 'pty:///dev/pts/N'."
        ),
    )
    init_commands: list[str] | None = Field(
        None,
        description="GDB commands to run immediately after connecting",
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
