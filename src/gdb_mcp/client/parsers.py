"""Shared parsing helpers for the MCP CLI client."""

from __future__ import annotations

import argparse
from typing import cast

from pydantic import BaseModel, ValidationError


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


def add_boolean_flag(
    parser: argparse.ArgumentParser,
    name: str,
    *,
    default: bool,
    help_text: str,
) -> None:
    """Add a paired boolean flag using argparse's optional boolean action."""

    parser.add_argument(
        f"--{name.replace('_', '-')}",
        dest=name,
        action=argparse.BooleanOptionalAction,
        default=default,
        help=help_text,
    )


def validate_model_payload(model: type[BaseModel], payload: dict[str, object]) -> dict[str, object]:
    """Validate and normalize one CLI-built payload using the shared schema model."""

    validated = model.model_validate(payload)
    dumped = validated.model_dump(mode="python", exclude_none=True)
    return cast(dict[str, object], dumped)


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
