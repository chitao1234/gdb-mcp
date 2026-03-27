"""Breakpoint operations for a composed SessionService."""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..domain import (
    BreakpointInfo,
    BreakpointListInfo,
    BreakpointRecord,
    CatchpointType,
    OperationError,
    OperationSuccess,
    SessionMessage,
    WatchpointAccessType,
    breakpoint_list_info_from_payload,
    breakpoint_record,
    payload_to_mapping,
)
from ..transport import extract_mi_result_payload, quote_mi_string
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_TIMEOUT_SEC
from .result_utils import command_result_payload

logger = logging.getLogger(__name__)
_CATCHPOINT_NUMBER_RE = re.compile(
    r"(?:Temporary\s+)?catchpoint\s+(?P<number>\d+)",
    re.IGNORECASE,
)


class SessionBreakpointService:
    """Breakpoint-related operations."""

    def __init__(self, command_runner: SessionCommandRunner):
        self._command_runner = command_runner

    def set_breakpoint(
        self, location: str, condition: Optional[str] = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Set a breakpoint at the specified location."""
        cmd_parts = ["-break-insert"]

        if temporary:
            cmd_parts.append("-t")

        if condition:
            cmd_parts.extend(["-c", quote_mi_string(condition)])

        cmd_parts.append(quote_mi_string(location))

        result = self._command_runner.execute_command_result(
            " ".join(cmd_parts), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        logger.debug("Breakpoint MI result: %s", payload)

        if payload is None:
            logger.warning("No MI result for breakpoint at %s", location)
            return OperationError(
                message=f"Failed to set breakpoint at {location}: no result from GDB",
                details={"raw_result": payload_to_mapping(result.value)},
            )

        bp = breakpoint_record(payload)

        if not bp:
            logger.warning("Empty breakpoint result for %s: %s", location, payload)
            return OperationError(
                message=f"Breakpoint set but no info returned for {location}",
                details={"raw_result": payload_to_mapping(result.value)},
            )

        return OperationSuccess(BreakpointInfo(breakpoint=bp))

    def set_watchpoint(
        self,
        expression: str,
        *,
        access: WatchpointAccessType = "write",
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Set a watchpoint and return the normalized breakpoint record."""

        cmd_parts = ["-break-watch"]
        if access == "read":
            cmd_parts.append("-r")
        elif access == "access":
            cmd_parts.append("-a")
        cmd_parts.append(quote_mi_string(expression))

        result = self._command_runner.execute_command_result(
            " ".join(cmd_parts), timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        number = self._extract_created_breakpoint_number(payload)
        if number is None:
            return OperationError(
                message=f"Watchpoint set for {expression}, but GDB did not report its number",
                details={"raw_result": payload_to_mapping(result.value)},
            )

        return self._breakpoint_info_for_number(
            number,
            fallback_record={"number": str(number), "exp": expression},
        )

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delete a watchpoint by its breakpoint number."""

        result = self._command_runner.execute_command_result(
            f"-break-delete {number}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        return OperationSuccess(SessionMessage(message=f"Watchpoint {number} deleted"))

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Set a catchpoint for a validated debugger event."""

        previous_catchpoints = self._catchpoint_numbers()
        prefix = "tcatch" if temporary else "catch"
        command = f"{prefix} {kind}"
        if argument:
            command = f"{command} {argument}"

        result = self._command_runner.execute_command_result(command, timeout_sec=DEFAULT_TIMEOUT_SEC)
        if isinstance(result, OperationError):
            return result

        number = self._extract_catchpoint_number(result.value.output or "")
        if number is None:
            number = self._infer_new_catchpoint_number(previous_catchpoints)
        if number is None:
            return OperationError(
                message=f"Catchpoint {kind} set, but GDB did not report its number",
                details={"output": result.value.output or ""},
            )

        return self._breakpoint_info_for_number(
            number,
            fallback_record={"number": str(number), "type": "catchpoint"},
        )

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        """List all breakpoints with structured data."""
        result = self._command_runner.execute_command_result(
            "-break-list", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        return OperationSuccess(
            breakpoint_list_info_from_payload(
                extract_mi_result_payload(command_result_payload(result))
            )
        )

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Delete a breakpoint by its number."""
        result = self._command_runner.execute_command_result(
            f"-break-delete {number}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        return OperationSuccess(SessionMessage(message=f"Breakpoint {number} deleted"))

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Enable a breakpoint by its number."""
        result = self._command_runner.execute_command_result(
            f"-break-enable {number}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        return OperationSuccess(SessionMessage(message=f"Breakpoint {number} enabled"))

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Disable a breakpoint by its number."""
        result = self._command_runner.execute_command_result(
            f"-break-disable {number}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        return OperationSuccess(SessionMessage(message=f"Breakpoint {number} disabled"))

    def _breakpoint_info_for_number(
        self,
        number: int,
        *,
        fallback_record: BreakpointRecord,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Resolve one breakpoint number from the current breakpoint inventory."""

        list_result = self.list_breakpoints()
        if isinstance(list_result, OperationError):
            return OperationSuccess(BreakpointInfo(breakpoint=fallback_record))

        breakpoint_info = self._find_breakpoint_record(list_result.value.breakpoints, number)
        if breakpoint_info is None:
            return OperationSuccess(BreakpointInfo(breakpoint=fallback_record))

        return OperationSuccess(BreakpointInfo(breakpoint=breakpoint_info))

    def _catchpoint_numbers(self) -> set[int] | None:
        """Return the current catchpoint-number set when breakpoint inventory is available."""

        list_result = self.list_breakpoints()
        if isinstance(list_result, OperationError):
            return None

        numbers: set[int] = set()
        for breakpoint_info in list_result.value.breakpoints:
            breakpoint_type = str(breakpoint_info.get("type", "")).lower()
            if "catch" not in breakpoint_type:
                continue
            number = self._extract_breakpoint_number(breakpoint_info)
            if number is not None:
                numbers.add(number)
        return numbers

    def _infer_new_catchpoint_number(self, previous: set[int] | None) -> int | None:
        """Infer the created catchpoint by diffing catchpoint inventory before/after creation."""

        current = self._catchpoint_numbers()
        if current is None:
            return None

        if previous is None:
            return max(current) if current else None

        created = sorted(current - previous)
        if created:
            return created[-1]
        return None

    @staticmethod
    def _find_breakpoint_record(
        breakpoints: list[BreakpointRecord],
        number: int,
    ) -> BreakpointRecord | None:
        """Find one breakpoint record by its numeric number field."""

        for breakpoint_info in breakpoints:
            if SessionBreakpointService._extract_breakpoint_number(breakpoint_info) == number:
                return breakpoint_info
        return None

    @staticmethod
    def _extract_breakpoint_number(payload: object) -> int | None:
        """Extract a breakpoint number from a raw breakpoint-like payload."""

        raw_number = breakpoint_record(payload).get("number")
        if isinstance(raw_number, int):
            return raw_number
        if isinstance(raw_number, str) and raw_number.isdigit():
            return int(raw_number)
        return None

    @classmethod
    def _extract_created_breakpoint_number(cls, payload: object) -> int | None:
        """Extract the created breakpoint number from MI result payloads."""

        raw_mapping = breakpoint_record(payload)
        number = cls._extract_breakpoint_number(raw_mapping)
        if number is not None:
            return number

        if isinstance(payload, dict):
            for key in ("wpt", "awpt", "hw-awpt", "hw-rwpt"):
                watchpoint = payload.get(key)
                if isinstance(watchpoint, dict):
                    number = cls._extract_breakpoint_number(watchpoint)
                    if number is not None:
                        return number

        return None

    @staticmethod
    def _extract_catchpoint_number(output: str) -> int | None:
        """Parse a catchpoint number from CLI catch/tcatch output."""

        match = _CATCHPOINT_NUMBER_RE.search(output)
        if match is None:
            return None
        return int(match.group("number"))
