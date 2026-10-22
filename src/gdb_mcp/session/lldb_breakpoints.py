"""LLDB breakpoint and watchpoint operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..domain import (
    BreakpointInfo,
    BreakpointListInfo,
    BreakpointRecord,
    CatchpointType,
    OperationError,
    OperationSuccess,
    SessionMessage,
    WatchpointAccessType,
)
from ..domain.errors import DebuggerDecodeError
from .lldb_bridge_types import BridgeError
from .lldb_values import mappings, operation_error, text

_LLDB_CATCHPOINTS = frozenset({"throw", "rethrow", "catch"})


def parse_breakpoint_location(location: str) -> tuple[str, dict[str, object]]:
    text = location.strip()
    if text.startswith("*"):
        return "address", {"address": text[1:]}
    if ":" in text:
        file, _, tail = text.rpartition(":")
        if file and tail.isdigit():
            return "location", {"file": file, "line": int(tail)}
    return "function", {"name": text}


class LldbRequest(Protocol):
    def _request(self, operation: str, **payload: object) -> Mapping[str, object]: ...


class LldbBreakpoints:
    """Own LLDB breakpoint numbers, watchpoint metadata, and inventory."""

    def __init__(self, request: LldbRequest) -> None:
        self._bridge = request
        # Public breakpoint numbers are one namespace. Native LLDB breakpoint
        # and watchpoint ids are separate, so each public number records the
        # native kind and id.
        self._public_native: dict[int, tuple[str, int]] = {}
        self._native_public: dict[tuple[str, int], int] = {}
        self._origins: dict[int, str] = {}
        self._watch_specs: dict[int, tuple[str | None, WatchpointAccessType | None]] = {}
        self._catchpoints: set[int] = set()
        self._next_public = 1

    def public_number(self, native_key: tuple[str, int], default: int) -> int:
        return self._native_public.get(native_key, default)

    def _native_number(self, payload: Mapping[str, object]) -> int:
        number = text(payload, "number")
        if number is None:
            raise DebuggerDecodeError("LLDB breakpoint response has no number")
        try:
            return int(number)
        except ValueError as exc:
            raise DebuggerDecodeError(
                f"LLDB breakpoint number must be an integer: {number!r}"
            ) from exc

    def _public_for_native(self, kind: str, native: int) -> int:
        key = (kind, native)
        existing = self._native_public.get(key)
        if existing is not None:
            return existing
        public = self._next_public
        self._next_public += 1
        self._native_public[key] = public
        self._public_native[public] = key
        return public

    def _apply_breakpoint(
        self, record: BreakpointRecord, kind: str, native: int
    ) -> BreakpointRecord:
        public = self._public_for_native("breakpoint" if kind == "catchpoint" else kind, native)
        update: dict[str, object] = {"number": str(public)}
        if kind == "watchpoint":
            update["type"] = "watchpoint"
        elif kind == "catchpoint" or public in self._catchpoints:
            update["type"] = "catchpoint"
        origin = self._origins.get(public)
        if origin is not None:
            update["original_location"] = origin
        if kind == "watchpoint":
            spec, access = self._watch_specs.get(public, (None, None))
            watch = record.watch
            watch_update: dict[str, object] = {}
            if watch is not None:
                if watch.access is None and access is not None:
                    watch_update["access"] = access
                if watch.spec is None and spec is not None:
                    watch_update["spec"] = spec
            if watch_update and watch is not None:
                update["watch"] = watch.model_copy(update=watch_update)
        return record.model_copy(update=update)

    def _refresh_breakpoints(self) -> None:
        seen: set[tuple[str, int]] = set()
        response = self._bridge._request("breakpoint_list")
        for payload in mappings(response.get("breakpoints")):
            fields = dict(payload)
            native = self._native_number(fields)
            record = BreakpointRecord.from_payload(fields)
            self._apply_breakpoint(record, "breakpoint", native)
            seen.add(("breakpoint", native))
        response = self._bridge._request("watchpoint_list")
        for payload in mappings(response.get("watchpoints")):
            fields = dict(payload)
            native = self._native_number(fields)
            record = BreakpointRecord.from_payload(fields)
            self._apply_breakpoint(record, "watchpoint", native)
            seen.add(("watchpoint", native))
        for public, native_key in list(self._public_native.items()):
            if native_key not in seen:
                self._public_native.pop(public, None)
                self._native_public.pop(native_key, None)
                self._origins.pop(public, None)
                self._watch_specs.pop(public, None)
                self._catchpoints.discard(public)

    def _all_breakpoints(self) -> list[BreakpointRecord]:
        self._refresh_breakpoints()
        records: list[BreakpointRecord] = []
        for public, (kind, native) in sorted(self._public_native.items()):
            if kind == "watchpoint":
                payload = self._bridge._request("watchpoint_get", id=native)
            else:
                payload = self._bridge._request("breakpoint_get", id=native)
            record = BreakpointRecord.from_payload(dict(payload))
            records.append(self._apply_breakpoint(record, kind, native))
        return sorted(records, key=lambda item: int(item.number or "0"))

    def _lookup_native(self, number: int) -> tuple[str, int] | None:
        return self._public_native.get(number)

    def _get_breakpoint_record(self, number: int) -> BreakpointRecord:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None:
            raise BridgeError(f"Breakpoint {number} not found", "not_found")
        kind, native_id = native
        operation = "watchpoint_get" if kind == "watchpoint" else "breakpoint_get"
        payload = self._bridge._request(operation, id=native_id)
        return self._apply_breakpoint(BreakpointRecord.from_payload(dict(payload)), kind, native_id)

    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        try:
            mode, fields = parse_breakpoint_location(location)
            payload = self._bridge._request(
                "breakpoint_create",
                mode=mode,
                condition=condition or "",
                temporary=temporary,
                original=location,
                **fields,
            )
            native = self._native_number(payload)
            public = self._public_for_native("breakpoint", native)
            self._origins[public] = location
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), "breakpoint", native
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return operation_error(exc)

    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        try:
            read = access in {"read", "access"}
            write = access in {"write", "access"}
            payload = self._bridge._request(
                "watchpoint_create",
                expression=expression,
                read=read,
                write=write,
                access=access,
            )
            native = self._native_number(payload)
            public = self._public_for_native("watchpoint", native)
            self._origins[public] = expression
            self._watch_specs[public] = (expression, access)
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), "watchpoint", native
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return operation_error(exc)

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        native = self._lookup_native(number)
        if native is None or native[0] != "watchpoint":
            return OperationError(message=f"Watchpoint {number} not found", code="not_found")
        return self._delete_breakpoint(number, expected="watchpoint")

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        if kind not in _LLDB_CATCHPOINTS:
            return OperationError(
                message=(
                    f"LLDB supports exception catchpoints (throw, rethrow, catch), not {kind!r}. "
                    "Fork, exec and syscall events are reported as stops when they occur."
                ),
                code="unsupported_combination",
            )
        try:
            payload = self._bridge._request(
                "breakpoint_create",
                mode="exception",
                catch_exception=kind == "catch",
                throw_exception=kind in {"throw", "rethrow"},
                temporary=temporary,
                original=kind,
            )
            native = self._native_number(payload)
            public = self._public_for_native("breakpoint", native)
            self._catchpoints.add(public)
            self._origins[public] = kind
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), "catchpoint", native
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return operation_error(exc)

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        try:
            records = self._all_breakpoints()
            return OperationSuccess(BreakpointListInfo(breakpoints=records))
        except BridgeError as exc:
            return operation_error(exc)

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        try:
            return OperationSuccess(BreakpointInfo(breakpoint=self._get_breakpoint_record(number)))
        except BridgeError as exc:
            return operation_error(exc)

    def _delete_breakpoint(
        self, number: int, *, expected: str | None = None
    ) -> OperationSuccess[SessionMessage] | OperationError:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None or (expected is not None and native[0] != expected):
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        kind, native_id = native
        operation = "watchpoint_delete" if kind == "watchpoint" else "breakpoint_delete"
        try:
            self._bridge._request(operation, id=native_id)
        except BridgeError as exc:
            return operation_error(exc)
        self._public_native.pop(number, None)
        self._native_public.pop(native, None)
        self._origins.pop(number, None)
        self._watch_specs.pop(number, None)
        self._catchpoints.discard(number)
        label = "Watchpoint" if kind == "watchpoint" else "Breakpoint"
        return OperationSuccess(SessionMessage(message=f"{label} {number} deleted"))

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._delete_breakpoint(number)

    def _set_enabled(
        self, number: int, enabled: bool
    ) -> OperationSuccess[SessionMessage] | OperationError:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        kind, native_id = native
        operation = "watchpoint_enable" if kind == "watchpoint" else "breakpoint_enable"
        try:
            self._bridge._request(operation, id=native_id, enabled=enabled)
        except BridgeError as exc:
            return operation_error(exc)
        label = "Watchpoint" if kind == "watchpoint" else "Breakpoint"
        word = "enabled" if enabled else "disabled"
        return OperationSuccess(SessionMessage(message=f"{label} {number} {word}"))

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._set_enabled(number, True)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._set_enabled(number, False)

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        kind, native_id = native
        operation = "watchpoint_condition" if kind == "watchpoint" else "breakpoint_condition"
        try:
            payload = self._bridge._request(
                operation,
                id=native_id,
                condition="" if clear_condition else (condition or ""),
            )
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), kind, native_id
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return operation_error(exc)
