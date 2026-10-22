"""Helpers for working with typed session-layer operation results."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain import CommandExecutionInfo, OperationSuccess
from ..transport.mi_parser import extract_mi_result_payload


def command_result_mapping(
    result: OperationSuccess[CommandExecutionInfo],
) -> Mapping[str, object]:
    """Return the raw MI result mapping from one typed command result."""

    payload = extract_mi_result_payload(result.value.backend_data.data)
    if payload is None:
        return {}
    return payload
