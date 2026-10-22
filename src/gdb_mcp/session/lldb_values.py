"""Consumed-field decoders shared by LLDB operation collaborators."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain import FrameRecord, OperationError, ThreadRecord
from ..domain.errors import DebuggerDecodeError
from .lldb_bridge_types import BridgeError


def operation_error(exc: BridgeError) -> OperationError:
    return OperationError(message=str(exc), code=exc.code)


def mapping(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise DebuggerDecodeError(f"LLDB value must be a mapping, got {type(value).__name__}")


def mappings(value: object) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DebuggerDecodeError(
            f"LLDB value must be a list of mappings, got {type(value).__name__}"
        )
    records: list[Mapping[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise DebuggerDecodeError(
                f"LLDB list item {index} must be a mapping, got {type(item).__name__}"
            )
        records.append(item)
    return records


def text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise DebuggerDecodeError(f"LLDB field {key!r} must be text, got {type(value).__name__}")


def integer(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise DebuggerDecodeError(f"LLDB field {key!r} must be an integer, got {type(value).__name__}")


def frame(payload: object) -> FrameRecord | None:
    fields = mapping(payload)
    return FrameRecord.from_payload(fields) if fields else None


def thread(payload: object) -> ThreadRecord | None:
    fields = dict(mapping(payload))
    for key in ("id", "target-id"):
        value = fields.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            fields[key] = str(value)
    return ThreadRecord.from_payload(fields) if fields else None
