"""Typed protocols for the GDB/MI controller boundary."""

from __future__ import annotations

from typing import Protocol

from .mi_models import MiRecord


class ControllerStdinProtocol(Protocol):
    """Subset of the controller stdin writer used by the transport client."""

    def write(self, data: bytes) -> int | None:
        """Write bytes to the controller stdin stream."""

    def flush(self) -> None:
        """Flush buffered stdin bytes to the GDB process."""


class ControllerIoManagerProtocol(Protocol):
    """Subset of pygdbmi's IO manager used by the transport client."""

    @property
    def stdin(self) -> ControllerStdinProtocol:
        """Expose the controller's stdin stream."""


class GdbProcessProtocol(Protocol):
    """Minimal process interface used for liveness checks and interrupts."""

    @property
    def pid(self) -> int:
        """Expose the child process id."""

    def poll(self) -> int | None:
        """Return the child exit code or None while it is still running."""


class GdbControllerProtocol(Protocol):
    """Minimal controller interface required by the session and transport layers."""

    @property
    def io_manager(self) -> ControllerIoManagerProtocol:
        """Expose the controller's IO manager."""

    @property
    def gdb_process(self) -> GdbProcessProtocol | None:
        """Expose the debugger child process, if the controller owns one."""

    def get_gdb_response(
        self,
        *,
        timeout_sec: float,
        raise_error_on_timeout: bool,
    ) -> list[MiRecord]:
        """Read one batch of GDB/MI records."""

    def exit(self) -> None:
        """Terminate the controller and its child GDB process."""


class GdbControllerFactoryProtocol(Protocol):
    """Callable factory used to construct new controller instances."""

    def __call__(
        self,
        *,
        command: list[str],
        time_to_check_for_additional_output_sec: float,
        cwd: str | None = None,
    ) -> GdbControllerProtocol:
        """Create one controller instance for a new debugger session."""
