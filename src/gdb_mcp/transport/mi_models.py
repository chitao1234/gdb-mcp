"""Typed models for parsed GDB/MI responses."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedMiResponse:
    """Normalized view of the records emitted for a single GDB/MI command."""

    console: list[Any] = field(default_factory=list)
    log: list[Any] = field(default_factory=list)
    output: list[Any] = field(default_factory=list)
    result: Any = None
    result_class: str | None = None
    notify: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert back to the legacy dict shape used by the current service layer."""

        return {
            "console": list(self.console),
            "log": list(self.log),
            "output": list(self.output),
            "result": self.result,
            "result_class": self.result_class,
            "notify": list(self.notify),
        }
