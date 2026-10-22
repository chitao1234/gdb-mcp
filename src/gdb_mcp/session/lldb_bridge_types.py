"""Shared values passed between LLDB bridge building and transport."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class BridgeError(RuntimeError):
    """One bridge request failed, with the debugger's own error text."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BridgeLaunch:
    """A built bridge and the environment that selects its liblldb."""

    executable: Path
    layer_dir: Path | None
    environment: dict[str, str]
