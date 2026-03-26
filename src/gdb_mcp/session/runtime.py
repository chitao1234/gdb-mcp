"""Authoritative mutable runtime state for one debugger session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..transport import MiClient
from .config import SessionConfig
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .state import SessionState


@dataclass(slots=True)
class SessionRuntime:
    """Single source of truth for session lifecycle and transport state."""

    transport: MiClient
    os_module: OsModuleProtocol
    time_module: TimeModuleProtocol
    config: SessionConfig | None = None
    state: SessionState = SessionState.CREATED
    is_running: bool = False
    target_loaded: bool = False
    last_failure_message: str | None = None
    current_thread_id: int | None = None
    current_frame: int | None = None

    @property
    def controller(self) -> Any:
        """Expose the transport controller for compatibility and tests."""

        return self.transport.controller

    @controller.setter
    def controller(self, value: Any) -> None:
        """Replace the underlying transport controller."""

        self.transport.controller = value

    @property
    def has_controller(self) -> bool:
        """Return whether a controller is currently attached."""

        return self.controller is not None

    def begin_startup(self, config: SessionConfig) -> None:
        """Record a startup attempt and reset transient state."""

        self.config = config
        self.state = SessionState.STARTING
        self.is_running = False
        self.target_loaded = False
        self.last_failure_message = None
        self.current_thread_id = None
        self.current_frame = None

    def mark_ready(self) -> None:
        """Mark the session as ready for requests."""

        self.state = SessionState.READY
        self.is_running = True
        self.last_failure_message = None

    def mark_failed(self, message: str) -> None:
        """Record a terminal failure for the current session."""

        self.state = SessionState.FAILED
        self.is_running = False
        self.target_loaded = False
        self.last_failure_message = message
        self.current_thread_id = None
        self.current_frame = None

    def mark_stopped(self) -> None:
        """Record a clean stop of the current session."""

        self.state = SessionState.STOPPED
        self.is_running = False
        self.target_loaded = False
        self.last_failure_message = None
        self.current_thread_id = None
        self.current_frame = None

    def mark_thread_selected(self, thread_id: int | None) -> None:
        """Track the currently selected thread."""

        self.current_thread_id = thread_id

    def mark_frame_selected(self, frame_number: int | None) -> None:
        """Track the currently selected frame."""

        self.current_frame = frame_number

