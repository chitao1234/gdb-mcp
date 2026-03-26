"""Breakpoint operations for a composed SessionService."""

from __future__ import annotations

import logging
from typing import Optional, cast

from ..domain import (
    BreakpointInfo,
    BreakpointListInfo,
    BreakpointRecord,
    OperationError,
    OperationSuccess,
    SessionMessage,
)
from ..transport import extract_mi_result_payload, quote_mi_string
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_TIMEOUT_SEC
from .result_utils import command_result_payload

logger = logging.getLogger(__name__)


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

        cmd_parts.append(location)

        result = self._command_runner.execute_command_result(
            " ".join(cmd_parts), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        mi_result = extract_mi_result_payload(command_result_payload(result))
        logger.debug("Breakpoint MI result: %s", mi_result)

        if mi_result is None:
            logger.warning("No MI result for breakpoint at %s", location)
            return OperationError(
                message=f"Failed to set breakpoint at {location}: no result from GDB",
                details={"raw_result": result.value},
            )

        bp_info = mi_result if isinstance(mi_result, dict) else {}
        raw_breakpoint = bp_info.get("bkpt", bp_info)
        breakpoint = raw_breakpoint if isinstance(raw_breakpoint, dict) else {}

        if not breakpoint:
            logger.warning("Empty breakpoint result for %s: %s", location, mi_result)
            return OperationError(
                message=f"Breakpoint set but no info returned for {location}",
                details={"raw_result": result.value},
            )

        return OperationSuccess(BreakpointInfo(breakpoint=cast(BreakpointRecord, breakpoint)))

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        """List all breakpoints with structured data."""
        result = self._command_runner.execute_command_result("-break-list", timeout_sec=DEFAULT_TIMEOUT_SEC)

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        raw_bp_table = mi_result.get("BreakpointTable", {})
        bp_table = raw_bp_table if isinstance(raw_bp_table, dict) else {}
        raw_breakpoints = bp_table.get("body", [])
        breakpoints = raw_breakpoints if isinstance(raw_breakpoints, list) else []

        return OperationSuccess(
            BreakpointListInfo(
                breakpoints=cast(list[BreakpointRecord], breakpoints),
                count=len(breakpoints),
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
