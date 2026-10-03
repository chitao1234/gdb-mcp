"""Map Pydantic validation failures onto the documented MCP error envelope."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from ..domain import OperationError
from .schema_normalizer import public_input_schema

_TAG_PROPERTY_NAMES = ("action", "kind")


def build_validation_error(
    exc: ValidationError,
    *,
    tool_name: str,
    model: type[BaseModel] | None,
) -> OperationError:
    """Translate one Pydantic failure into an actionable ``validation_error``.

    Error locations inside discriminated unions contain the chosen tag (for
    example ``('create', 'breakpoint', 'code', 'location')``). The published
    flattened schema is used to strip those tags so clients see stable
    property paths such as ``breakpoint.location``.
    """

    schema: Any = public_input_schema(model) if model is not None else {}
    field_errors: list[dict[str, str]] = []
    action: str | None = None

    for error in exc.errors(include_url=False):
        path, tags = _clean_location(schema, tuple(error.get("loc") or ()))
        if action is None:
            action = next((value for parent, _, value in tags if not parent), None)
        field, issue, message = _describe(error, path, tags)
        field_errors.append({"field": field, "issue": issue, "message": message})

    if len(field_errors) == 1:
        message = field_errors[0]["message"]
    else:
        message = f"{field_errors[0]['message']} (+{len(field_errors) - 1} more validation errors)"

    details: dict[str, Any] = {"tool": tool_name, "field_errors": field_errors}
    if action is not None:
        details["action"] = action
    return OperationError(message=message, code="validation_error", details=details)


def _clean_location(
    schema: Any,
    loc: tuple[Any, ...],
) -> tuple[list[str], list[tuple[tuple[str, ...], str, str]]]:
    """Return the public property path plus union tags dropped on the way."""

    path: list[str] = []
    tags: list[tuple[tuple[str, ...], str, str]] = []
    node = schema

    for segment in loc:
        if isinstance(segment, int):
            if path:
                path[-1] = f"{path[-1]}[{segment}]"
            node = node.get("items", {}) if isinstance(node, dict) else {}
            continue
        if not isinstance(segment, str):
            continue
        properties = node.get("properties") if isinstance(node, dict) else None
        if isinstance(properties, dict) and segment in properties:
            path.append(segment)
            node = properties[segment]
            continue
        tag_property = _matching_tag_property(node, segment)
        if tag_property is not None:
            tags.append((tuple(path), tag_property, segment))
            continue
        path.append(segment)
        node = {}

    return path, tags


def _matching_tag_property(node: Any, segment: str) -> str | None:
    """Return the discriminator property whose enum contains this loc segment."""

    if not isinstance(node, dict):
        return None
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return None
    for name in _TAG_PROPERTY_NAMES:
        prop = properties.get(name)
        if isinstance(prop, dict):
            enum = prop.get("enum")
            if isinstance(enum, list) and segment in enum:
                return name
    return None


def _describe(
    error: Mapping[str, Any],
    path: list[str],
    tags: list[tuple[tuple[str, ...], str, str]],
) -> tuple[str, str, str]:
    """Return the public field, issue kind, and message for one error entry."""

    error_type = str(error.get("type", ""))
    context = error.get("ctx") or {}
    field = ".".join(path)

    if error_type == "missing":
        target = field or "(root)"
        return target, "missing", f"{target} is required{_tag_suffix(path, tags)}"

    if error_type == "extra_forbidden":
        return (
            field or "(root)",
            "not_allowed",
            f"{field} is not allowed" if field else "Unknown field",
        )

    if error_type == "union_tag_not_found":
        tag_property = _context_text(context, "discriminator")
        target = (
            f"{field}.{tag_property}"
            if field and tag_property
            else (tag_property or field or "(root)")
        )
        return target, "missing", f"{target} is required"

    if error_type == "union_tag_invalid":
        discriminator = _context_text(context, "discriminator") or field or "value"
        tag = context.get("tag")
        expected = context.get("expected_tags")
        if isinstance(tag, str):
            message = f"Unknown {discriminator} {tag!r}"
            if isinstance(expected, str) and expected:
                message = f"{message}; expected one of {expected}"
            return discriminator, "invalid", message
        return discriminator, "invalid", str(error.get("msg", "Invalid value"))

    if error_type == "value_error":
        raw_error = context.get("error")
        message = (
            str(raw_error) if raw_error is not None else str(error.get("msg", "Invalid value"))
        )
    else:
        message = str(error.get("msg", "Invalid value"))

    return field or "(root)", "invalid", f"{field}: {message}" if field else message


def _tag_suffix(path: list[str], tags: list[tuple[tuple[str, ...], str, str]]) -> str:
    """Describe the deepest union tag that selected this error path."""

    best: tuple[tuple[str, ...], str, str] | None = None
    for entry in tags:
        parent = entry[0]
        if len(parent) < len(path) and tuple(path[: len(parent)]) == parent:
            if best is None or len(parent) > len(best[0]):
                best = entry
    if best is None:
        return ""
    return f" for {best[1]}={best[2]}"


def _context_text(context: dict[str, Any], key: str) -> str | None:
    value = context.get(key)
    if isinstance(value, str):
        return value.strip("'")
    return None
