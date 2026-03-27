"""Helpers for working with typed session-layer operation results."""

from __future__ import annotations

from ..domain import CommandExecutionInfo, OperationSuccess, StructuredPayload


def command_result_payload(result: OperationSuccess[CommandExecutionInfo]) -> StructuredPayload:
    """Extract the MI command-result container from a successful execution result."""

    return result.value.result or {}
