"""Typed models for parsed GDB/MI responses."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias, TypedDict

MiRecord: TypeAlias = dict[str, object]


class MiNotifyRecord(TypedDict):
    """Normalized async notification record."""

    message: str | None
    payload: object


@dataclass(slots=True)
class ParsedMiResponse:
    """Normalized view of the records emitted for a single GDB/MI command."""

    console: list[object] = field(default_factory=list)
    log: list[object] = field(default_factory=list)
    output: list[object] = field(default_factory=list)
    result: object | None = None
    result_class: str | None = None
    notify: list[MiNotifyRecord] = field(default_factory=list)

    def is_error_result(self) -> bool:
        """Return True when the MI result record reported an error."""

        return self.result_class == "error"

    def error_message(self) -> str | None:
        """Extract the best available error message from the parsed MI result."""

        if not self.is_error_result():
            return None

        if isinstance(self.result, Mapping):
            for key in ("msg", "message"):
                message = self.result.get(key)
                if isinstance(message, str) and message.strip():
                    return message.strip()

        for stream in (self.console, self.log, self.output):
            text = "".join(item for item in stream if isinstance(item, str)).strip()
            if text:
                return text

        return "GDB returned an error"

    def to_dict(self) -> dict[str, object]:
        """Convert into plain builtin containers for higher-level payload assembly."""

        return {
            "console": list(self.console),
            "log": list(self.log),
            "output": list(self.output),
            "result": self.result,
            "result_class": self.result_class,
            "notify": list(self.notify),
        }


@dataclass(slots=True)
class MiTransportResponse:
    """Result of sending one command over the GDB/MI transport."""

    command_responses: list[MiRecord] = field(default_factory=list)
    async_notifications: list[MiRecord] = field(default_factory=list)
    timed_out: bool = False
    error: str | None = None
    fatal: bool = False

    def to_dict(self) -> dict[str, object]:
        """Convert into plain builtin containers for session-layer handling."""

        result = {
            "command_responses": list(self.command_responses),
            "async_notifications": list(self.async_notifications),
            "timed_out": self.timed_out,
        }
        if self.error is not None:
            result["error"] = self.error
        if self.fatal:
            result["fatal"] = True
        return result
