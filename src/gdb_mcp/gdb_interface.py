"""Compatibility GDB interface built on top of the session service layer."""

import os
import time

from pygdbmi.gdbcontroller import GdbController

from .session.factory import create_gdb_controller
from .session.service import (
    DEFAULT_MAX_BACKTRACE_FRAMES,
    DEFAULT_TIMEOUT_SEC,
    FILE_LOAD_TIMEOUT_SEC,
    INITIAL_COMMAND_TOKEN,
    INIT_COMMAND_DELAY_SEC,
    INTERRUPT_RESPONSE_TIMEOUT_SEC,
    POLL_TIMEOUT_SEC,
    SessionService,
)


class GDBSession(SessionService):
    """Compatibility wrapper that preserves the historic GDBSession import path."""

    def __init__(self):
        super().__init__(
            controller_factory=lambda **kwargs: create_gdb_controller(
                controller_class=GdbController, **kwargs
            ),
            os_module=os,
            time_module=time,
            initial_command_token=INITIAL_COMMAND_TOKEN,
            poll_timeout_sec=POLL_TIMEOUT_SEC,
        )
