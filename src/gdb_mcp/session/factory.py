"""Factories for constructing the default session service implementation."""

import os
import time

from pygdbmi.gdbcontroller import GdbController

from .service import (
    INITIAL_COMMAND_TOKEN,
    POLL_TIMEOUT_SEC,
    SessionService,
)


def create_default_session_service() -> SessionService:
    """Create the standard SessionService used by the registry and server."""

    return SessionService(
        controller_factory=GdbController,
        os_module=os,
        time_module=time,
        initial_command_token=INITIAL_COMMAND_TOKEN,
        poll_timeout_sec=POLL_TIMEOUT_SEC,
    )
