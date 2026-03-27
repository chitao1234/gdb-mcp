"""Typed protocol definitions for session collaborators."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Protocol


class OsPathProtocol(Protocol):
    """Subset of os.path used by the session layer."""

    def isdir(self, path: str) -> bool:
        """Return whether the provided path is an existing directory."""


class OsModuleProtocol(Protocol):
    """Subset of the os module used by the session layer."""

    environ: Mapping[str, str]
    path: OsPathProtocol

    def kill(self, pid: int, sig: int) -> None:
        """Send a signal to a process."""


class TimeModuleProtocol(Protocol):
    """Subset of the time module used by the session layer."""

    def sleep(self, seconds: float) -> None:
        """Sleep for the given number of seconds."""

    def time(self) -> float:
        """Return the current wall-clock time in seconds."""


class LockProtocol(Protocol):
    """Context-manager lock contract used for lifecycle serialization."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the lock."""

    def release(self) -> None:
        """Release the lock."""

    def __enter__(self) -> object:
        """Enter the lock context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the lock context."""
