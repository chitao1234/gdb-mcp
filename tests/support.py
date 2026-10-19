"""Typed accessors for the untyped JSON payloads that tests assert against.

Tool payloads are `dict[str, object]`, so one index yields `object` and a second
index or an attribute access is a type error. These helpers make the shape
assertion explicit: they fail with the value that was wrong instead of passing
`object` around, and they keep the narrowing in one place.
"""

from __future__ import annotations

from typing import TypeVar

from gdb_mcp.domain import OperationError, OperationResult, OperationSuccess

PayloadT = TypeVar("PayloadT")


def mapping(value: object) -> dict[str, object]:
    """Return a JSON object, or fail with what was found instead."""

    assert isinstance(value, dict), f"expected an object, got {type(value).__name__}"
    return value


def sequence(value: object) -> list[object]:
    """Return a JSON array, or fail with what was found instead."""

    assert isinstance(value, list), f"expected an array, got {type(value).__name__}"
    return value


def mapping_list(value: object) -> list[dict[str, object]]:
    """Return an array of JSON objects."""

    return [mapping(item) for item in sequence(value)]


def text(value: object) -> str:
    """Return a JSON string."""

    assert isinstance(value, str), f"expected a string, got {type(value).__name__}"
    return value


def integer(value: object) -> int:
    """Return a JSON integer. Booleans are rejected because they are not counts."""

    assert isinstance(value, int) and not isinstance(
        value, bool
    ), f"expected an integer, got {type(value).__name__}"
    return value


def dig(value: object, *keys: str) -> object:
    """Walk nested objects, reporting the key that was missing."""

    for key in keys:
        current = mapping(value)
        assert key in current, f"missing key {key!r} in {sorted(current)}"
        value = current[key]
    return value


def succeeded(result: OperationResult[PayloadT]) -> PayloadT:
    """Assert a successful operation and return its payload."""

    assert isinstance(result, OperationSuccess), getattr(result, "message", result)
    return result.value


def failed(result: OperationResult[object]) -> OperationError:
    """Assert a failed operation and return its error."""

    assert isinstance(result, OperationError), result
    return result
