"""Typed result containers for internal operations."""

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
