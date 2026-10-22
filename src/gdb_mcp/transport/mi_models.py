"""Typed models for parsed GDB/MI responses."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypeAlias, TypedDict

MiRecord: TypeAlias = dict[str, object]


class MiNotifyRecord(TypedDict):
    """Normalized async notification record."""

    message: str | None
    payload: Mapping[str, object] | None


@dataclass(slots=True)
class ParsedMiResponse:
    """Normalized view of the records emitted for a single GDB/MI command."""

    console: list[object] = field(default_factory=list)
    log: list[object] = field(default_factory=list)
    output: list[object] = field(default_factory=list)
    result: Mapping[str, object] | None = None
    result_class: str | None = None
    notify: list[MiNotifyRecord] = field(default_factory=list)

    def is_error_result(self) -> bool:
        """Return True when the MI result record reported an error."""

        return self.result_class == "error"

    def error_message(self) -> str | None:
        """Return everything GDB reported for a failed command.

        GDB keeps the error itself in the MI ``msg`` and often prints context to
        the console first (the thread header of ``thread apply``, the address
        ``x`` tried to read). Both belong to the failure, so the streams come
        first and the ``msg`` is appended unless it is already part of them.
        """

        if not self.is_error_result():
            return None

        streams = self.output_text()
        result_message = None
        if isinstance(self.result, Mapping):
            for key in ("msg", "message"):
                message = self.result.get(key)
                if isinstance(message, str) and message.strip():
                    result_message = message.strip()
                    break

        if result_message is None:
            return streams or "GDB returned an error"
        if not streams or result_message in streams:
            return streams or result_message
        return f"{streams}\n{result_message}"

    def output_text(self) -> str:
        """Join the console, log, and output streams GDB printed for this command."""

        parts = [
            "".join(item for item in stream if isinstance(item, str)).strip()
            for stream in (self.console, self.log, self.output)
        ]
        return "\n".join(part for part in parts if part)

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
