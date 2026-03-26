"""Breakpoint operations for SessionService."""

from __future__ import annotations

import logging
from typing import Optional

from ..transport import extract_mi_result_payload

logger = logging.getLogger(__name__)


class SessionBreakpointMixin:
    """Breakpoint-related methods used by SessionService."""

    def set_breakpoint(
        self, location: str, condition: Optional[str] = None, temporary: bool = False
    ) -> dict[str, object]:
        """Set a breakpoint at the specified location."""
        cmd_parts = ["-break-insert"]

        if temporary:
            cmd_parts.append("-t")

        if condition:
            escaped_condition = condition.replace("\\", "\\\\").replace('"', '\\"')
            cmd_parts.extend(["-c", f'"{escaped_condition}"'])

        cmd_parts.append(location)

        result = self.execute_command(" ".join(cmd_parts))

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result)
        logger.debug("Breakpoint MI result: %s", mi_result)

        if mi_result is None:
            logger.warning("No MI result for breakpoint at %s", location)
            return {
                "status": "error",
                "message": f"Failed to set breakpoint at {location}: no result from GDB",
                "raw_result": result,
            }

        bp_info = mi_result if isinstance(mi_result, dict) else {}
        breakpoint = bp_info.get("bkpt", bp_info)

        if not breakpoint:
            logger.warning("Empty breakpoint result for %s: %s", location, mi_result)
            return {
                "status": "error",
                "message": f"Breakpoint set but no info returned for {location}",
                "raw_result": result,
            }

        return {"status": "success", "breakpoint": breakpoint}

    def list_breakpoints(self) -> dict[str, object]:
        """List all breakpoints with structured data."""
        result = self.execute_command("-break-list")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        bp_table = mi_result.get("BreakpointTable", {})
        breakpoints = bp_table.get("body", [])

        return {"status": "success", "breakpoints": breakpoints, "count": len(breakpoints)}

    def delete_breakpoint(self, number: int) -> dict[str, object]:
        """Delete a breakpoint by its number."""
        result = self.execute_command(f"-break-delete {number}")

        if result["status"] == "error":
            return result

        return {"status": "success", "message": f"Breakpoint {number} deleted"}

    def enable_breakpoint(self, number: int) -> dict[str, object]:
        """Enable a breakpoint by its number."""
        result = self.execute_command(f"-break-enable {number}")

        if result["status"] == "error":
            return result

        return {"status": "success", "message": f"Breakpoint {number} enabled"}

    def disable_breakpoint(self, number: int) -> dict[str, object]:
        """Disable a breakpoint by its number."""
        result = self.execute_command(f"-break-disable {number}")

        if result["status"] == "error":
            return result

        return {"status": "success", "message": f"Breakpoint {number} disabled"}
