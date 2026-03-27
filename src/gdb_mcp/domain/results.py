"""Typed result containers for internal operations."""

from dataclasses import asdict, is_dataclass
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

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
_RESERVED_ERROR_KEYS = {"status", "message", "fatal"}


def payload_to_mapping(value: Any) -> Any:
    """Convert typed payload objects into JSON-serializable builtin structures."""

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(cast(Any, value))
    if isinstance(value, dict):
        return {key: payload_to_mapping(item) for key, item in value.items()}
    if isinstance(value, list):
        return [payload_to_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [payload_to_mapping(item) for item in value]
    return value


def result_to_mapping(result: OperationResult[Any]) -> dict[str, Any]:
    """Convert a typed operation result into the external JSON payload shape."""

    if isinstance(result, OperationSuccess):
        payload = payload_to_mapping(result.value)
        if not isinstance(payload, dict):
            payload = {"value": payload}
        payload.setdefault("status", "success")
        if result.warnings and "warnings" not in payload:
            payload["warnings"] = list(result.warnings)
        return payload

    error_payload: dict[str, Any] = {
        "status": "error",
        "message": result.message,
    }
    if result.fatal:
        error_payload["fatal"] = True
    details_payload = payload_to_mapping(result.details)
    if isinstance(details_payload, dict):
        for key, value in details_payload.items():
            if key not in _RESERVED_ERROR_KEYS:
                error_payload[key] = value
    return error_payload
