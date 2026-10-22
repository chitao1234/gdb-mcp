"""LLDB backend driven exclusively through the native SB API bridge."""

from __future__ import annotations

import signal
import time
from collections.abc import Mapping
from typing import Literal

from ..domain import (
    BackendData,
    ExecutionState,
    SessionStatusSnapshot,
    StopEvent,
    StructuredPayload,
)
from ..domain.parsing import as_execution_state, as_stop_kind
from ..domain.results import payload_to_mapping
from .lldb_breakpoints import LldbBreakpoints
from .lldb_values import frame as frame_record
from .lldb_values import integer, text
from .runtime import SessionRuntime


class LldbEvents:
    """Interpret LLDB status and retain stop-event identity."""

    def __init__(self, runtime: SessionRuntime, breakpoints: LldbBreakpoints) -> None:
        self.runtime = runtime
        self._breakpoints = breakpoints
        self.state_detail: Literal["crashed"] | None = None
        self._last_stop_key: tuple[object, ...] | None = None

    def reset(self) -> None:
        self.state_detail = None
        self._last_stop_key = None

    def apply(self, payload: Mapping[str, object]) -> SessionStatusSnapshot:
        execution = as_execution_state(text(payload, "execution_state")) or "unknown"
        detail = text(payload, "state_detail")
        self.state_detail = "crashed" if detail == "crashed" else None
        reason = text(payload, "stop_reason")
        stop_kind = as_stop_kind(payload.get("stop_kind"))
        exit_code = integer(payload, "exit_code")
        target_loaded = bool(payload.get("target_loaded"))
        current_inferior = integer(payload, "current_inferior_id")
        inferior_count = integer(payload, "inferior_count")
        self.runtime.mark_target_loaded(target_loaded)
        if inferior_count is not None:
            self.runtime.update_inferior_inventory(
                current_inferior_id=current_inferior,
                count=inferior_count,
                inferior_ids=tuple(range(1, inferior_count + 1)),
            )
        current_thread = integer(payload, "current_thread_id")
        current_frame = integer(payload, "current_frame")
        self.runtime.mark_thread_selected(current_thread)
        self.runtime.mark_frame_selected(current_frame)
        if execution == "paused":
            self.runtime.mark_inferior_paused(reason, stop_kind=stop_kind)
        elif execution == "running":
            self.runtime.mark_inferior_running()
        elif execution == "exited":
            self.runtime.mark_inferior_exited(reason, exit_code)
        elif execution == "not_started":
            self.runtime.mark_inferior_not_started()
        self.runtime.mark_debugger_running(True)
        self._record_stop(payload, execution)
        return SessionStatusSnapshot(
            debugger_alive=True,
            target_loaded=target_loaded,
            session_state=self.runtime.state,
            execution_state=execution,
            state_detail=self.state_detail,
            stop_reason=reason,
            stop_kind=stop_kind,
            exit_code=exit_code,
            current_inferior_id=current_inferior,
            inferior_count=inferior_count,
            inferior_states=self.runtime.inferiors_state_summary() or None,
            follow_fork_mode=self.runtime.follow_fork_mode,
            detach_on_fork=self.runtime.detach_on_fork,
        )

    def _record_stop(self, payload: Mapping[str, object], execution: ExecutionState) -> None:
        if execution not in {"paused", "exited"}:
            return
        reason = text(payload, "stop_reason")
        stop_frame = frame_record(payload.get("frame"))
        breakpoint_id = integer(payload, "breakpoint_id")
        watchpoint_id = integer(payload, "watchpoint_id")
        key = (
            integer(payload, "pid"),
            integer(payload, "current_inferior_id"),
            integer(payload, "stop_id"),
            reason,
            integer(payload, "current_thread_id"),
            stop_frame.addr if stop_frame is not None else None,
            integer(payload, "exit_code"),
            breakpoint_id,
            watchpoint_id,
            text(payload, "state_detail"),
        )
        if self._last_stop_key == key:
            return
        self._last_stop_key = key
        signal_number = integer(payload, "signal")
        signal_name = text(payload, "signal_name")
        signal_meaning: str | None = None
        if signal_number is not None:
            try:
                signal_meaning = signal.strsignal(signal_number)
            except (ValueError, AttributeError):
                signal_meaning = None
        crash = payload.get("crash")
        details: StructuredPayload = {}
        if signal_number is not None:
            details["signal_number"] = signal_number
        if isinstance(crash, dict):
            exception_details = {
                name: value
                for name, value in crash.items()
                if name
                not in {
                    "signal",
                    "signal_name",
                    "signal_meaning",
                    "code",
                    "signal_code",
                    "fault_address",
                }
            }
            if exception_details:
                details["exception"] = payload_to_mapping(exception_details)
        for name in ("stop_data", "pid"):
            if name in payload:
                details[name] = payload_to_mapping(payload[name])
        if breakpoint_id is not None:
            details["native_breakpoint_id"] = breakpoint_id
        if watchpoint_id is not None:
            details["native_watchpoint_id"] = watchpoint_id
        breakpoint_number: str | None = None
        if watchpoint_id is not None:
            breakpoint_number = str(
                self._breakpoints.public_number(("watchpoint", watchpoint_id), watchpoint_id)
            )
        elif breakpoint_id is not None:
            breakpoint_number = str(
                self._breakpoints.public_number(("breakpoint", breakpoint_id), breakpoint_id)
            )
        event = StopEvent(
            execution_state=execution,
            state_detail=self.state_detail,
            reason=reason,
            stop_kind=as_stop_kind(payload.get("stop_kind")),
            inferior_id=integer(payload, "current_inferior_id"),
            thread_id=integer(payload, "current_thread_id"),
            frame=stop_frame,
            signal_name=signal_name,
            signal_meaning=signal_meaning,
            signal_code=integer(payload, "signal_code"),
            fault_address=text(payload, "fault_address"),
            breakpoint_number=breakpoint_number,
            exit_code=integer(payload, "exit_code"),
            timestamp=time.time(),
            backend_data=(
                BackendData(debugger="lldb", console_output="", data=details) if details else None
            ),
        )
        self.runtime.record_stop_event(event)
