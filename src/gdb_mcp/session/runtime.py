"""Authoritative mutable runtime state for one debugger session."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from ..domain import (
    CommandTranscriptEntry,
    ExecutionState,
    FollowForkMode,
    InferiorRecord,
    InferiorStateRecord,
    SessionStatusSnapshot,
    StopEvent,
    StopKind,
    stop_kind_from_reason,
)
from .config import SessionConfig
from .constants import DEFAULT_COMMAND_TRANSCRIPT_LIMIT, DEFAULT_STOP_HISTORY_LIMIT
from .protocols import LockProtocol, OsModuleProtocol, TimeModuleProtocol
from .state import SessionState

HistoryItemT = TypeVar("HistoryItemT")


@dataclass(slots=True)
class InferiorRuntimeState:
    """Mutable runtime execution state for one inferior."""

    execution_state: ExecutionState = "unknown"
    stop_reason: str | None = None
    exit_code: int | None = None
    refined_stop_kind: tuple[str | None, StopKind] | None = None
    state_detail: Literal["crashed"] | None = None

    @property
    def stop_kind(self) -> StopKind:
        refined = self.refined_stop_kind
        if refined is not None and refined[0] == self.stop_reason:
            return refined[1]
        return stop_kind_from_reason(self.stop_reason)


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    """Decoded debugger state applied as one runtime transition."""

    execution_state: ExecutionState
    target_loaded: bool
    current_inferior_id: int | None
    inferior_count: int | None
    current_thread_id: int | None
    current_frame: int | None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
    state_detail: Literal["crashed"] | None = None
    stop_event: StopEvent | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """One observation of session metadata and bounded execution history."""

    status: SessionStatusSnapshot
    config: SessionConfig | None
    attached_pid: int | None
    current_thread_id: int | None
    current_frame: int | None
    last_failure_message: str | None
    last_stop_event: StopEvent | None
    stop_history: tuple[StopEvent, ...]
    command_transcript: tuple[CommandTranscriptEntry, ...]


@dataclass(slots=True)
class SessionRuntime:
    """Single source of truth for common session and inferior state."""

    os_module: OsModuleProtocol
    time_module: TimeModuleProtocol
    lifecycle_lock: LockProtocol = field(default_factory=threading.RLock, repr=False)
    workflow_lock: LockProtocol = field(default_factory=threading.RLock, repr=False)
    config: SessionConfig | None = None
    state: SessionState = SessionState.CREATED
    is_running: bool = False
    debugger: str = "gdb"
    target_loaded: bool = False
    # Interrupts go through MI when the debugger child cannot be signalled
    # (Windows) or does not exist (an external endpoint); startup sets this.
    mi_async_interrupt: bool = False
    attached_pid: int | None = None
    last_failure_message: str | None = None
    current_thread_id: int | None = None
    current_frame: int | None = None
    current_inferior_id: int | None = None
    inferior_count: int | None = None
    inferior_states: dict[int, InferiorRuntimeState] = field(default_factory=dict)
    _selected_state: InferiorRuntimeState = field(default_factory=InferiorRuntimeState, repr=False)
    follow_fork_mode: FollowForkMode | None = None
    detach_on_fork: bool | None = None
    artifact_root: str | None = None
    last_stop_event: StopEvent | None = None
    stop_history: list[StopEvent] = field(default_factory=list)
    stop_history_limit: int = DEFAULT_STOP_HISTORY_LIMIT
    command_transcript: list[CommandTranscriptEntry] = field(default_factory=list)
    command_transcript_limit: int = DEFAULT_COMMAND_TRANSCRIPT_LIMIT
    # Leaf lock guarding the container state below. No other lock is taken while
    # it is held, so it cannot participate in a lock-order cycle.
    _state_lock: LockProtocol = field(default_factory=threading.RLock, repr=False, compare=False)

    @property
    def execution_state(self) -> ExecutionState:
        return self._selected_state.execution_state

    @execution_state.setter
    def execution_state(self, state: ExecutionState) -> None:
        with self._state_lock:
            self._selected_state.execution_state = state

    @property
    def stop_reason(self) -> str | None:
        return self._selected_state.stop_reason

    @stop_reason.setter
    def stop_reason(self, reason: str | None) -> None:
        with self._state_lock:
            self._selected_state.stop_reason = reason

    @property
    def exit_code(self) -> int | None:
        return self._selected_state.exit_code

    @exit_code.setter
    def exit_code(self, code: int | None) -> None:
        with self._state_lock:
            self._selected_state.exit_code = code

    @property
    def stop_kind(self) -> StopKind:
        """Canonical kind of the current stop reason.

        A backend whose protocol loses a distinction (DAP reports a watchpoint as
        a plain breakpoint) records the refined kind with the stop it belongs to;
        it is used only while that stop reason is current.
        """

        return self._selected_state.stop_kind

    def begin_startup(self, config: SessionConfig) -> None:
        """Record a startup attempt and reset transient state."""

        with self._state_lock:
            self.config = config
            self.debugger = config.debugger
            self.state = SessionState.STARTING
            self.last_failure_message = None
            self._reset_transient()
            self.last_stop_event = None
            self.stop_history.clear()
            self.command_transcript.clear()

    def _reset_transient(self) -> None:
        """Clear per-run transient state shared by startup, stop, and failure.

        Callers must already hold ``self._state_lock``.
        """

        self.is_running = False
        self.target_loaded = False
        self.attached_pid = None
        self._selected_state = InferiorRuntimeState()
        self.current_thread_id = None
        self.current_frame = None
        self.current_inferior_id = None
        self.inferior_count = None
        self.inferior_states.clear()
        self.follow_fork_mode = None
        self.detach_on_fork = None
        # Async MI belongs to the debugger child that enabled it, so a new
        # startup or a stopped session must fall back to the platform default.
        self.mi_async_interrupt = False

    def mark_ready(self) -> None:
        """Mark the session as ready for requests."""

        with self._state_lock:
            self.state = SessionState.READY
            self.is_running = True
            self.last_failure_message = None
            if self.current_inferior_id is None:
                self.current_inferior_id = 1
            if self.inferior_count is None:
                self.inferior_count = 1
            if self.current_inferior_id not in self.inferior_states:
                self.inferior_states[self.current_inferior_id] = self._selected_state
            self._synchronize_selected_state()
            if self.follow_fork_mode is None:
                self.follow_fork_mode = "parent"
            if self.detach_on_fork is None:
                self.detach_on_fork = True

    def mark_failed(self, message: str) -> None:
        """Record a terminal failure for the current session."""

        with self._state_lock:
            self.state = SessionState.FAILED
            self.last_failure_message = message
            self._reset_transient()

    def mark_transport_terminated(self, message: str) -> None:
        """Record that the debugger transport has died."""

        self.mark_failed(message)

    def record_failure_message(self, message: str) -> None:
        with self._state_lock:
            self.last_failure_message = message

    def mark_stopped(self) -> None:
        """Record a clean stop of the current session."""

        with self._state_lock:
            self.state = SessionState.STOPPED
            self.last_failure_message = None
            self._reset_transient()

    def mark_thread_selected(self, thread_id: int | None) -> None:
        """Track the currently selected thread."""

        with self._state_lock:
            self.current_thread_id = thread_id

    def mark_frame_selected(self, frame_number: int | None) -> None:
        """Track the currently selected frame."""

        with self._state_lock:
            self.current_frame = frame_number

    def mark_inferior_selected(self, inferior_id: int | None) -> None:
        """Track the currently selected inferior and clear stale stack selection."""

        with self._state_lock:
            self.current_inferior_id = inferior_id
            self.current_thread_id = None
            self.current_frame = None
            if inferior_id is not None:
                self.ensure_inferior(inferior_id)
            self._synchronize_selected_state()

    def update_inferior_inventory(
        self,
        *,
        current_inferior_id: int | None,
        count: int,
        inferior_ids: tuple[int, ...] | None = None,
    ) -> None:
        """Track the current inferior inventory snapshot."""

        with self._state_lock:
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
            if self.current_inferior_id is None and self.inferior_states:
                self.current_inferior_id = min(self.inferior_states)
            if self.current_inferior_id is not None:
                self.ensure_inferior(self.current_inferior_id)
            self._synchronize_selected_state()

    def ensure_inferior(self, inferior_id: int) -> None:
        """Ensure one inferior has an allocated runtime state record."""

        if inferior_id <= 0:
            return
        with self._state_lock:
            if inferior_id not in self.inferior_states:
                self.inferior_states[inferior_id] = InferiorRuntimeState()
            if self.inferior_count is None or self.inferior_count < len(self.inferior_states):
                self.inferior_count = len(self.inferior_states)

    def apply_inferior_inventory(
        self, records: list[InferiorRecord], *, current_inferior_id: int | None
    ) -> None:
        """Apply an inventory without erasing stop details it does not report."""

        with self._state_lock:
            self.update_inferior_inventory(
                current_inferior_id=current_inferior_id,
                count=len(records),
                inferior_ids=tuple(
                    record.inferior_id for record in records if record.inferior_id is not None
                ),
            )
            for record in records:
                if record.inferior_id is None or record.execution_state is None:
                    continue
                state = self.inferior_states[record.inferior_id]
                if state.execution_state != record.execution_state:
                    self._update_inferior_state(
                        inferior_id=record.inferior_id,
                        execution_state=record.execution_state,
                        stop_reason=record.stop_reason,
                        exit_code=record.exit_code,
                        refined_stop_kind=record.stop_kind,
                    )
                    continue
                if "stop_reason" in record.model_fields_set:
                    if record.stop_reason != state.stop_reason:
                        state.state_detail = None
                        state.refined_stop_kind = None
                    state.stop_reason = record.stop_reason
                if "exit_code" in record.model_fields_set:
                    state.exit_code = record.exit_code
                if "stop_kind" in record.model_fields_set:
                    if record.stop_kind != state.stop_kind:
                        state.state_detail = None
                    state.refined_stop_kind = None
                    if record.stop_kind is not None:
                        state.refined_stop_kind = (state.stop_reason, record.stop_kind)

    def remove_inferior(self, inferior_id: int) -> None:
        """Remove one inferior from runtime tracking after thread-group removal."""

        with self._state_lock:
            if inferior_id not in self.inferior_states:
                return

            del self.inferior_states[inferior_id]
            self.inferior_count = len(self.inferior_states)

            if self.current_inferior_id == inferior_id:
                self.current_inferior_id = (
                    min(self.inferior_states) if self.inferior_states else None
                )
                self.current_thread_id = None
                self.current_frame = None

            self._synchronize_selected_state()

    def mark_follow_fork_mode(self, mode: FollowForkMode | None) -> None:
        """Track the configured follow-fork-mode value."""

        with self._state_lock:
            self.follow_fork_mode = mode

    def mark_detach_on_fork(self, enabled: bool | None) -> None:
        """Track the configured detach-on-fork value."""

        with self._state_lock:
            self.detach_on_fork = enabled

    def mark_attached(self, pid: int) -> None:
        """Track the PID of the currently attached process."""

        with self._state_lock:
            self.attached_pid = pid

    def clear_attached_pid(self) -> None:
        """Clear any remembered attached process PID."""

        with self._state_lock:
            self.attached_pid = None

    def mark_target_loaded(self, loaded: bool) -> None:
        with self._state_lock:
            self.target_loaded = loaded

    def mark_debugger_running(self, running: bool) -> None:
        with self._state_lock:
            self.is_running = running

    def mark_inferior_not_started(self, inferior_id: int | None = None) -> None:
        """Record that a target is loaded but execution has not begun."""

        self._update_inferior_state(
            inferior_id=inferior_id,
            execution_state="not_started",
            stop_reason=None,
            exit_code=None,
        )

    def begin_execution(self) -> None:
        with self._state_lock:
            self.current_frame = None
            self.mark_inferior_running()

    def apply_execution_observation(
        self, observation: ExecutionObservation
    ) -> SessionStatusSnapshot:
        with self._state_lock:
            self.target_loaded = observation.target_loaded
            self.is_running = True
            if observation.inferior_count is not None:
                self.update_inferior_inventory(
                    current_inferior_id=observation.current_inferior_id,
                    count=observation.inferior_count,
                    inferior_ids=tuple(range(1, observation.inferior_count + 1)),
                )
            self.current_thread_id = observation.current_thread_id
            self.current_frame = observation.current_frame
            state = InferiorRuntimeState(
                execution_state=observation.execution_state,
                stop_reason=observation.stop_reason,
                exit_code=observation.exit_code,
                state_detail=observation.state_detail,
            )
            if observation.stop_kind is not None:
                state.refined_stop_kind = (observation.stop_reason, observation.stop_kind)
            self._selected_state = state
            if self.current_inferior_id is not None:
                self.inferior_states[self.current_inferior_id] = state
            if observation.stop_event is not None:
                self.record_stop_event(observation.stop_event)
            return self.status_snapshot(debugger_alive=True)

    def apply_stop_event(self, event: StopEvent) -> None:
        """Apply a GDB stop notification and its selection as one transition."""

        with self._state_lock:
            if event.thread_id is not None:
                self.current_thread_id = event.thread_id
            if event.frame is not None and event.frame.level is not None:
                self.current_frame = event.frame.level
            self._update_inferior_state(
                inferior_id=event.inferior_id,
                execution_state=event.execution_state,
                stop_reason=event.reason,
                exit_code=event.exit_code,
                refined_stop_kind=event.stop_kind,
                state_detail=event.state_detail,
            )
            self.record_stop_event(event)

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
        stop_kind: StopKind | None = None,
        inferior_id: int | None = None,
    ) -> None:
        """Record that the inferior is paused and inspectable.

        ``stop_kind`` refines the canonical kind when the native reason cannot say
        it (DAP calls a watchpoint stop ``breakpoint``).
        """

        self._update_inferior_state(
            inferior_id=inferior_id,
            execution_state="paused",
            stop_reason=reason,
            exit_code=None,
            refined_stop_kind=stop_kind,
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

        with self._state_lock:
            records: list[InferiorStateRecord] = []
            for inferior_id in sorted(self.inferior_states):
                state = self.inferior_states[inferior_id]
                records.append(
                    InferiorStateRecord(
                        inferior_id=inferior_id,
                        execution_state=state.execution_state,
                        stop_reason=state.stop_reason,
                        stop_kind=state.stop_kind,
                        exit_code=state.exit_code,
                    )
                )
            return records

    def status_snapshot(self, *, debugger_alive: bool) -> SessionStatusSnapshot:
        """Read common status fields under the runtime state lock."""

        with self._state_lock:
            return SessionStatusSnapshot(
                debugger_alive=debugger_alive,
                target_loaded=self.target_loaded,
                session_state=self.state,
                execution_state=self.execution_state,
                state_detail=self._selected_state.state_detail,
                stop_reason=self.stop_reason,
                stop_kind=self.stop_kind,
                exit_code=self.exit_code,
                current_inferior_id=self.current_inferior_id,
                inferior_count=self.inferior_count,
                inferior_states=self.inferiors_state_summary() or None,
                follow_fork_mode=self.follow_fork_mode,
                detach_on_fork=self.detach_on_fork,
            )

    def snapshot(self, *, debugger_alive: bool) -> RuntimeSnapshot:
        with self._state_lock:
            return RuntimeSnapshot(
                status=self.status_snapshot(debugger_alive=debugger_alive),
                config=self.config,
                attached_pid=self.attached_pid,
                current_thread_id=self.current_thread_id,
                current_frame=self.current_frame,
                last_failure_message=self.last_failure_message,
                last_stop_event=self.last_stop_event,
                stop_history=tuple(self.stop_history),
                command_transcript=tuple(self.command_transcript),
            )

    def record_stop_event(self, event: StopEvent) -> None:
        """Remember a structured stop event and keep bounded history."""

        with self._state_lock:
            self.last_stop_event = event
            self.stop_history.append(event)
            self._trim_history(self.stop_history, self.stop_history_limit)

    def record_command_transcript(self, entry: CommandTranscriptEntry) -> None:
        """Remember command execution metadata and keep bounded transcript history."""

        with self._state_lock:
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
        execution_state: ExecutionState,
        stop_reason: str | None,
        exit_code: int | None,
        refined_stop_kind: StopKind | None = None,
        state_detail: Literal["crashed"] | None = None,
    ) -> None:
        """Update the execution state owned by one inferior."""

        with self._state_lock:
            target_inferior_id = self._resolve_target_inferior_id(inferior_id)
            state = self.inferior_states.get(target_inferior_id)
            if state is None:
                state = InferiorRuntimeState()
                self.inferior_states[target_inferior_id] = state

            state.execution_state = execution_state
            state.stop_reason = stop_reason
            state.exit_code = exit_code
            state.state_detail = state_detail
            state.refined_stop_kind = (
                (stop_reason, refined_stop_kind) if refined_stop_kind is not None else None
            )

            if self.inferior_count is None or self.inferior_count < len(self.inferior_states):
                self.inferior_count = len(self.inferior_states)

            if target_inferior_id == self.current_inferior_id:
                self._selected_state = state

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
        """Point selected status at its inferior's authoritative state record.

        Callers must already hold ``self._state_lock``.
        """

        if self.current_inferior_id is None:
            self._selected_state = InferiorRuntimeState()
            return

        state = self.inferior_states.get(self.current_inferior_id)
        if state is None:
            state = InferiorRuntimeState()
            self.inferior_states[self.current_inferior_id] = state

        self._selected_state = state
