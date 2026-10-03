"""Handlers for dedicated command tools."""

from __future__ import annotations


from ...session.service import SessionService
from ..schemas import (
    AttachProcessArgs,
    CallFunctionArgs,
    ExecuteCommandArgs,
)
from .common import ToolResult


def _handle_execute_command(session: SessionService, args: ExecuteCommandArgs) -> ToolResult:
    return session.execute_command(command=args.command, timeout_sec=args.timeout_sec)


def _handle_attach_process(session: SessionService, args: AttachProcessArgs) -> ToolResult:
    return session.attach_process(pid=args.pid, timeout_sec=args.timeout_sec)


def _handle_call_function(session: SessionService, args: CallFunctionArgs) -> ToolResult:
    return session.call_function(function_call=args.function_call, timeout_sec=args.timeout_sec)
