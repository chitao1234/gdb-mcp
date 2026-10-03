"""Base model, shared field aliases, and normalizers for MCP requests."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class StrictArgsModel(BaseModel):
    """Base model for MCP request validation."""

    model_config = ConfigDict(extra="forbid")


SessionId = Annotated[int, Field(gt=0, description="Session ID from gdb_session_start")]


def _coerce_int_like(
    value: object,
    *,
    field_name: str,
    minimum: int,
    allow_none: bool = False,
) -> int | None:
    """Normalize integer-like fields while accepting numeric strings."""

    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field_name} is required")

    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must be an integer")
        if text.startswith(("+", "-")):
            sign = text[0]
            digits = text[1:]
            if not digits.isdigit():
                raise ValueError(f"{field_name} must be an integer")
            parsed = int(f"{sign}{digits}", 10)
        elif text.isdigit():
            parsed = int(text, 10)
        else:
            raise ValueError(f"{field_name} must be an integer")
    else:
        raise ValueError(f"{field_name} must be an integer")

    if parsed < minimum:
        if minimum == 1:
            raise ValueError(f"{field_name} must be > 0")
        raise ValueError(f"{field_name} must be >= {minimum}")
    return parsed


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    """Normalize optional string selectors while rejecting blanks."""

    if value is None:
        return None

    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text
