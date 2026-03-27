"""Authoritative mutable runtime state for one debugger session."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import TypeVar

from ..domain import CommandTranscriptEntry, FollowForkMode, StopEvent
from ..transport import MiClient
from ..transport.protocols import GdbControllerProtocol
from .config import SessionConfig
from .constants import DEFAULT_COMMAND_TRANSCRIPT_LIMIT, DEFAULT_STOP_HISTORY_LIMIT
from .protocols import LockProtocol, OsModuleProtocol, TimeModuleProtocol
from .state import SessionState

HistoryItemT = TypeVar("HistoryItemT")


@dataclass(slots=True)
class SessionRuntime:
    """Single source of truth for session lifecycle and transport state."""

    transport: MiClient
    os_module: OsModuleProtocol
    time_module: TimeModuleProtocol
    lifecycle_lock: LockProtocol = field(default_factory=threading.RLock, repr=False)
    workflow_lock: LockProtocol = field(default_factory=threading.RLock, repr=False)
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
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    follow_fork_mode: FollowForkMode | None = None
    detach_on_fork: bool | None = None
    artifact_root: str | None = None
    last_stop_event: StopEvent | None = None
    stop_history: list[StopEvent] = field(default_factory=list)
    stop_history_limit: int = DEFAULT_STOP_HISTORY_LIMIT
    command_transcript: list[CommandTranscriptEntry] = field(default_factory=list)
    command_transcript_limit: int = DEFAULT_COMMAND_TRANSCRIPT_LIMIT

    @property
    def controller(self) -> GdbControllerProtocol | None:
        """Expose the transport controller for compatibility and tests."""

        return self.transport.controller

    @controller.setter
    def controller(self, value: GdbControllerProtocol | None) -> None:
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
        self.current_inferior_id = None
        self.inferior_count = None
        self.follow_fork_mode = None
        self.detach_on_fork = None
        self.last_stop_event = None
        self.stop_history.clear()
        self.command_transcript.clear()

    def mark_ready(self) -> None:
        """Mark the session as ready for requests."""

        self.state = SessionState.READY
        self.is_running = True
        self.last_failure_message = None
        if self.current_inferior_id is None:
            self.current_inferior_id = 1
        if self.inferior_count is None:
            self.inferior_count = 1
        if self.follow_fork_mode is None:
            self.follow_fork_mode = "parent"
        if self.detach_on_fork is None:
            self.detach_on_fork = True

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
        self.current_inferior_id = None
        self.inferior_count = None
        self.follow_fork_mode = None
        self.detach_on_fork = None

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
        self.current_inferior_id = None
        self.inferior_count = None
        self.follow_fork_mode = None
        self.detach_on_fork = None

    def mark_thread_selected(self, thread_id: int | None) -> None:
        """Track the currently selected thread."""

        self.current_thread_id = thread_id

    def mark_frame_selected(self, frame_number: int | None) -> None:
        """Track the currently selected frame."""

        self.current_frame = frame_number

    def mark_inferior_selected(self, inferior_id: int | None) -> None:
        """Track the currently selected inferior and clear stale stack selection."""

        self.current_inferior_id = inferior_id
        self.current_thread_id = None
        self.current_frame = None

    def update_inferior_inventory(
        self,
        *,
        current_inferior_id: int | None,
        count: int,
    ) -> None:
        """Track the current inferior inventory snapshot."""

        self.current_inferior_id = current_inferior_id
        self.inferior_count = count

    def mark_follow_fork_mode(self, mode: FollowForkMode | None) -> None:
        """Track the configured follow-fork-mode value."""

        self.follow_fork_mode = mode

    def mark_detach_on_fork(self, enabled: bool | None) -> None:
        """Track the configured detach-on-fork value."""

        self.detach_on_fork = enabled

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

    def record_stop_event(self, event: StopEvent) -> None:
        """Remember a structured stop event and keep bounded history."""

        self.last_stop_event = event
        self.stop_history.append(event)
        self._trim_history(self.stop_history, self.stop_history_limit)

    def record_command_transcript(self, entry: CommandTranscriptEntry) -> None:
        """Remember command execution metadata and keep bounded transcript history."""

        self.command_transcript.append(entry)
        self._trim_history(self.command_transcript, self.command_transcript_limit)

    @staticmethod
    def _trim_history(items: list[HistoryItemT], limit: int) -> None:
        """Trim one history list to the configured maximum size."""

        if limit < 1:
            items.clear()
            return

        overflow = len(items) - limit
        if overflow > 0:
            del items[:overflow]
