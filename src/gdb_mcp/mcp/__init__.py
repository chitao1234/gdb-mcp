"""MCP-layer helpers for the GDB MCP server."""

from .app import create_mcp_app, run_stdio_app
from .handlers import dispatch_tool_call
from .schemas import (
    BreakpointNumberArgs,
    CallFunctionArgs,
    EvaluateExpressionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetVariablesArgs,
    SessionIdArgs,
    SetBreakpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
    build_tool_definitions,
)

__all__ = [
    "BreakpointNumberArgs",
    "CallFunctionArgs",
    "EvaluateExpressionArgs",
    "ExecuteCommandArgs",
    "FrameSelectArgs",
    "GetBacktraceArgs",
    "GetVariablesArgs",
    "SessionIdArgs",
    "SetBreakpointArgs",
    "StartSessionArgs",
    "ThreadSelectArgs",
    "build_tool_definitions",
    "create_mcp_app",
    "dispatch_tool_call",
    "run_stdio_app",
]
