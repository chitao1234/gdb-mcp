"""Helpers for working with typed session-layer operation results."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain import OperationSuccess
from .gdb_command import GdbCommandResult


def command_result_mapping(
    result: OperationSuccess[GdbCommandResult],
) -> Mapping[str, object]:
    """Return the raw MI result mapping from one typed command result."""

    payload = result.value.parsed.result
    if payload is None:
        return {}
    return payload
