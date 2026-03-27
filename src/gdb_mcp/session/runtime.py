"""Authoritative mutable runtime state for one debugger session."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
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
    lifecycle_lock: Any = field(default_factory=threading.RLock, repr=False)
    config: SessionConfig | None = None
    state: SessionState = SessionState.CREATED
    is_running: bool = False
    target_loaded: bool = False
    attached_pid: int | None = None
    execution_state: str = "unknown"
    stop_reason: str | None = None
    exit_code: int | None = None
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
        self.attached_pid = None
        self.execution_state = "unknown"
        self.stop_reason = None
        self.exit_code = None
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
        self.attached_pid = None
        self.execution_state = "unknown"
        self.stop_reason = None
        self.exit_code = None
        self.last_failure_message = message
        self.current_thread_id = None
        self.current_frame = None

    def mark_transport_terminated(self, message: str) -> None:
        """Record that the debugger transport has died and clear its controller."""

        self.controller = None
        self.mark_failed(message)

    def mark_stopped(self) -> None:
        """Record a clean stop of the current session."""

        self.state = SessionState.STOPPED
        self.is_running = False
        self.target_loaded = False
        self.attached_pid = None
        self.execution_state = "unknown"
        self.stop_reason = None
        self.exit_code = None
        self.last_failure_message = None
        self.current_thread_id = None
        self.current_frame = None

    def mark_thread_selected(self, thread_id: int | None) -> None:
        """Track the currently selected thread."""

        self.current_thread_id = thread_id

    def mark_frame_selected(self, frame_number: int | None) -> None:
        """Track the currently selected frame."""

        self.current_frame = frame_number

    def mark_attached(self, pid: int) -> None:
        """Track the PID of the currently attached process."""

        self.attached_pid = pid

    def clear_attached_pid(self) -> None:
        """Clear any remembered attached process PID."""

        self.attached_pid = None

    def mark_inferior_not_started(self) -> None:
        """Record that a target is loaded but execution has not begun."""

        self.execution_state = "not_started"
        self.stop_reason = None
        self.exit_code = None

    def mark_inferior_running(self) -> None:
        """Record that the inferior is currently running."""

        self.execution_state = "running"
        self.stop_reason = None
        self.exit_code = None

    def mark_inferior_paused(self, reason: str | None = None) -> None:
        """Record that the inferior is paused and inspectable."""

        self.execution_state = "paused"
        self.stop_reason = reason
        self.exit_code = None

    def mark_inferior_exited(
        self,
        reason: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        """Record that the inferior has exited while the session remains alive."""

        self.execution_state = "exited"
        self.stop_reason = reason
        self.exit_code = exit_code
