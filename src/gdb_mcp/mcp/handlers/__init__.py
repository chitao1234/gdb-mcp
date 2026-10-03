"""Structured MCP tool dispatch for the GDB MCP server."""

from .dispatch import dispatch_tool_call
from .registry import SESSION_TOOL_SPECS

__all__ = ["SESSION_TOOL_SPECS", "dispatch_tool_call"]
