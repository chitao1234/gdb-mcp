"""Normalize generated JSON Schemas into the form this server publishes.

A published schema may use ``anyOf``, ``$ref``, and ``$defs``, but not ``oneOf``,
``allOf``, or OpenAPI's ``discriminator``. Pydantic emits ``oneOf`` (with a
discriminator) for a tagged union, which offers the same alternatives as
``anyOf``, so this module renames it and drops the discriminator; everything else
is published exactly as generated. ``const`` is spelled as a single-value
``enum``, which clients understand everywhere.

Keeping the union alternatives intact is what makes the published schema
faithful: each alternative states its own requirements instead of being merged
into one object whose conditional rules have to be described in prose.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import overload

from pydantic import BaseModel

Schema = dict[str, object]


def normalize_schema(schema: Schema) -> Schema:
    """Return the published form of one generated schema.

    MCP requires a tool schema to describe an object, and a tagged union of
    payload objects does not say that on its own, so the root declares it.
    """

    published = _normalize_node(schema)
    definitions = published.get("$defs")
    defs = definitions if isinstance(definitions, Mapping) else {}
    if "type" not in published and _describes_object(published, defs):
        published["type"] = "object"
    return published


@overload
def freeze_schema(value: Mapping[str, object]) -> Mapping[str, object]: ...


@overload
def freeze_schema(value: object) -> object: ...


def freeze_schema(value: object) -> object:
    """Return a deeply immutable copy so the cached schema cannot be mutated."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_schema(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze_schema(item) for item in value)
    return value


@overload
def thaw_schema(value: Mapping[str, object]) -> Schema: ...


@overload
def thaw_schema(value: object) -> object: ...


def thaw_schema(value: object) -> object:
    """Return a mutable, JSON-serializable copy of a frozen schema."""

    if isinstance(value, Mapping):
        return {str(key): thaw_schema(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_schema(item) for item in value]
    return value


@functools.cache
def _cached_input_schema(model: type[BaseModel]) -> Mapping[str, object]:
    """Return the immutable published schema for a model, computed once per process."""

    return freeze_schema(normalize_schema(model.model_json_schema()))


def public_input_schema(
    model: type[BaseModel],
    *,
    examples: Sequence[dict[str, object]] | None = None,
) -> Schema:
    """Return a fresh, published JSON Schema for one MCP tool input model.

    The cached schema is immutable and shared, so each call returns an
    independent copy that callers may freely mutate.
    """

    schema = thaw_schema(_cached_input_schema(model))
    if examples:
        schema["examples"] = [dict(example) for example in examples]
    return schema


def _describes_object(node: object, defs: Mapping[str, object]) -> bool:
    """Return whether one schema node describes an object, directly or by union."""

    current = node if isinstance(node, Mapping) else {}
    ref = current.get("$ref")
    if isinstance(ref, str):
        return _describes_object(defs.get(ref.rsplit("/", 1)[-1]), defs)
    if current.get("type") == "object" or "properties" in current:
        return True
    alternatives = current.get("anyOf")
    return (
        isinstance(alternatives, list)
        and bool(alternatives)
        and all(_describes_object(alternative, defs) for alternative in alternatives)
    )


@overload
def _normalize_node(node: Mapping[str, object]) -> Schema: ...


@overload
def _normalize_node(node: object) -> object: ...


def _normalize_node(node: object) -> object:
    """Rewrite one schema node into its published form, recursively."""

    if isinstance(node, list):
        return [_normalize_node(item) for item in node]
    if not isinstance(node, Mapping):
        return node

    published: Schema = {}
    for key, value in node.items():
        if not isinstance(key, str) or key == "discriminator":
            continue
        if key == "oneOf":
            published["anyOf"] = _normalize_node(value)
            continue
        if key == "const":
            published.setdefault("enum", [value])
            continue
        published[key] = _normalize_node(value)
    return published
