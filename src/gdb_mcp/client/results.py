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

from gdb_mcp.domain.parsing import as_int, as_text

_ACTION_LIST = "list"


@dataclass(frozen=True, slots=True)
class ToolEnvelope:
    """Narrowed view over one MCP tool result payload."""

    status: str | None
    action: str | None
    code: str | None
    message: str | None
    fatal: bool | None
    session_id: int | None
    target_loaded: bool | None
    execution_state: str | None
    error_count: int | None
    result: Mapping[str, object] | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ToolEnvelope:
        """Build a typed envelope from a raw tool payload."""

        status = as_text(payload.get("status"))
        if status == "success":
            if "result" not in payload:
                raise TypeError("Successful tool results require an object payload")
            result_value = payload["result"]
            if not isinstance(result_value, Mapping):
                raise TypeError("Successful tool results must contain an object payload")
            result: Mapping[str, object] | None = result_value
        else:
            result = None
        message = (
            as_text(result.get("message"))
            if result is not None
            else as_text(payload.get("message"))
        )
        return cls(
            status=status,
            action=as_text(payload.get("action")),
            code=as_text(payload.get("code")),
            message=message,
            fatal=_as_bool(payload.get("fatal")),
            session_id=as_int(result.get("session_id")) if result is not None else None,
            target_loaded=_as_bool(result.get("target_loaded")) if result is not None else None,
            execution_state=as_text(result.get("execution_state")) if result is not None else None,
            error_count=as_int(result.get("error_count")) if result is not None else None,
            result=result,
        )

    def is_list_action(self) -> bool:
        """Return whether this payload is a session-list action envelope."""

        return self.action == _ACTION_LIST


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
