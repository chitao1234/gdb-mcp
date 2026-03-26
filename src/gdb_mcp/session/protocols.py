"""Typed protocol definitions for session collaborators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TYPE_CHECKING

from ..domain import CommandExecutionInfo, OperationError, OperationSuccess

if TYPE_CHECKING:
    from .runtime import SessionRuntime


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

    def sleep(self, seconds: float) -> Any:
        """Sleep for the given number of seconds."""

    def time(self) -> float:
        """Return the current wall-clock time in seconds."""


class SessionHostProtocol(Protocol):
    """Capabilities required by composed session operation services."""

    runtime: "SessionRuntime"

    @property
    def controller(self) -> Any:
        """Expose the underlying controller for compatibility and tests."""

    def _execute_command_result(
        self, command: str, timeout_sec: int
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a command through the shared command runner."""

    def _send_command_and_wait_for_prompt(
        self, command: str, timeout_sec: float
    ) -> dict[str, object]:
        """Send one command and wait for a prompt/result."""

    def _is_gdb_alive(self) -> bool:
        """Return whether the underlying GDB process is still alive."""

