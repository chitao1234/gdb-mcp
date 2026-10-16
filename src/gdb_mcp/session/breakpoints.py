"""Breakpoint operations for a composed SessionService."""

from __future__ import annotations

import logging
import re

from ..domain import (
    BreakpointInfo,
    BreakpointListInfo,
    BreakpointRecord,
    CatchpointType,
    OperationError,
    OperationSuccess,
    SessionMessage,
    StructuredPayload,
    WatchpointAccessType,
    breakpoint_list_info_from_payload,
    breakpoint_record,
    payload_to_mapping,
    result_to_mapping,
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
_INSERT_BLOCKED_RE = re.compile(
    r"Cannot insert breakpoint\s+(?P<number>\d+)",
    re.IGNORECASE,
)


def _blocked_breakpoint_numbers(message: str) -> list[int]:
    """Return the breakpoint numbers GDB reported it cannot insert, in order."""

    numbers: list[int] = []
    for match in _INSERT_BLOCKED_RE.finditer(message):
        number = int(match.group("number"))
        if number not in numbers:
            numbers.append(number)
    return numbers


def _format_breakpoint_numbers(numbers: list[int]) -> str:
    """Render breakpoint numbers for a human-readable message."""

    return ", ".join(str(number) for number in numbers)


def _insert_blocked_reason(message: str) -> str:
    """Extract the concrete reason from a GDB insert-blocked warning."""

    for line in message.splitlines():
        text = line.strip()
        if not text or text.lower().startswith("warning"):
            continue
        if _INSERT_BLOCKED_RE.match(text):
            continue
        return text
    return "GDB could not insert the breakpoint"


class SessionBreakpointService:
    """Breakpoint-related operations."""

    def __init__(self, command_runner: SessionCommandRunner):
        self._command_runner = command_runner

    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
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
            return self._attribute_insert_failure(location, result)

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
            create_details={
                "created_breakpoint_number": number,
                "create_payload": payload_to_mapping(payload),
            },
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

        result = self._command_runner.execute_command_result(
            command, timeout_sec=DEFAULT_TIMEOUT_SEC
        )
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
            create_details={
                "created_breakpoint_number": number,
                "create_output": (result.value.output or "").strip(),
            },
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

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Return one breakpoint by its numeric identifier."""

        list_result = self.list_breakpoints()
        if isinstance(list_result, OperationError):
            return list_result

        breakpoint_info = self._find_breakpoint_record(list_result.value.breakpoints, number)
        if breakpoint_info is None:
            return OperationError(
                message=f"Breakpoint {number} not found",
                code="not_found",
                details={"breakpoint_number": number},
            )

        return OperationSuccess(BreakpointInfo(breakpoint=breakpoint_info))

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

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Update one existing breakpoint and return the refreshed record."""

        if condition is not None and clear_condition:
            return OperationError(
                message="condition and clear_condition are mutually exclusive",
                code="unsupported_combination",
            )
        if condition is None and clear_condition is False:
            return OperationError(
                message="At least one breakpoint change is required",
                code="validation_error",
            )

        command = f"-break-condition {number}"
        if condition is not None:
            command = f"{command} {quote_mi_string(condition)}"

        result = self._command_runner.execute_command_result(
            command,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        return self._breakpoint_info_for_number(
            number,
            create_details={"updated_breakpoint_number": number},
        )

    def _attribute_insert_failure(
        self, location: str, error: OperationError
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Rewrite a ``-break-insert`` failure so it names the real blocker.

        GDB retries every existing breakpoint whenever a new one is inserted.
        An earlier breakpoint that cannot be inserted (for example an absolute
        address that is not relocatable in a PIE) therefore turns unrelated
        ``-break-insert`` calls into errors whose message is about the stale
        breakpoint. Re-read the inventory and either confirm the requested
        breakpoint with a warning, or fail while pointing at the blocker.
        """

        blocked_numbers = _blocked_breakpoint_numbers(error.message)
        if not blocked_numbers:
            return error

        blocker_label = _format_breakpoint_numbers(blocked_numbers)
        reason = _insert_blocked_reason(error.message)
        list_result = self.list_breakpoints()
        if isinstance(list_result, OperationError):
            return OperationError(
                message=(
                    f"Could not insert a breakpoint at {location}: existing breakpoint(s) "
                    f"{blocker_label} cannot be inserted ({reason}); the breakpoint "
                    "inventory could not be refreshed to confirm the result"
                ),
                code="breakpoint_insert_blocked",
                details={
                    "requested_location": location,
                    "blocked_breakpoint_numbers": payload_to_mapping(blocked_numbers),
                    "raw_error": error.message,
                    "refresh_error": result_to_mapping(list_result),
                },
            )

        records = list_result.value.breakpoints
        blocked_records = [
            blocked_record
            for number in blocked_numbers
            if (blocked_record := self._find_breakpoint_record(records, number)) is not None
        ]
        created = self._find_breakpoint_for_location(records, location)
        created_number = self._extract_breakpoint_number(created) if created else None

        details: StructuredPayload = {
            "requested_location": location,
            "blocked_breakpoint_numbers": payload_to_mapping(blocked_numbers),
            "blocked_breakpoints": payload_to_mapping(blocked_records),
            "raw_error": error.message,
        }

        if created is None:
            return OperationError(
                message=(
                    f"Could not insert a breakpoint at {location}: existing breakpoint(s) "
                    f"{blocker_label} cannot be inserted ({reason}). "
                    "Delete or fix them before retrying."
                ),
                code="breakpoint_insert_blocked",
                details=details,
            )

        details["created_breakpoint"] = payload_to_mapping(created)
        if created_number is not None and created_number in blocked_numbers:
            return OperationError(
                message=f"Breakpoint {created_number} at {location} cannot be inserted ({reason})",
                code="breakpoint_insert_blocked",
                details=details,
            )

        created_label = (
            f"breakpoint {created_number}"
            if created_number is not None
            else "the requested breakpoint"
        )
        warning = (
            f"Created {created_label} at {location}, but existing breakpoint(s) "
            f"{blocker_label} cannot be inserted ({reason}); delete or fix them before continuing."
        )
        return OperationSuccess(BreakpointInfo(breakpoint=created), warnings=(warning,))

    def _breakpoint_info_for_number(
        self,
        number: int,
        *,
        create_details: StructuredPayload,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Confirm one breakpoint number from the current breakpoint inventory."""

        list_result = self.list_breakpoints()
        if isinstance(list_result, OperationError):
            return OperationError(
                message=(
                    f"Breakpoint {number} could not be confirmed because "
                    "gdb_list_breakpoints failed"
                ),
                details={
                    **create_details,
                    "refresh_error": result_to_mapping(list_result),
                },
            )

        breakpoint_info = self._find_breakpoint_record(list_result.value.breakpoints, number)
        if breakpoint_info is None:
            return OperationError(
                message=(
                    f"Breakpoint {number} could not be confirmed because "
                    f"gdb_list_breakpoints did not return breakpoint {number}"
                ),
                details={
                    **create_details,
                    "refreshed_breakpoints": payload_to_mapping(list_result.value.breakpoints),
                },
            )

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
    def _find_breakpoint_for_location(
        breakpoints: list[BreakpointRecord],
        location: str,
    ) -> BreakpointRecord | None:
        """Find one breakpoint record by the ``original-location`` GDB stored for it."""

        for breakpoint_info in breakpoints:
            if breakpoint_info.get("original-location") == location:
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
