"""Map Pydantic validation failures onto the documented MCP error envelope."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from ..domain import JsonArray, JsonObject, OperationError, StructuredPayload
from .schema_normalizer import public_input_schema

_TAG_PROPERTY_NAMES = ("action", "kind")


def build_validation_error(
    exc: ValidationError,
    *,
    tool_name: str,
    model: type[BaseModel] | None,
    field_prefix: str = "",
) -> OperationError:
    """Translate one Pydantic failure into an actionable ``validation_error``.

    Error locations inside tagged unions contain the chosen tag (for example
    ``('create', 'breakpoint', 'code', 'location')``). The published schema is
    used to strip those tags so clients see stable property paths such as
    ``breakpoint.location``.
    """

    schema: object = public_input_schema(model) if model is not None else {}
    field_errors: JsonArray = []
    messages: list[str] = []
    action: str | None = None

    for error in exc.errors(include_url=False):
        path, tags = _clean_location(schema, tuple(error.get("loc") or ()))
        if action is None:
            action = next((value for parent, _, value in tags if not parent), None)
        field, issue, message = _describe(error, path, tags)
        if field_prefix:
            field = f"{field_prefix}.{field}" if field != "(root)" else field_prefix
            message = f"{field_prefix}: {message}"
        entry: JsonObject = {"field": field, "issue": issue, "message": message}
        field_errors.append(entry)
        messages.append(message)

    details: StructuredPayload = {"tool": tool_name, "field_errors": field_errors}
    if action is not None:
        details["action"] = action
    return OperationError(
        message="; ".join(messages),
        code="validation_error",
        details=details,
    )


def _clean_location(
    schema: object,
    loc: tuple[object, ...],
) -> tuple[list[str], list[tuple[tuple[str, ...], str, str]]]:
    """Return the public property path plus union tags dropped on the way."""

    root = _as_mapping(schema)
    path: list[str] = []
    tags: list[tuple[tuple[str, ...], str, str]] = []
    node: Mapping[str, object] = root

    for segment in loc:
        if isinstance(segment, int):
            if path:
                path[-1] = f"{path[-1]}[{segment}]"
            node = _deref(root, node.get("items"))
            continue
        if not isinstance(segment, str):
            continue
        node = _deref(root, node)
        properties = node.get("properties")
        if isinstance(properties, Mapping) and segment in properties:
            path.append(segment)
            node = _as_mapping(properties[segment])
            continue
        tag = _matching_tag(root, node, segment)
        if tag is not None:
            tag_property, alternative = tag
            tags.append((tuple(path), tag_property, segment))
            node = alternative
            continue
        path.append(segment)
        node = {}

    return path, tags


def _as_mapping(value: object) -> Mapping[str, object]:
    """Return a schema node as a mapping, or an empty one."""

    return value if isinstance(value, Mapping) else {}


def _deref(root: Mapping[str, object], node: object) -> Mapping[str, object]:
    """Follow one ``$ref`` inside the schema document."""

    current = _as_mapping(node)
    ref = current.get("$ref")
    if isinstance(ref, str):
        defs = root.get("$defs")
        if isinstance(defs, Mapping):
            target = defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(target, Mapping):
                return target
    return current


def _matching_tag(
    root: Mapping[str, object],
    node: Mapping[str, object],
    segment: str,
) -> tuple[str, Mapping[str, object]] | None:
    """Return the tag property and the alternative one loc segment selects."""

    alternatives = node.get("anyOf")
    if not isinstance(alternatives, list):
        return None
    for alternative in alternatives:
        resolved = _deref(root, alternative)
        for name in _TAG_PROPERTY_NAMES:
            properties = resolved.get("properties")
            if not isinstance(properties, Mapping):
                continue
            prop = properties.get(name)
            if not isinstance(prop, Mapping):
                continue
            enum = prop.get("enum")
            if isinstance(enum, list) and segment in enum:
                return name, resolved
    return None


def _describe(
    error: Mapping[str, object],
    path: list[str],
    tags: list[tuple[tuple[str, ...], str, str]],
) -> tuple[str, str, str]:
    """Return the public field, issue kind, and message for one error entry."""

    error_type = str(error.get("type", ""))
    raw_context = error.get("ctx")
    context: Mapping[str, object] = raw_context if isinstance(raw_context, Mapping) else {}
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
        if (
            len(parent) < len(path)
            and tuple(path[: len(parent)]) == parent
            and (best is None or len(parent) > len(best[0]))
        ):
            best = entry
    if best is None:
        return ""
    return f" for {best[1]}={best[2]}"


def _context_text(context: Mapping[str, object], key: str) -> str | None:
    value = context.get(key)
    if isinstance(value, str):
        return value.strip("'")
    return None
