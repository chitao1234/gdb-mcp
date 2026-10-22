"""Go debugging through Delve's streaming JSON-RPC API v2."""

from __future__ import annotations

from ..domain import (
    BreakpointInfo,
    BreakpointListInfo,
    OperationSuccess,
    SessionMessage,
    WatchpointAccessType,
)
from ..domain.errors import DebuggerDecodeError
from .delve_inspection import DelveInspection
from .delve_protocols import DelveRequests
from .delve_protocols import operation as _operation
from .delve_values import (
    breakpoint_record,
    integer,
    mapping,
    mappings,
    optional_integer,
)


class DelveBreakpoints:
    """Delve breakpoint operations and temporary-breakpoint lifetime."""

    def __init__(self, bridge: DelveRequests, inspection: DelveInspection) -> None:
        self._bridge = bridge
        self._inspection = inspection
        self._temporary: set[int] = set()

    def reset(self) -> None:
        self._temporary.clear()

    @_operation
    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo]:
        locations = self._inspection.locations(location)
        addresses: list[int] = []
        for loc in locations:
            pcs = loc.get("pcs")
            if pcs is None:
                addresses.append(integer(loc.get("pc")))
                continue
            if not isinstance(pcs, list):
                raise DebuggerDecodeError("Delve location pcs must be a list of integers")
            addresses.extend(integer(pc) for pc in pcs)
        response = self._bridge._request(
            "CreateBreakpoint", {"Breakpoint": {"addrs": addresses, "Cond": condition or ""}}
        )
        breakpoint = mapping(response.get("Breakpoint"))
        if temporary:
            self._temporary.add(integer(breakpoint.get("id")))
        return OperationSuccess(BreakpointInfo(breakpoint_record(breakpoint, temporary=temporary)))

    @_operation
    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo]:
        response = self._bridge._request(
            "CreateWatchpoint",
            {
                "Scope": self._inspection.scope(),
                "Expr": expression,
                "Type": {"read": 1, "write": 2, "access": 3}[access],
            },
        )
        return OperationSuccess(BreakpointInfo(breakpoint_record(response)))

    @_operation
    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        self._inspection.scope()
        self._bridge._request("ClearBreakpoint", {"Id": number})
        return OperationSuccess(SessionMessage(f"Watchpoint {number} deleted"))

    @_operation
    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo]:
        self._inspection.scope()
        breakpoints = mappings(
            self._bridge._request("ListBreakpoints", {"All": False}).get("Breakpoints")
        )
        return OperationSuccess(
            BreakpointListInfo(
                [
                    breakpoint_record(value, temporary=integer(value.get("id")) in self._temporary)
                    for value in breakpoints
                ]
            )
        )

    def _breakpoint(self, number: int) -> dict[str, object]:
        self._inspection.scope()
        breakpoint = mapping(
            self._bridge._request("GetBreakpoint", {"Id": number}).get("Breakpoint")
        )
        integer(breakpoint.get("id"))
        return breakpoint

    @_operation
    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo]:
        return OperationSuccess(
            BreakpointInfo(
                breakpoint_record(self._breakpoint(number), temporary=number in self._temporary)
            )
        )

    @_operation
    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        self._inspection.scope()
        self._bridge._request("ClearBreakpoint", {"Id": number})
        self._temporary.discard(number)
        return OperationSuccess(SessionMessage(f"Breakpoint {number} deleted"))

    def remove_hit_temporary_breakpoints(self, state: dict[str, object]) -> None:
        hit = {
            optional_integer(mapping(thread.get("breakPoint")).get("id"))
            for thread in mappings(state.get("Threads"))
        }
        hit.add(
            optional_integer(
                mapping(mapping(state.get("currentThread")).get("breakPoint")).get("id")
            )
        )
        for number in hit & self._temporary:
            self._bridge._request("ClearBreakpoint", {"Id": number})
            self._temporary.discard(number)

    def _enable_breakpoint(self, number: int, enabled: bool) -> OperationSuccess[SessionMessage]:
        breakpoint = self._breakpoint(number)
        breakpoint["disabled"] = not enabled
        self._bridge._request("AmendBreakpoint", {"Breakpoint": breakpoint})
        return OperationSuccess(
            SessionMessage(f"Breakpoint {number} {'enabled' if enabled else 'disabled'}")
        )

    @_operation
    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        return self._enable_breakpoint(number, True)

    @_operation
    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        return self._enable_breakpoint(number, False)

    @_operation
    def update_breakpoint(
        self, number: int, *, condition: str | None = None, clear_condition: bool = False
    ) -> OperationSuccess[BreakpointInfo]:
        breakpoint = self._breakpoint(number)
        if clear_condition or condition is not None:
            breakpoint["Cond"] = "" if clear_condition else condition
            self._bridge._request("AmendBreakpoint", {"Breakpoint": breakpoint})
        return OperationSuccess(
            BreakpointInfo(breakpoint_record(breakpoint, temporary=number in self._temporary))
        )
