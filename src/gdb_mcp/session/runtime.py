"""Authoritative mutable runtime state for one debugger session."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import TypeVar

from ..domain import (
    CommandTranscriptEntry,
    FollowForkMode,
    InferiorStateRecord,
    StopEvent,
)
from ..transport import MiClient
from ..transport.protocols import GdbControllerProtocol
from .config import SessionConfig
from .constants import DEFAULT_COMMAND_TRANSCRIPT_LIMIT, DEFAULT_STOP_HISTORY_LIMIT
from .protocols import LockProtocol, OsModuleProtocol, TimeModuleProtocol
from .state import SessionState

HistoryItemT = TypeVar("HistoryItemT")


@dataclass(slots=True)
class InferiorRuntimeState:
    """Mutable runtime execution state for one inferior."""

    execution_state: str = "unknown"
    stop_reason: str | None = None
    exit_code: int | None = None


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
    inferior_states: dict[int, InferiorRuntimeState] = field(default_factory=dict)
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
        self.inferior_states.clear()
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
        if self.current_inferior_id not in self.inferior_states:
            self.inferior_states[self.current_inferior_id] = InferiorRuntimeState(
                execution_state=self.execution_state,
                stop_reason=self.stop_reason,
                exit_code=self.exit_code,
            )
        self._synchronize_selected_state()
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
        self.inferior_states.clear()
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
        self.inferior_states.clear()
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
        if inferior_id is not None and inferior_id not in self.inferior_states:
            self.inferior_states[inferior_id] = InferiorRuntimeState()
        self._synchronize_selected_state()

    def update_inferior_inventory(
        self,
        *,
        current_inferior_id: int | None,
        count: int,
        inferior_ids: tuple[int, ...] | None = None,
    ) -> None:
        """Track the current inferior inventory snapshot."""

        if inferior_ids is not None:
            normalized_states: dict[int, InferiorRuntimeState] = {}
            for inferior_id in inferior_ids:
                existing = self.inferior_states.get(inferior_id)
                normalized_states[inferior_id] = (
                    existing if existing is not None else InferiorRuntimeState()
                )
            self.inferior_states = normalized_states

        self.current_inferior_id = current_inferior_id
        self.inferior_count = count
        if current_inferior_id is not None and current_inferior_id not in self.inferior_states:
            self.inferior_states[current_inferior_id] = InferiorRuntimeState()
        self._synchronize_selected_state()

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

    def mark_inferior_not_started(self, inferior_id: int | None = None) -> None:
        """Record that a target is loaded but execution has not begun."""

        self._update_inferior_state(
            inferior_id=inferior_id,
            execution_state="not_started",
            stop_reason=None,
            exit_code=None,
        )

    def mark_inferior_running(self, inferior_id: int | None = None) -> None:
        """Record that the inferior is currently running."""

        self._update_inferior_state(
            inferior_id=inferior_id,
            execution_state="running",
            stop_reason=None,
            exit_code=None,
        )

    def mark_inferior_paused(
        self,
        reason: str | None = None,
        *,
        inferior_id: int | None = None,
    ) -> None:
        """Record that the inferior is paused and inspectable."""

        self._update_inferior_state(
            inferior_id=inferior_id,
            execution_state="paused",
            stop_reason=reason,
            exit_code=None,
        )

    def mark_inferior_exited(
        self,
        reason: str | None = None,
        exit_code: int | None = None,
        *,
        inferior_id: int | None = None,
    ) -> None:
        """Record that the inferior has exited while the session remains alive."""

        self._update_inferior_state(
            inferior_id=inferior_id,
            execution_state="exited",
            stop_reason=reason,
            exit_code=exit_code,
        )

    def inferiors_state_summary(self) -> list[InferiorStateRecord]:
        """Return sorted runtime state summaries for all known inferiors."""

        records: list[InferiorStateRecord] = []
        for inferior_id in sorted(self.inferior_states):
            state = self.inferior_states[inferior_id]
            records.append(
                InferiorStateRecord(
                    inferior_id=inferior_id,
                    is_current=inferior_id == self.current_inferior_id,
                    execution_state=state.execution_state,
                    stop_reason=state.stop_reason,
                    exit_code=state.exit_code,
                )
            )
        return records

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

    def _update_inferior_state(
        self,
        *,
        inferior_id: int | None,
        execution_state: str,
        stop_reason: str | None,
        exit_code: int | None,
    ) -> None:
        """Update one inferior state and mirror it to selected-global fields when applicable."""

        target_inferior_id = self._resolve_target_inferior_id(inferior_id)
        state = self.inferior_states.get(target_inferior_id)
        if state is None:
            state = InferiorRuntimeState()
            self.inferior_states[target_inferior_id] = state

        state.execution_state = execution_state
        state.stop_reason = stop_reason
        state.exit_code = exit_code

        if self.inferior_count is None or self.inferior_count < len(self.inferior_states):
            self.inferior_count = len(self.inferior_states)

        if target_inferior_id == self.current_inferior_id:
            self.execution_state = execution_state
            self.stop_reason = stop_reason
            self.exit_code = exit_code

    def _resolve_target_inferior_id(self, inferior_id: int | None) -> int:
        """Resolve the target inferior for a state update."""

        if inferior_id is not None:
            if self.current_inferior_id is None:
                self.current_inferior_id = inferior_id
            return inferior_id

        if self.current_inferior_id is None:
            self.current_inferior_id = 1
        return self.current_inferior_id

    def _synchronize_selected_state(self) -> None:
        """Mirror the selected inferior runtime state into top-level fields."""

        if self.current_inferior_id is None:
            self.execution_state = "unknown"
            self.stop_reason = None
            self.exit_code = None
            return

        state = self.inferior_states.get(self.current_inferior_id)
        if state is None:
            state = InferiorRuntimeState()
            self.inferior_states[self.current_inferior_id] = state

        self.execution_state = state.execution_state
        self.stop_reason = state.stop_reason
        self.exit_code = state.exit_code
