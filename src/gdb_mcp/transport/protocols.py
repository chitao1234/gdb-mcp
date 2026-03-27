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

    stdin: ControllerStdinProtocol


class GdbProcessProtocol(Protocol):
    """Minimal process interface used for liveness checks and interrupts."""

    pid: int

    def poll(self) -> int | None:
        """Return the child exit code or None while it is still running."""


class GdbControllerProtocol(Protocol):
    """Minimal controller interface required by the session and transport layers."""

    io_manager: ControllerIoManagerProtocol
    gdb_process: GdbProcessProtocol | None

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
