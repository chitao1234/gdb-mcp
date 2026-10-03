"""Normalize published JSON Schemas for broad MCP client compatibility.

Pydantic emits ``oneOf``/``anyOf`` combinators, ``discriminator`` metadata, and
``$ref``/``$defs`` indirection for discriminated unions, optional fields, and
scalar unions. Many MCP clients implement only a small subset of JSON Schema
and fail to parse tool definitions that contain them. :func:`public_input_schema`
flattens those constructs into plain object and scalar schemas:

* object unions merge into one object whose discriminator property becomes an
  ``enum``; requirements that only hold for some variants are appended to the
  description as ``Conditional requirements: ...`` using dotted paths;
* alternative titles and descriptions are joined rather than dropped;
* ``null`` alternatives are dropped, leaving fields optional via ``required``;
* heterogeneous unions keep their primary (first declared) shape, and the
  request-model descriptions already document shorthand string alternatives;
* ``const`` values become single-value ``enum`` lists, which clients support
  more widely;
* ``$ref``/``$defs`` indirection is inlined.

Runtime validation is unchanged: handlers still validate payloads with the
Pydantic request models, so request semantics remain fully enforced.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

Schema = dict[str, Any]

_MISSING = object()


def public_input_schema(
    model: type[BaseModel],
    *,
    examples: Sequence[dict[str, object]] | None = None,
) -> Schema:
    """Return a client-friendly JSON Schema for one MCP tool input model."""

    schema = normalize_input_schema(model.model_json_schema())
    if examples:
        schema["examples"] = [dict(example) for example in examples]
    return schema


def normalize_input_schema(schema: Schema) -> Schema:
    """Flatten Pydantic JSON Schema combinators into plain nested schemas."""

    defs_value = schema.get("$defs")
    defs: dict[str, Any] = defs_value if isinstance(defs_value, dict) else {}
    root: Schema = {key: value for key, value in schema.items() if key != "$defs"}

    normalized: Schema = _normalize_node(root, defs, ())

    pending_refs = _collect_ref_names(normalized)
    if pending_refs:
        kept_defs = _prune_defs(defs, pending_refs)
        normalized["$defs"] = {
            name: _normalize_node(definition, kept_defs, (name,))
            for name, definition in kept_defs.items()
        }
    return normalized


def _normalize_node(node: Any, defs: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """Recursively normalize one schema node, inlining ``$ref`` targets."""

    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        return _resolve_ref(node, ref, defs, stack)

    if "oneOf" in node or "anyOf" in node:
        return _normalize_union(node, defs, stack)

    normalized: Schema = {key: value for key, value in node.items() if key != "discriminator"}

    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            name: _normalize_node(value, defs, stack) for name, value in properties.items()
        }
    for key in ("items", "additionalProperties", "contains"):
        value = normalized.get(key)
        if isinstance(value, dict):
            normalized[key] = _normalize_node(value, defs, stack)
    prefix_items = normalized.get("prefixItems")
    if isinstance(prefix_items, list):
        normalized["prefixItems"] = [_normalize_node(value, defs, stack) for value in prefix_items]

    if "const" in normalized:
        normalized.setdefault("enum", [normalized.pop("const")])

    return normalized


def _resolve_ref(node: Schema, ref: str, defs: dict[str, Any], stack: tuple[str, ...]) -> Any:
    """Inline one ``$ref`` target, falling back to the reference on cycles."""

    name = ref.rsplit("/", 1)[-1]
    target = defs.get(name)
    if target is None or name in stack:
        return dict(node)

    resolved = _normalize_node(target, defs, (*stack, name))
    siblings = {key: value for key, value in node.items() if key != "$ref"}
    if siblings and isinstance(resolved, dict):
        resolved = {**resolved, **siblings}
    return resolved


def _normalize_union(node: Schema, defs: dict[str, Any], stack: tuple[str, ...]) -> Schema:
    """Flatten one union into a single schema without combinators."""

    raw_variants = node.get("oneOf", node.get("anyOf"))
    if not isinstance(raw_variants, list):
        return dict(node)

    wrapper: Schema = {
        key: value for key, value in node.items() if key not in ("oneOf", "anyOf", "discriminator")
    }

    variants: list[Schema] = []
    nullable = False
    for raw_variant in raw_variants:
        variant = _normalize_node(raw_variant, defs, stack)
        if not isinstance(variant, dict):
            continue
        if _is_null_schema(variant):
            nullable = True
            continue
        variants.append(variant)

    if nullable and wrapper.get("default", _MISSING) is None:
        wrapper.pop("default", None)

    if not variants:
        empty: Schema = {"type": "null"} if nullable else {}
        empty.update(wrapper)
        return empty

    if len(variants) == 1:
        merged: Schema = dict(variants[0])
    elif all(_is_object_like(variant) for variant in variants):
        merged = _merge_object_variants(variants)
    else:
        merged = dict(variants[0])

    merged.update(wrapper)

    if len(variants) > 1:
        note = _conditional_requirements_note(node.get("discriminator"), variants)
        if note:
            description = merged.get("description")
            merged["description"] = f"{description} {note}" if description else note

    return merged


def _is_null_schema(schema: Schema) -> bool:
    return schema.get("type") == "null"


def _is_object_like(schema: Schema) -> bool:
    return schema.get("type") == "object" or "properties" in schema


def _merge_object_variants(variants: list[Schema]) -> Schema:
    """Merge alternative object shapes into one object schema."""

    merged: Schema = {"type": "object"}
    properties: dict[str, Any] = {}
    required_order: list[str] = []
    required_sets: list[set[str]] = []

    for variant in variants:
        required_value = variant.get("required")
        required_names = (
            [name for name in required_value if isinstance(name, str)]
            if isinstance(required_value, list)
            else []
        )
        required_sets.append(set(required_names))
        for name in required_names:
            if name not in required_order:
                required_order.append(name)

        variant_properties = variant.get("properties")
        if isinstance(variant_properties, dict):
            for name, prop in variant_properties.items():
                if name in properties:
                    properties[name] = _merge_property_schemas(properties[name], prop)
                else:
                    properties[name] = prop

    if properties:
        merged["properties"] = properties

    common_required = set.intersection(*required_sets) if required_sets else set()
    ordered_common = [name for name in required_order if name in common_required]
    if ordered_common:
        merged["required"] = ordered_common

    if variants and all(variant.get("additionalProperties") is False for variant in variants):
        merged["additionalProperties"] = False

    return merged


def _merge_property_schemas(left: Any, right: Any) -> Any:
    """Merge one property that appears in several alternative object shapes.

    The result must accept every instance either alternative accepts, so
    ``required`` becomes the intersection and ``additionalProperties: false``
    only survives when every alternative forbids extras.
    """

    if left == right:
        return left
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left

    merged = dict(left)

    left_required = left.get("required")
    right_required = right.get("required")
    if isinstance(left_required, list) or isinstance(right_required, list):
        left_names = left_required if isinstance(left_required, list) else []
        right_names = set(right_required) if isinstance(right_required, list) else set()
        common_required = [name for name in left_names if name in right_names]
        if common_required:
            merged["required"] = common_required
        else:
            merged.pop("required", None)

    for key, value in right.items():
        if key == "required":
            continue
        if key == "additionalProperties":
            if not (merged.get(key) is False and value is False):
                merged.pop(key, None)
            continue
        if key in ("title", "description"):
            # Combine alternative labels/descriptions instead of dropping any.
            merged[key] = _merge_text(merged.get(key), value)
            continue
        if key not in merged:
            merged[key] = value
            continue
        if key == "enum" and isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = _dedupe([*merged[key], *value])
        elif key == "properties" and isinstance(merged[key], dict) and isinstance(value, dict):
            properties = dict(merged[key])
            for name, prop in value.items():
                properties[name] = (
                    _merge_property_schemas(properties[name], prop) if name in properties else prop
                )
            merged[key] = properties
        # Other conflicting keys keep the primary variant's value on purpose:
        # per-variant semantics are carried by the conditional-requirements note.

    return merged


def _dedupe(values: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _merge_text(left: Any, right: Any) -> Any:
    """Join alternative labels or descriptions, skipping repeated text."""

    if not isinstance(right, str) or not right:
        return left
    if not isinstance(left, str) or not left:
        return right
    if left == right:
        return left
    return f"{left} | {right}"


def _conditional_requirements_note(discriminator: Any, variants: list[Schema]) -> str | None:
    """Summarize per-variant required paths that a flat schema cannot express."""

    if not isinstance(discriminator, dict):
        return None
    property_name = discriminator.get("propertyName")
    if not isinstance(property_name, str):
        return None

    variant_paths = [_required_paths(variant) for variant in variants]
    path_sets = [set(paths) for paths in variant_paths]
    common_paths = set.intersection(*path_sets) if path_sets else set()

    clauses: list[str] = []
    for variant, paths in zip(variants, variant_paths):
        conditional = [path for path in paths if path not in common_paths]
        if not conditional:
            continue
        variant_properties = variant.get("properties")
        property_schema = (
            variant_properties.get(property_name) if isinstance(variant_properties, dict) else None
        )
        values = _discriminator_values(property_schema)
        if not values:
            continue
        scope = _discriminator_scope(property_name, values)
        clauses.append(f"{scope} requires {', '.join(conditional)}")

    if not clauses:
        return None
    return "Conditional requirements: " + "; ".join(clauses) + "."


def _required_paths(schema: Schema, prefix: str = "", depth: int = 2) -> list[str]:
    """List required property paths, expanding one level of nested objects."""

    required = schema.get("required")
    if not isinstance(required, list):
        return []

    properties = schema.get("properties")
    paths: list[str] = []
    for name in required:
        if not isinstance(name, str):
            continue
        path = f"{prefix}{name}"
        paths.append(path)
        if depth > 1 and isinstance(properties, dict):
            nested = properties.get(name)
            if isinstance(nested, dict):
                paths.extend(_required_paths(nested, f"{path}.", depth - 1))
    return paths


def _discriminator_values(property_schema: Any) -> list[str]:
    if not isinstance(property_schema, dict):
        return []
    enum = property_schema.get("enum")
    if isinstance(enum, list):
        return [value for value in enum if isinstance(value, str)]
    const = property_schema.get("const")
    if isinstance(const, str):
        return [const]
    return []


def _discriminator_scope(property_name: str, values: list[str]) -> str:
    if len(values) == 1:
        return f"{property_name}='{values[0]}'"
    if len(values) == 2:
        return f"{property_name}='{values[0]}' or {property_name}='{values[1]}'"
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{property_name} in [{rendered}]"


def _collect_ref_names(node: Any) -> set[str]:
    """Collect every ``$ref`` target name reachable in one schema node."""

    if isinstance(node, dict):
        names: set[str] = set()
        ref = node.get("$ref")
        if isinstance(ref, str):
            names.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            names |= _collect_ref_names(value)
        return names
    if isinstance(node, list):
        names = set()
        for item in node:
            names |= _collect_ref_names(item)
        return names
    return set()


def _prune_defs(defs: dict[str, Any], initial_refs: set[str]) -> dict[str, Any]:
    """Keep only definitions transitively reachable from remaining references."""

    kept: set[str] = set()
    pending = list(initial_refs)
    while pending:
        name = pending.pop()
        if name in kept or name not in defs:
            continue
        kept.add(name)
        pending.extend(_collect_ref_names(defs[name]))
    return {name: definition for name, definition in defs.items() if name in kept}
