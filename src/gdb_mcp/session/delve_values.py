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


def integer(value: object, default: int | None = None) -> int:
    if value is None and default is not None:
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


def boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise DebuggerDecodeError(f"Delve value must be a boolean, got {type(value).__name__}")


def frame_record(payload: object, level: int = 0) -> FrameRecord:
    location = mapping(payload)
    function = mapping(location.get("function"))
    values = dict(location)
    values["level"] = level
    address = optional_integer(location.get("pc"))
    if address is not None:
        values["addr"] = hex(address)
    if "name" in function:
        values["func"] = function["name"]
    if "file" in location:
        values["fullname"] = location["file"]
    return FrameRecord.from_payload(values)


def variable_value(payload: Mapping[str, object]) -> str:
    unreadable = text(payload.get("unreadable"))
    if unreadable:
        return f"<unreadable: {unreadable}>"
    kind = integer(payload.get("kind"), 0)
    value = text(payload.get("value"))
    children = mappings(payload.get("children"))
    if kind == 24:  # reflect.String
        return json.dumps(value, ensure_ascii=False)
    if value:
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
        if kind in {17, 23} and integer(payload.get("len"), 0) > len(children):
            parts.append("...")
        return text(payload.get("type")) + "{" + ", ".join(parts) + "}"
    if kind in {18, 19, 20, 21, 22, 23, 26} and not integer(payload.get("base"), 0):
        return "nil"
    return text(payload.get("type")) + "{}"


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
    raw_addresses = payload.get("addrs")
    addresses: list[int] = []
    if raw_addresses is not None:
        if not isinstance(raw_addresses, list):
            raise DebuggerDecodeError(
                f"Delve breakpoint addresses must be a list, got {type(raw_addresses).__name__}"
            )
        addresses = [integer(address) for address in raw_addresses]
    if not addresses:
        address = optional_integer(payload.get("addr"))
        if address is not None:
            addresses.append(address)
    watch = text(payload.get("WatchExpr"))
    breakpoint_id = integer(payload.get("id"))
    values.update(
        {
            "number": str(breakpoint_id),
            "type": "watchpoint" if watch else "breakpoint",
            "temporary": temporary,
            "locations": [],
        }
    )
    if "disabled" in payload:
        values["enabled"] = not boolean(payload["disabled"])
    if "Cond" in payload:
        values["condition"] = payload["Cond"]
    if "totalHitCount" in payload:
        values["times"] = payload["totalHitCount"]
    if addresses:
        values["verified"] = any(addresses)
        location = {
            canonical: payload[native]
            for canonical, native in (
                ("fullname", "file"),
                ("line", "line"),
                ("func", "functionName"),
            )
            if native in payload
        }
        values["locations"] = [{**location, "addr": hex(address)} for address in addresses]
    if watch:
        values["watch"] = {
            "access": {1: "read", 2: "write", 3: "access"}.get(
                integer(payload.get("WatchType"), 0)
            ),
            "spec": watch,
        }
    return BreakpointRecord.from_payload(values)
