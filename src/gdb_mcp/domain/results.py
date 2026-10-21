"""Typed result containers for internal operations."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Generic, TypeVar, cast

from pydantic import BaseModel

from .models import JsonObject, JsonValue, StructuredPayload

PayloadT_co = TypeVar("PayloadT_co", covariant=True)


@dataclass(slots=True, frozen=True)
class OperationSuccess(Generic[PayloadT_co]):
    """Successful internal operation result."""

    value: PayloadT_co
    warnings: tuple[str, ...] = ()
    action: str | None = None


@dataclass(slots=True, frozen=True)
class OperationError:
    """Failed internal operation result."""

    message: str
    code: str = "error"
    fatal: bool = False
    details: StructuredPayload = field(default_factory=dict)


OperationResult = OperationSuccess[PayloadT_co] | OperationError
_RESERVED_ERROR_KEYS = {"status", "code", "message", "fatal"}
_ERROR_TOP_LEVEL_DETAIL_KEYS = {"action", "tool"}


def payload_to_mapping(value: object) -> JsonValue:
    """Convert typed payload objects into JSON-serializable builtin structures.

    Any mapping (not just ``dict``) is copied into a plain object, and values
    with no JSON representation raise ``TypeError`` here rather than during the
    later ``json.dumps`` in the transport layer.
    """

    if is_dataclass(value) and not isinstance(value, type):
        return {info.name: payload_to_mapping(getattr(value, info.name)) for info in fields(value)}
    if isinstance(value, BaseModel):
        return payload_to_mapping(
            value.model_dump(by_alias=True, exclude_unset=True, warnings=False)
        )
    if isinstance(value, Mapping):
        payload: JsonObject = {}
        for key, item in value.items():
            payload[str(key)] = payload_to_mapping(item)
        return payload
    if isinstance(value, list | tuple):
        return [payload_to_mapping(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return cast(JsonValue, value)
    raise TypeError(f"Value of type {type(value).__name__} is not JSON-serializable")


def result_to_mapping(result: OperationResult[object]) -> StructuredPayload:
    """Convert a typed operation result into its flattened payload mapping."""

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
        "code": result.code,
        "message": result.message,
    }
    if result.fatal:
        error_payload["fatal"] = True
    details_payload = payload_to_mapping(result.details)
    if isinstance(details_payload, dict):
        nested_details: StructuredPayload = {}
        for key, value in details_payload.items():
            if key in _ERROR_TOP_LEVEL_DETAIL_KEYS:
                error_payload[key] = value
            elif key not in _RESERVED_ERROR_KEYS:
                nested_details[key] = value
        if nested_details:
            error_payload["details"] = nested_details
    return error_payload


def result_to_envelope(result: OperationResult[object]) -> StructuredPayload:
    """Convert an operation result into the uniform public tool envelope."""

    if isinstance(result, OperationError):
        return result_to_mapping(result)

    serialized_value = payload_to_mapping(result.value)
    if not isinstance(serialized_value, dict):
        raise TypeError("Successful tool results must contain an object payload")
    payload: StructuredPayload = {"status": "success"}
    if result.action is not None:
        payload["action"] = result.action
    payload["result"] = serialized_value
    if result.warnings:
        payload["warnings"] = list(result.warnings)
    return payload
