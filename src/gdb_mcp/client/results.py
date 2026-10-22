"""Typed view over MCP tool result payloads.

The wire payload is a JSON object whose keys depend on the tool and the
outcome. Rather than reaching into it with duck-typed ``.get`` calls, callers
read this envelope, which narrows the fields the CLI actually uses through the
shared ``domain.parsing`` helpers. Unknown fields stay available on the raw
payload for echoing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from gdb_mcp.contracts import ACTION_LIST
from gdb_mcp.domain import ToolResultStatus
from gdb_mcp.domain.parsing import as_int, as_text


@dataclass(frozen=True, slots=True)
class ToolEnvelope:
    """Narrowed view over one MCP tool result payload."""

    status: ToolResultStatus
    action: str | None
    code: str | None
    message: str | None
    fatal: bool | None
    session_id: int | None
    target_loaded: bool | None
    execution_state: str | None
    result: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ToolEnvelope:
        """Build a typed envelope from a raw tool payload."""

        status = cast(ToolResultStatus, payload["status"])
        if status == "success":
            result = cast(Mapping[str, object], payload["result"])
            message = as_text(result.get("message"))
        else:
            result = {}
            message = as_text(payload.get("message"))
        return cls(
            status=status,
            action=as_text(payload.get("action")),
            code=as_text(payload.get("code")),
            message=message,
            fatal=_as_bool(payload.get("fatal")),
            session_id=as_int(result.get("session_id")),
            target_loaded=_as_bool(result.get("target_loaded")),
            execution_state=as_text(result.get("execution_state")),
            result=result,
        )

    def is_list_action(self) -> bool:
        """Return whether this payload is a session-list action envelope."""

        return self.action == ACTION_LIST


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
