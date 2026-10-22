"""Normalize Delve API v2 values across debugger releases."""

from __future__ import annotations

import json
from collections.abc import Mapping

from ..domain import BreakpointRecord, FrameRecord, VariableRecord


def mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def mappings(value: object) -> list[dict[str, object]]:
    return (
        [mapping(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def integer(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


def frame_record(payload: object, level: int = 0) -> FrameRecord:
    location = mapping(payload)
    function = mapping(location.get("function"))
    return FrameRecord.from_payload(
        {
            "level": level,
            "addr": hex(integer(location.get("pc"))),
            "func": text(function.get("name")),
            "file": text(location.get("file")),
            "fullname": text(location.get("file")),
            "line": integer(location.get("line")),
        }
    )


def variable_value(payload: Mapping[str, object]) -> str:
    unreadable = text(payload.get("unreadable"))
    if unreadable:
        return f"<unreadable: {unreadable}>"
    kind = integer(payload.get("kind"))
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
        if integer(payload.get("len")) > len(children) and kind in {17, 23}:
            parts.append("...")
        return text(payload.get("type")) + "{" + ", ".join(parts) + "}"
    if kind in {18, 19, 20, 21, 22, 23, 26} and not integer(payload.get("base")):
        return "nil"
    return text(payload.get("type")) + "{}"


def variable_record(payload: Mapping[str, object], *, argument: bool = False) -> VariableRecord:
    values = dict(payload)
    values.update(
        {
            "value": variable_value(payload),
            "address": hex(integer(payload.get("addr"))),
            "children_count": len(mappings(payload.get("children"))),
        }
    )
    if argument:
        values["arg"] = "1"
    return VariableRecord.from_payload(values)


def breakpoint_record(
    payload: Mapping[str, object], *, temporary: bool = False
) -> BreakpointRecord:
    values = dict(payload)
    addresses = payload.get("addrs")
    if not isinstance(addresses, list) or not addresses:
        addresses = [integer(payload.get("addr"))]
    watch = text(payload.get("WatchExpr"))
    values.update(
        {
            "number": str(integer(payload.get("id"))),
            "type": "watchpoint" if watch else "breakpoint",
            "enabled": not bool(payload.get("disabled")),
            "condition": text(payload.get("Cond")) or None,
            "times": integer(payload.get("totalHitCount")),
            "verified": any(integer(address) for address in addresses),
            "temporary": temporary,
            "locations": [
                {
                    "addr": hex(integer(address)),
                    "fullname": text(payload.get("file")),
                    "line": integer(payload.get("line")),
                    "func": text(payload.get("functionName")),
                }
                for address in addresses
            ],
        }
    )
    if watch:
        watch_type = integer(payload.get("WatchType"))
        values["watch"] = {
            "access": {1: "read", 2: "write", 3: "access"}.get(watch_type),
            "spec": watch,
        }
    return BreakpointRecord.from_payload(values)
