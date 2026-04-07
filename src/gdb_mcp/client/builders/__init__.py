"""Typed payload builders for MCP CLI client tools."""

from .breakpoint import build_breakpoint_manage_payload, build_breakpoint_query_payload
from .context import build_context_manage_payload, build_context_query_payload
from .execution import build_execution_manage_payload
from .inferior import build_inferior_manage_payload, build_inferior_query_payload
from .inspect import build_inspect_query_payload
from .session import build_session_query_payload, build_session_start_payload

__all__ = [
    "build_breakpoint_manage_payload",
    "build_breakpoint_query_payload",
    "build_context_manage_payload",
    "build_context_query_payload",
    "build_execution_manage_payload",
    "build_inferior_manage_payload",
    "build_inferior_query_payload",
    "build_inspect_query_payload",
    "build_session_query_payload",
    "build_session_start_payload",
]
