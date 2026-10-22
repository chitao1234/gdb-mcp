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
from .runtime import ExecutionObservation, SessionRuntime


class LldbEvents:
    """Interpret LLDB status and retain stop-event identity."""

    def __init__(self, runtime: SessionRuntime, breakpoints: LldbBreakpoints) -> None:
        self.runtime = runtime
        self._breakpoints = breakpoints
        self._last_stop_key: tuple[object, ...] | None = None

    def reset(self) -> None:
        self._last_stop_key = None

    def apply(self, payload: Mapping[str, object]) -> SessionStatusSnapshot:
        execution = as_execution_state(text(payload, "execution_state")) or "unknown"
        detail = text(payload, "state_detail")
        state_detail: Literal["crashed"] | None = "crashed" if detail == "crashed" else None
        reason = text(payload, "stop_reason")
        stop_kind = as_stop_kind(payload.get("stop_kind"))
        exit_code = integer(payload, "exit_code")
        target_loaded = bool(payload.get("target_loaded"))
        current_inferior = integer(payload, "current_inferior_id")
        inferior_count = integer(payload, "inferior_count")
        current_thread = integer(payload, "current_thread_id")
        current_frame = integer(payload, "current_frame")
        event = self._build_stop(payload, execution, state_detail)
        key: tuple[object, ...] = ()
        if event is not None:
            key = (
                integer(payload, "pid"),
                current_inferior,
                integer(payload, "stop_id"),
                reason,
                current_thread,
                event.frame.addr if event.frame is not None else None,
                exit_code,
                integer(payload, "breakpoint_id"),
                integer(payload, "watchpoint_id"),
                detail,
            )
            if self._last_stop_key == key:
                event = None
        status = self.runtime.apply_execution_observation(
            ExecutionObservation(
                execution_state=execution,
                target_loaded=target_loaded,
                current_inferior_id=current_inferior,
                inferior_count=inferior_count,
                current_thread_id=current_thread,
                current_frame=current_frame,
                state_detail=state_detail,
                stop_reason=reason,
                stop_kind=stop_kind,
                exit_code=exit_code,
                stop_event=event,
            )
        )
        if event is not None:
            self._last_stop_key = key
        return status

    def _build_stop(
        self,
        payload: Mapping[str, object],
        execution: ExecutionState,
        state_detail: Literal["crashed"] | None,
    ) -> StopEvent | None:
        if execution not in {"paused", "exited"}:
            return None
        reason = text(payload, "stop_reason")
        stop_frame = frame_record(payload.get("frame"))
        breakpoint_id = integer(payload, "breakpoint_id")
        watchpoint_id = integer(payload, "watchpoint_id")
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
        return StopEvent(
            execution_state=execution,
            state_detail=state_detail,
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
