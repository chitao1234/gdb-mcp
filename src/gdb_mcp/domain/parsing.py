"""Strict narrowing helpers for untrusted GDB/MI payloads.

GDB/MI hands the server loosely-typed structures: nested ``dict`` / ``list``
trees of ``str`` / ``int`` values. This module is the single place where that
untrusted ``object`` data is narrowed and coerced into concrete types.

Every other module works with those concrete types; keeping the
``object``/``isinstance`` logic here stops it from leaking across the domain,
session, and transport layers, and gives one well-tested place to audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast, get_args

from .models import ExecutionState, FrameRecord, StructuredPayload

_EXECUTION_STATES: frozenset[str] = frozenset(get_args(ExecutionState))


def as_payload(value: object) -> StructuredPayload:
    """Narrow untrusted data into a JSON object payload.

    Returns the mapping unchanged when it is one, otherwise an empty mapping so
    callers can read fields without guarding every access.
    """

    return cast(StructuredPayload, value) if isinstance(value, Mapping) else {}


def as_mapping(value: object) -> Mapping[str, object]:
    """Narrow untrusted data into a mapping, or an empty mapping otherwise."""

    return value if isinstance(value, Mapping) else {}


def as_record_list(value: object) -> list[Mapping[str, object]]:
    """Return the list payload entries that are themselves mappings."""

    return [item for item in as_list(value) if isinstance(item, Mapping)]


def as_optional_payload(value: object) -> StructuredPayload | None:
    """Narrow untrusted data into a JSON object payload, or ``None``."""

    return cast(StructuredPayload, value) if isinstance(value, Mapping) else None


def as_frame_record(value: object) -> FrameRecord | None:
    """Narrow an MI frame payload into the structured frame record type."""

    return value if isinstance(value, Mapping) else None


def as_list(value: object) -> list[object]:
    """Return a shallow copy of a list payload, or an empty list otherwise."""

    return list(value) if isinstance(value, list) else []


def as_text(value: object) -> str | None:
    """Return a string value unchanged, or ``None`` for any other type."""

    return value if isinstance(value, str) else None


def as_text_or_number(value: object) -> str | None:
    """Render a string or integer scalar as text, or ``None`` otherwise.

    GDB sometimes reports the same field as an MI string in one response and as
    a bare number in another; this keeps that normalization in one place.
    """

    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return None


def as_int(value: object) -> int | None:
    """Parse an integer or all-digit string scalar, or ``None`` otherwise."""

    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def as_execution_state(value: object) -> ExecutionState | None:
    """Parse a session execution-state string, or ``None`` when unrecognized."""

    text = as_text(value)
    if text is not None and text in _EXECUTION_STATES:
        return cast(ExecutionState, text)
    return None
