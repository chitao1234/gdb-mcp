"""MCP-layer helpers for the GDB MCP server."""

from .app import create_mcp_app, run_stdio_app
from .handlers import dispatch_tool_call
from .runtime import ServerRuntime, create_server_runtime
from .schemas import (
    AttachProcessArgs,
    BreakpointNumberArgs,
    CallFunctionArgs,
    EvaluateExpressionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetRegistersArgs,
    GetVariablesArgs,
    RunArgs,
    SessionIdArgs,
    SetBreakpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
    build_tool_definitions,
)

__all__ = [
    "BreakpointNumberArgs",
    "CallFunctionArgs",
    "AttachProcessArgs",
    "EvaluateExpressionArgs",
    "ExecuteCommandArgs",
    "FrameSelectArgs",
    "GetBacktraceArgs",
    "GetRegistersArgs",
    "GetVariablesArgs",
    "RunArgs",
    "SessionIdArgs",
    "SetBreakpointArgs",
    "StartSessionArgs",
    "ThreadSelectArgs",
    "build_tool_definitions",
    "create_mcp_app",
    "create_server_runtime",
    "dispatch_tool_call",
    "run_stdio_app",
    "ServerRuntime",
]
