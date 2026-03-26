"""Session service composition root."""

from __future__ import annotations

from typing import Any, Optional

from ..transport import MiClient
from .breakpoints import SessionBreakpointMixin
from .config import SessionConfig
from .constants import (
    DEFAULT_MAX_BACKTRACE_FRAMES,
    DEFAULT_TIMEOUT_SEC,
    FILE_LOAD_TIMEOUT_SEC,
    INITIAL_COMMAND_TOKEN,
    INIT_COMMAND_DELAY_SEC,
    INTERRUPT_RESPONSE_TIMEOUT_SEC,
    POLL_TIMEOUT_SEC,
)
from .execution import SessionExecutionMixin
from .inspection import SessionInspectionMixin
from .lifecycle import SessionLifecycleMixin
from .state import SessionState


class SessionService(
    SessionLifecycleMixin,
    SessionExecutionMixin,
    SessionBreakpointMixin,
    SessionInspectionMixin,
):
    """
    Orchestrates one debugger session on top of the transport layer.

    The public API remains on this class, but the implementation is split across
    focused mixins for lifecycle, execution, breakpoints, and inspection.
    """

    def __init__(
        self,
        *,
        controller_factory: Any,
        os_module: Any,
        time_module: Any,
        initial_command_token: int = INITIAL_COMMAND_TOKEN,
        poll_timeout_sec: float = POLL_TIMEOUT_SEC,
    ):
        self._transport = MiClient(
            controller_factory=controller_factory,
            initial_command_token=initial_command_token,
            poll_timeout_sec=poll_timeout_sec,
        )
        self._os = os_module
        self._time = time_module
        self.is_running = False
        self.target_loaded = False
        self.config: Optional[SessionConfig] = None
        self.state = SessionState.CREATED

    @property
    def controller(self) -> Any:
        """Expose the underlying controller for orchestration and tests."""

        return self._transport.controller

    @controller.setter
    def controller(self, value: Any) -> None:
        """Allow tests to replace the underlying controller."""

        self._transport.controller = value
