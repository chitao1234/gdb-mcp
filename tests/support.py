"""Typed accessors for the untyped JSON payloads that tests assert against.

Tool payloads are `dict[str, object]`, so one index yields `object` and a second
index or an attribute access is a type error. These helpers make the shape
assertion explicit: they fail with the value that was wrong instead of passing
`object` around, and they keep the narrowing in one place.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TypeVar, cast

from gdb_mcp.domain import OperationError, OperationResult, OperationSuccess
from gdb_mcp.session.gdb_backend import GdbMiBackend
from gdb_mcp.session.service import SessionService
from gdb_mcp.transport.protocols import GdbControllerProtocol

PayloadT = TypeVar("PayloadT")


def mapping(value: object) -> dict[str, object]:
    """Return a JSON object, or fail with what was found instead."""

    assert isinstance(value, dict), f"expected an object, got {type(value).__name__}"
    return value


def json_object(text: str) -> dict[str, object]:
    """Decode one JSON object, rejecting arrays and scalars."""

    decoded: object = json.loads(text)
    return mapping(decoded)


def json_array(text: str) -> list[object]:
    """Decode one JSON array, rejecting objects and scalars."""

    decoded: object = json.loads(text)
    return sequence(decoded)


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

    assert isinstance(result, OperationSuccess), result
    return result.value


def failed(result: OperationResult[object]) -> OperationError:
    """Assert a failed operation and return its error."""

    assert isinstance(result, OperationError), result
    return result


_UNSUPPORTED_SCHEMA_KEYS = ("oneOf", "allOf", "discriminator")


def unsupported_schema_keys(node: object, path: str = "$") -> list[str]:
    """Return every JSON-Schema construct a published schema may not contain."""

    hits: list[str] = []
    if isinstance(node, dict):
        entries: Mapping[str, object] = node
        for key, value in entries.items():
            if key in _UNSUPPORTED_SCHEMA_KEYS:
                hits.append(f"{path}.{key}")
            hits.extend(unsupported_schema_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        items: list[object] = node
        for index, value in enumerate(items):
            hits.extend(unsupported_schema_keys(value, f"{path}[{index}]"))
    return hits


def gdb_backend(service: SessionService) -> GdbMiBackend:
    """Return the GDB backend behind a default session service.

    Tests that patch a command runner or a lifecycle probe are exercising the GDB
    backend, so they reach it through this helper instead of the service facade.
    """

    backend = service.backend
    assert isinstance(backend, GdbMiBackend), type(backend).__name__
    return backend


def attach_controller(service: SessionService, controller: object) -> None:
    """Attach a controller double, as a started GDB backend would."""

    gdb_backend(service).transport.controller = cast("GdbControllerProtocol", controller)
    service.runtime.controller = controller
