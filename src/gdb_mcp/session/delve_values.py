"""Normalize Delve API v2 values across debugger releases."""

from __future__ import annotations

import json
from collections.abc import Mapping

from ..domain import BreakpointRecord, FrameRecord, VariableRecord
from ..domain.errors import DebuggerDecodeError


def mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise DebuggerDecodeError(f"Delve value must be a mapping, got {type(value).__name__}")


def mappings(value: object) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DebuggerDecodeError(
            f"Delve value must be a list of mappings, got {type(value).__name__}"
        )
    records: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise DebuggerDecodeError(
                f"Delve list item {index} must be a mapping, got {type(item).__name__}"
            )
        records.append(dict(item))
    return records


def integer(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise DebuggerDecodeError(f"Delve value must be an integer, got {type(value).__name__}")


def optional_integer(value: object) -> int | None:
    """Read an optional Delve integer without manufacturing a value when absent."""

    if value is None:
        return None
    return integer(value)


def text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raise DebuggerDecodeError(f"Delve value must be text, got {type(value).__name__}")


def optional_text(value: object) -> str | None:
    """Read an optional Delve string without manufacturing empty text."""

    if value is None:
        return None
    return text(value)


def frame_record(payload: object, level: int = 0) -> FrameRecord:
    location = mapping(payload)
    function = mapping(location.get("function"))
    values: dict[str, object] = {"level": level}
    address = optional_integer(location.get("pc"))
    if address is not None:
        values["addr"] = hex(address)
    function_name = optional_text(function.get("name"))
    if function_name is not None:
        values["func"] = function_name
    file_name = optional_text(location.get("file"))
    if file_name is not None:
        values.update({"file": file_name, "fullname": file_name})
    line = optional_integer(location.get("line"))
    if line is not None:
        values["line"] = line
    return FrameRecord.from_payload(values)


def variable_value(payload: Mapping[str, object]) -> str:
    unreadable = optional_text(payload.get("unreadable"))
    if unreadable:
        return f"<unreadable: {unreadable}>"
    kind = optional_integer(payload.get("kind"))
    value = optional_text(payload.get("value"))
    children = mappings(payload.get("children"))
    if kind == 24:  # reflect.String
        return json.dumps(value or "", ensure_ascii=False)
    if value is not None and value:
        return value
    if payload.get("onlyAddr"):
        return hex(integer(payload.get("addr")))
    if children:
        if kind == 21:  # reflect.Map alternates keys and values
            parts = [
                f"{variable_value(children[i])}: {variable_value(children[i + 1])}"
                for i in range(0, len(children) - 1, 2)
            ]
        elif kind in {17, 23}:  # reflect.Array, reflect.Slice
            parts = [variable_value(child) for child in children]
        else:
            parts = [f"{text(child.get('name'))}: {variable_value(child)}" for child in children]
        if integer(payload.get("len")) > len(children) and kind in {17, 23}:
            parts.append("...")
        return (optional_text(payload.get("type")) or "") + "{" + ", ".join(parts) + "}"
    if kind in {18, 19, 20, 21, 22, 23, 26} and not (optional_integer(payload.get("base")) or 0):
        return "nil"
    return (optional_text(payload.get("type")) or "") + "{}"


def variable_record(payload: Mapping[str, object], *, argument: bool = False) -> VariableRecord:
    values = dict(payload)
    values["value"] = variable_value(payload)
    address = optional_integer(payload.get("addr"))
    if address is not None:
        values["address"] = hex(address)
    values["children_count"] = len(mappings(payload.get("children")))
    if argument:
        values["arg"] = "1"
    return VariableRecord.from_payload(values)


def breakpoint_record(
    payload: Mapping[str, object], *, temporary: bool = False
) -> BreakpointRecord:
    values = dict(payload)
    addresses = payload.get("addrs")
    if addresses is None:
        address = optional_integer(payload.get("addr"))
        addresses = [] if address is None else [address]
    elif not isinstance(addresses, list):
        raise DebuggerDecodeError(
            f"Delve breakpoint addresses must be a list, got {type(addresses).__name__}"
        )
    else:
        addresses = [integer(address) for address in addresses]
    watch = optional_text(payload.get("WatchExpr"))
    breakpoint_id = integer(payload.get("id"))
    values.update(
        {
            "number": str(breakpoint_id),
            "type": "watchpoint" if watch else "breakpoint",
            "enabled": not bool(payload.get("disabled")),
            "temporary": temporary,
            "locations": [],
        }
    )
    condition = optional_text(payload.get("Cond"))
    if condition is not None:
        values["condition"] = condition
    hit_count = optional_integer(payload.get("totalHitCount"))
    if hit_count is not None:
        values["times"] = hit_count
    if addresses:
        values["verified"] = any(addresses)
        file_name = optional_text(payload.get("file"))
        line = optional_integer(payload.get("line"))
        function_name = optional_text(payload.get("functionName"))
        values["locations"] = [
            {
                "addr": hex(address),
                **({"fullname": file_name} if file_name is not None else {}),
                **({"line": line} if line is not None else {}),
                **({"func": function_name} if function_name is not None else {}),
            }
            for address in addresses
        ]
    if watch:
        watch_type = optional_integer(payload.get("WatchType"))
        values["watch"] = {
            "access": (
                {1: "read", 2: "write", 3: "access"}.get(watch_type)
                if watch_type is not None
                else None
            ),
            "spec": watch,
        }
    return BreakpointRecord.from_payload(values)
