"""Factories for constructing the default session service implementation."""

from collections.abc import Callable
import logging
import os
import subprocess
import time
from typing import cast

from pygdbmi.IoManager import IoManager
from pygdbmi.gdbcontroller import GdbController

from .constants import (
    INITIAL_COMMAND_TOKEN,
    POLL_TIMEOUT_SEC,
)
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .service import SessionService
from ..transport.protocols import GdbControllerFactoryProtocol, GdbControllerProtocol

logger = logging.getLogger(__name__)


class _WorkingDirGdbController(GdbController):
    """GdbController variant that spawns the subprocess in a specific cwd."""

    def __init__(
        self,
        command=None,
        time_to_check_for_additional_output_sec=0.2,
        *,
        cwd: str | None = None,
    ):
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

        assert self.gdb_process.stdin is not None
        assert self.gdb_process.stdout is not None
        self.io_manager = IoManager(
            self.gdb_process.stdin,
            self.gdb_process.stdout,
            self.gdb_process.stderr,
            self.time_to_check_for_additional_output_sec,
        )
        return self.gdb_process.pid


def create_gdb_controller(
    *,
    command: list[str],
    time_to_check_for_additional_output_sec: float,
    cwd: str | None = None,
    controller_class: Callable[..., object] | None = None,
) -> GdbControllerProtocol:
    """Create a GDB controller, adding cwd support for the default pygdbmi class."""

    selected_controller_class = (
        cast(Callable[..., object], GdbController) if controller_class is None else controller_class
    )

    if cwd is None:
        return cast(
            GdbControllerProtocol,
            selected_controller_class(
                command=command,
                time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
            ),
        )

    if controller_class is None or controller_class is GdbController:
        return cast(
            GdbControllerProtocol,
            _WorkingDirGdbController(
                command=command,
                time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
                cwd=cwd,
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


def create_default_session_service() -> SessionService:
    """Create the standard SessionService used by the registry and server."""

    return SessionService(
        controller_factory=cast(GdbControllerFactoryProtocol, create_gdb_controller),
        os_module=cast(OsModuleProtocol, os),
        time_module=cast(TimeModuleProtocol, time),
        initial_command_token=INITIAL_COMMAND_TOKEN,
        poll_timeout_sec=POLL_TIMEOUT_SEC,
    )
