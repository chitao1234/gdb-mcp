"""Factories for constructing the default session service implementation."""

import logging
import os
import subprocess
import time
from collections.abc import Callable
from typing import BinaryIO, cast

from pygdbmi.gdbcontroller import GdbController
from pygdbmi.IoManager import IoManager

from ..contracts import DEBUGGER_GDB, DEBUGGER_LLDB
from ..transport.protocols import GdbControllerProtocol
from .backends import DebuggerBackend
from .constants import (
    INITIAL_COMMAND_TOKEN,
    POLL_TIMEOUT_SEC,
)
from .gdb_backend import GdbMiBackend
from .lldb_backend import LldbSbBackend
from .process_cleanup import cleanup_process
from .protocols import OsModuleProtocol
from .service import SessionService

logger = logging.getLogger(__name__)
_PYGDB_GDB_CONTROLLER = GdbController


class _GdbController(GdbController):
    """GDB controller with a process working directory and bounded cleanup."""

    def __init__(
        self,
        command: list[str] | None = None,
        time_to_check_for_additional_output_sec: float = 0.2,
        *,
        cwd: str | None = None,
    ) -> None:
        self._cwd = cwd
        super().__init__(
            command=command,
            time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
        )

    def spawn_new_gdb_subprocess(self) -> int:
        """Spawn the GDB subprocess using the configured working directory."""

        if self.gdb_process:
            logger.debug("Killing current gdb subprocess (pid %d)", self.gdb_process.pid)
            self.exit()

        logger.debug("Launching gdb: %s", " ".join(self.command))

        self.gdb_process = subprocess.Popen(
            self.command,
            shell=False,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            cwd=self._cwd,
        )

        stdin = self.gdb_process.stdin
        stdout = self.gdb_process.stdout
        self.io_manager = IoManager(
            cast(BinaryIO, stdin),
            cast(BinaryIO, stdout),
            self.gdb_process.stderr,
            self.time_to_check_for_additional_output_sec,
        )
        return self.gdb_process.pid

    def exit(self) -> None:
        """Terminate GDB and close its pipes within the cleanup budget."""

        process = self.gdb_process
        self.gdb_process = None
        if process is not None:
            cleanup_process(process)


def create_gdb_controller(
    *,
    command: list[str],
    time_to_check_for_additional_output_sec: float,
    cwd: str | None = None,
    controller_class: Callable[..., object] | None = None,
) -> GdbControllerProtocol:
    """Create a GDB controller with cwd support and bounded process cleanup."""

    selected_controller_class = cast(
        Callable[..., object], GdbController if controller_class is None else controller_class
    )

    if selected_controller_class is _PYGDB_GDB_CONTROLLER:
        return cast(
            GdbControllerProtocol,
            _GdbController(
                command=command,
                time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
                cwd=cwd,
            ),
        )

    if cwd is None:
        return cast(
            GdbControllerProtocol,
            selected_controller_class(
                command=command,
                time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
            ),
        )

    try:
        return cast(
            GdbControllerProtocol,
            selected_controller_class(
                command=command,
                time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
                cwd=cwd,
            ),
        )
    except TypeError as exc:
        raise TypeError(
            "Controller factory must accept a 'cwd' keyword argument when working_dir is used"
        ) from exc


def create_default_session_service(debugger: str = DEBUGGER_GDB) -> SessionService:
    """Create the SessionService for one debugger.

    The backend owns everything debugger-specific, so the choice happens here,
    where the session is composed, rather than inside the facade.
    """

    backend: DebuggerBackend
    if debugger == DEBUGGER_LLDB:
        backend = LldbSbBackend(
            os_module=cast(OsModuleProtocol, os),
            time_module=time,
        )
    else:
        backend = GdbMiBackend(
            controller_factory=create_gdb_controller,
            os_module=cast(OsModuleProtocol, os),
            time_module=time,
            initial_command_token=INITIAL_COMMAND_TOKEN,
            poll_timeout_sec=POLL_TIMEOUT_SEC,
        )
    return SessionService(backend=backend)
