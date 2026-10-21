"""Strict narrowing helpers for untrusted GDB/MI payloads.

GDB/MI hands the server loosely-typed structures: nested ``dict`` / ``list``
trees of ``str`` / ``int`` values. This module is the single place where that
untrusted ``object`` data is narrowed and coerced into concrete types.

Every other module works with those concrete types; keeping the
``object``/``isinstance`` logic here stops it from leaking across the domain,
session, and transport layers, and gives one well-tested place to audit.

The mapping helpers return the original mapping unchanged (no copy), so the
records they produce are read-only *views* over the transport's parsed payload;
callers must not mutate them. Use :func:`as_optional_mapping` when the
difference between a missing value and an empty mapping matters.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast, get_args

from ..contracts import ExecutionState
from .stop_kinds import StopKind

logger = logging.getLogger(__name__)

_EXECUTION_STATES: frozenset[str] = frozenset(get_args(ExecutionState))
_STOP_KINDS: frozenset[str] = frozenset(get_args(StopKind))


def as_mapping(value: object) -> Mapping[str, object]:
    """Narrow untrusted data into a read-only mapping, or an empty mapping.

    Returns the mapping unchanged when it is one, otherwise an empty mapping so
    callers can read fields without guarding every access. The result is a view,
    not a copy: treat it as read-only.
    """

    return value if isinstance(value, Mapping) else {}


def as_optional_mapping(value: object) -> Mapping[str, object] | None:
    """Narrow untrusted data into a mapping, or ``None`` when it is not one.

    Unlike :func:`as_mapping`, a missing or mistyped value is preserved as
    ``None`` so callers can distinguish "absent" from "empty". The result is a
    read-only view.
    """

    return value if isinstance(value, Mapping) else None


def as_record_list(value: object) -> list[Mapping[str, object]]:
    """Return the list payload entries that are themselves mappings.

    Non-mapping entries are dropped so callers get a clean record list, but a
    non-list value or a dropped entry is logged: GDB returning anything other
    than a list of records here usually means malformed output worth knowing
    about. A missing (``None``) value is expected and not logged.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("Expected a list of MI records, got %s", type(value).__name__)
        return []

    records = [item for item in value if isinstance(item, Mapping)]
    if len(records) != len(value):
        logger.warning(
            "Dropped %d non-mapping MI record(s) from a list of %d",
            len(value) - len(records),
            len(value),
        )
    return records


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


def as_stop_kind(value: object) -> StopKind | None:
    """Parse a canonical stop-kind string, or ``None`` when unrecognized."""

    text = as_text(value)
    if text is not None and text in _STOP_KINDS:
        return cast(StopKind, text)
    return None
