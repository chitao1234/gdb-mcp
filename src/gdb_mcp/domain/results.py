"""Typed result containers for internal operations."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

PayloadT = TypeVar("PayloadT")


@dataclass(slots=True, frozen=True)
class OperationSuccess(Generic[PayloadT]):
    """Successful internal operation result."""

    value: PayloadT
    warnings: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class OperationError:
    """Failed internal operation result."""

    message: str
    code: str = "error"
    fatal: bool = False
    details: dict[str, Any] = field(default_factory=dict)


OperationResult = OperationSuccess[PayloadT] | OperationError


def from_legacy_result(payload: Mapping[str, Any]) -> OperationResult[dict[str, Any]]:
    """Convert the current dict-shaped operation result into a typed result."""

    payload_dict = dict(payload)
    if payload_dict.get("status") == "error":
        details = {
            key: value
            for key, value in payload_dict.items()
            if key not in {"status", "message", "fatal"}
        }
        return OperationError(
            message=str(payload_dict.get("message", "Unknown error")),
            fatal=bool(payload_dict.get("fatal", False)),
            details=details,
        )

    return OperationSuccess(payload_dict)
