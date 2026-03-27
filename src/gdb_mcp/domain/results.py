"""Typed result containers for internal operations."""

from dataclasses import asdict, is_dataclass
from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

from .models import JsonObject, JsonValue, StructuredPayload

PayloadT = TypeVar("PayloadT", covariant=True)


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
    details: StructuredPayload = field(default_factory=dict)


OperationResult = OperationSuccess[PayloadT] | OperationError
_RESERVED_ERROR_KEYS = {"status", "message", "fatal"}


def payload_to_mapping(value: object) -> JsonValue:
    """Convert typed payload objects into JSON-serializable builtin structures."""

    if is_dataclass(value) and not isinstance(value, type):
        return payload_to_mapping(asdict(value))
    if isinstance(value, dict):
        payload: JsonObject = {}
        for key, item in value.items():
            payload[str(key)] = payload_to_mapping(item)
        return payload
    if isinstance(value, list):
        return [payload_to_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [payload_to_mapping(item) for item in value]
    return cast(JsonValue, value)


def result_to_mapping(result: OperationResult[object]) -> StructuredPayload:
    """Convert a typed operation result into the external JSON payload shape."""

    if isinstance(result, OperationSuccess):
        serialized_value = payload_to_mapping(result.value)
        if isinstance(serialized_value, dict):
            payload: StructuredPayload = dict(serialized_value)
        else:
            payload = {"value": serialized_value}
        payload.setdefault("status", "success")
        if result.warnings and "warnings" not in payload:
            payload["warnings"] = list(result.warnings)
        return payload

    error_payload: StructuredPayload = {
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
