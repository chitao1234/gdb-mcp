"""Shared parsing helpers for the MCP CLI client."""

from __future__ import annotations

import argparse
from copy import deepcopy
from collections.abc import Iterable
from typing import cast

from pydantic import BaseModel, ValidationError


class CliUsageError(ValueError):
    """Raised when CLI flag combinations are structurally invalid."""


class AppendTaggedValue(argparse.Action):
    """Collect tagged CLI events while preserving the originating option."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser

        events = getattr(namespace, self.dest, None)
        if events is None:
            events = []
            setattr(namespace, self.dest, events)

        events.append((option_string or self.option_strings[0], values))


def key_value_entry(text: str) -> tuple[str, str]:
    """Parse one KEY=VALUE argument."""

    if "=" not in text:
        raise argparse.ArgumentTypeError("Expected KEY=VALUE")

    key, value = text.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("Expected non-empty KEY in KEY=VALUE")

    return key, value


def collapse_key_value_entries(entries: list[tuple[str, str]]) -> dict[str, str] | None:
    """Collapse repeated KEY=VALUE arguments into one mapping."""

    if not entries:
        return None

    return {key: value for key, value in entries}


def coerce_scalar(text: str) -> object:
    """Preserve dotted-assignment values as raw strings until schema validation."""

    return text


def dotted_assignment(text: str) -> tuple[str, object]:
    """Parse one PATH=VALUE dotted assignment."""

    if "=" not in text:
        raise argparse.ArgumentTypeError("Expected PATH=VALUE")

    path, raw_value = text.split("=", 1)
    if not path:
        raise argparse.ArgumentTypeError("Expected non-empty PATH in PATH=VALUE")
    if any(part == "" for part in path.split(".")):
        raise argparse.ArgumentTypeError(
            "Expected PATH with non-empty dotted segments in PATH=VALUE"
        )

    return path, coerce_scalar(raw_value)


def assign_dotted_value(target: dict[str, object], path: str, value: object) -> None:
    """Assign one dotted path into a nested mapping."""

    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        elif not isinstance(child, dict):
            raise CliUsageError(
                f"Conflicting dotted assignment for {path}: segment {part!r} already set"
            )
        current = child

    leaf_key = parts[-1]
    existing = current.get(leaf_key)
    if existing is None:
        current[leaf_key] = value
        return
    if isinstance(existing, dict):
        raise CliUsageError(
            f"Conflicting dotted assignment for {path}: path already set to a mapping"
        )
    if isinstance(existing, list):
        existing.append(value)
        return
    current[leaf_key] = [existing, value]


def add_boolean_flag(
    parser: argparse.ArgumentParser,
    name: str,
    *,
    default: bool,
    help_text: str,
    suppress_default: bool = False,
) -> None:
    """Add a paired boolean flag using argparse's optional boolean action."""

    parser.add_argument(
        f"--{name.replace('_', '-')}",
        dest=name,
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS if suppress_default else default,
        help=help_text,
    )


def validate_model_payload(model: type[BaseModel], payload: dict[str, object]) -> dict[str, object]:
    """Validate and normalize one CLI-built payload using the shared schema model."""

    validated = model.model_validate(payload)
    dumped = validated.model_dump(mode="python", exclude_none=True)
    return cast(dict[str, object], dumped)


def _coerce_single_item_list_path(
    payload: dict[str, object],
    path: tuple[object, ...],
    *,
    action: str | None,
) -> bool:
    normalized_path: list[str] = []
    for position, segment in enumerate(path):
        if not isinstance(segment, str):
            return False
        if position == 0 and action is not None and segment == action:
            continue
        normalized_path.append(segment)

    if not normalized_path:
        return False

    current: object = payload
    for segment in normalized_path[:-1]:
        if not isinstance(current, dict):
            return False
        current = current.get(segment)
        if current is None:
            return False

    if not isinstance(current, dict):
        return False

    leaf = normalized_path[-1]
    existing = current.get(leaf)
    if existing is None or isinstance(existing, list):
        return False

    current[leaf] = [existing]
    return True


def validate_model_payload_with_list_coercion(
    model: type[BaseModel],
    payload: dict[str, object],
) -> dict[str, object]:
    """Validate one payload, retrying by widening scalars into single-item lists when needed."""

    candidate = cast(dict[str, object], deepcopy(payload))

    while True:
        try:
            return validate_model_payload(model, candidate)
        except ValidationError as exc:
            errors = exc.errors()
            if not errors or any(error.get("type") != "list_type" for error in errors):
                raise

            action_value = candidate.get("action")
            action: str | None = action_value if isinstance(action_value, str) else None
            changed = False
            for error in errors:
                location = cast(tuple[object, ...], tuple(error.get("loc", ())))
                changed = (
                    _coerce_single_item_list_path(candidate, location, action=action) or changed
                )

            if not changed:
                raise


def format_validation_error(exc: ValidationError) -> str:
    """Render a compact CLI-facing summary for one schema-validation failure."""

    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        if location:
            messages.append(f"{location}: {error['msg']}")
        else:
            messages.append(str(error["msg"]))
    return "; ".join(messages)


def format_cli_flag(name: str) -> str:
    """Render one namespace field name as a CLI flag."""

    return f"--{name.replace('_', '-')}"


def ensure_action_fields(
    namespace: argparse.Namespace,
    *,
    action: str,
    tracked_fields: Iterable[str],
    allowed_fields: Iterable[str],
) -> None:
    """Reject explicit flags that are incompatible with the selected action."""

    allowed = set(allowed_fields)
    unexpected = [
        format_cli_flag(field_name)
        for field_name in tracked_fields
        if hasattr(namespace, field_name) and field_name not in allowed
    ]
    if unexpected:
        joined = ", ".join(sorted(unexpected))
        raise CliUsageError(f"{joined} not valid with --action {action}")
