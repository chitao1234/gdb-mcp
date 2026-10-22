"""Go debugging through Delve's streaming JSON-RPC API v2."""

from __future__ import annotations

import json
import threading
import time
from typing import Protocol

from ..contracts import ACTION_JUMP
from ..domain import (
    BackendData,
    CommandExecutionInfo,
    CommandTranscriptEntry,
    ExecutionState,
    FinishInfo,
    FunctionCallInfo,
    LocationExecutionInfo,
    MessageResult,
    OperationSuccess,
    StopEvent,
    StopKind,
    WaitForStopInfo,
    stop_kind_from_reason,
)
from ..domain.errors import DebuggerDecodeError
from ..transport.delve_rpc import DelveRpcClient, DelveRpcError, RpcRequest
from .backends import BackendOperation
from .constants import DEFAULT_TIMEOUT_SEC
from .delve_breakpoints import DelveBreakpoints
from .delve_inspection import DelveInspection
from .delve_protocols import VARIABLE_LOAD_CONFIG, DelveRequests
from .delve_protocols import operation as _operation
from .delve_values import (
    frame_record,
    integer,
    mapping,
    mappings,
    optional_integer,
    text,
    variable_value,
)
from .requests import (
    DEFAULT_EXECUTION_OPTIONS,
    ExecutionOptions,
)
from .runtime import ExecutionObservation, SessionRuntime
from .state import SessionState


class DelveExecutionBackend(DelveRequests, Protocol):
    _attached: bool
    _client: DelveRpcClient | None
    _unsupported: set[BackendOperation]

    def _data(self, values: dict[str, object]) -> BackendData: ...


class DelveExecution:
    """Own Delve's asynchronous execution state, completion, and stop events."""

    def __init__(
        self,
        runtime: SessionRuntime,
        bridge: DelveExecutionBackend,
        inspection: DelveInspection,
        breakpoints: DelveBreakpoints,
    ) -> None:
        self.runtime = runtime
        self._bridge = bridge
        self._inspection = inspection
        self._breakpoints = breakpoints
        self._command_lock = threading.RLock()
        self.done = threading.Event()
        self.done.set()
        self._execution_error: Exception | None = None
        self._execution_result: dict[str, object] = {}
        self._pending: RpcRequest | None = None
        self.last_state: dict[str, object] = {}
        self._last_stop_key: tuple[object, ...] | None = None
        self._generation = 0
        self._command = ""
        self._has_run = False
        self._interrupt_requested = False

    def reset(self) -> None:
        self._has_run = False
        self.last_state = {}
        self._last_stop_key = None
        self._command = ""
        self._pending = None
        self._execution_error = None
        self._execution_result = {}
        self.done = threading.Event()
        self.done.set()

    def cancel_generation(self) -> None:
        with self._command_lock:
            self._generation += 1

    def _client_state(self, response: dict[str, object], *, record: bool = True) -> None:
        state = mapping(response.get("State"))
        if not state:
            raise DelveRpcError("Delve returned no debugger state", "protocol_error")
        with self._command_lock:
            if (
                not record
                and self._pending is not None
                and not self.done.is_set()
                and not state.get("Running")
            ):
                return
            thread = mapping(state.get("currentThread"))
            goroutine = mapping(state.get("currentGoroutine"))
            goroutine_id = integer(goroutine.get("id"), 0) or integer(thread.get("goroutineID"), 0)
            snapshot = self.runtime.snapshot(debugger_alive=True)
            current_frame = snapshot.current_frame
            if snapshot.current_thread_id != (goroutine_id or None) or current_frame is None:
                current_frame = 0
            if state.get("Running"):
                self.runtime.apply_execution_observation(
                    ExecutionObservation(
                        execution_state="running",
                        target_loaded=True,
                        current_inferior_id=1,
                        inferior_count=1,
                        current_thread_id=goroutine_id or None,
                        current_frame=current_frame,
                    )
                )
                self.last_state = state
                return
            breakpoint = mapping(thread.get("breakPoint"))
            native_reason = text(state.get("stopReason"))
            reason = native_reason
            execution: ExecutionState = "paused"
            canonical: StopKind | None = None
            exit_code: int | None = None
            if state.get("exited"):
                reason = reason or "exited"
                execution = "exited"
                exit_code = optional_integer(state.get("exitStatus"))
            else:
                if optional_integer(breakpoint.get("id")) == -1:
                    reason = "panic"
                elif breakpoint:
                    reason = "watchpoint" if breakpoint.get("WatchExpr") else "breakpoint-hit"
                elif self._interrupt_requested:
                    reason = "interrupt"
                elif not reason:
                    reason = (
                        "step"
                        if self._command
                        in {"step", "next", "stepOut", "stepInstruction", "nextInstruction"}
                        else "entry" if not self._has_run else "unknown"
                    )
                kinds: dict[str, StopKind] = {
                    "launched": "entry",
                    "attached": "pause",
                    "manual": "pause",
                    "next finished": "step",
                    "call returned": "step",
                    "hardcoded breakpoint": "breakpoint",
                }
                canonical = kinds.get(native_reason)
                if reason == "panic":
                    canonical = "exception"
                reason = native_reason or reason
            event: StopEvent | None = None
            key: tuple[object, ...] = ()
            if record:
                frame = frame_record(goroutine.get("currentLoc") or thread)
                key = (
                    self._generation,
                    execution,
                    reason,
                    goroutine_id,
                    frame.addr,
                    optional_integer(breakpoint.get("id")),
                )
                if key != self._last_stop_key:
                    event = StopEvent(
                        execution_state=execution,
                        reason=reason,
                        stop_kind=canonical or stop_kind_from_reason(reason),
                        thread_id=goroutine_id or None,
                        inferior_id=1,
                        frame=frame,
                        breakpoint_number=str(breakpoint["id"]) if "id" in breakpoint else None,
                        exit_code=exit_code,
                        timestamp=time.time(),
                        backend_data=self._bridge._data({"State": state}),
                    )
            self.runtime.apply_execution_observation(
                ExecutionObservation(
                    execution_state=execution,
                    target_loaded=True,
                    current_inferior_id=1,
                    inferior_count=1,
                    current_thread_id=goroutine_id or None,
                    current_frame=current_frame,
                    stop_reason=reason,
                    stop_kind=canonical,
                    exit_code=exit_code,
                    stop_event=event,
                )
            )
            self.last_state = state
            if event is not None:
                self._last_stop_key = key

    def _begin_execution(self, command: str, arguments: dict[str, object] | None = None) -> None:
        with self._command_lock:
            if not self.done.is_set() or self.runtime.execution_state == "running":
                raise DelveRpcError("The target is already running", "inferior_running")
            if self.runtime.config is not None and self.runtime.config.core:
                raise DelveRpcError("Cannot execute a core dump", "unsupported_combination")
            payload = {
                "name": command,
                "ReturnInfoLoadConfig": VARIABLE_LOAD_CONFIG,
                **(arguments or {}),
            }
            request = self._bridge._require_client().submit("Command", payload)
            self._generation += 1
            generation = self._generation
            self._command = command
            self._interrupt_requested = False
            self._pending = request
            self.done = threading.Event()
            done = self.done
            self._execution_error = None
            self._has_run = True
            self.runtime.begin_execution()

        def complete() -> None:
            try:
                response = request.wait()
                with self._command_lock:
                    if generation != self._generation:
                        return
                    self._execution_result = response
                    self._client_state(response)
                self._breakpoints.remove_hit_temporary_breakpoints(self.last_state)
            except Exception as exc:
                with self._command_lock:
                    if generation == self._generation:
                        self._execution_error = exc
                        self._pending = None
                        if not isinstance(exc, DelveRpcError):
                            return
                        if self._bridge._client is not None and self._bridge._client.alive:
                            try:
                                self._client_state(
                                    self._bridge._request("State", {"NonBlocking": True}),
                                    record=False,
                                )
                            except (DelveRpcError, DebuggerDecodeError):
                                pass
                        elif self.runtime.state != SessionState.STOPPED:
                            self.runtime.mark_transport_terminated(str(exc))
            finally:
                if generation == self._generation:
                    self.runtime.record_command_transcript(
                        CommandTranscriptEntry(
                            command=command,
                            sent_command=json.dumps(payload),
                            status="error" if self._execution_error else "success",
                            result_class="delve",
                            error=str(self._execution_error) if self._execution_error else None,
                            execution_state=self.runtime.execution_state,
                            stop_reason=self.runtime.stop_reason,
                            stop_kind=self.runtime.stop_kind,
                            timestamp=time.time(),
                        )
                    )
                done.set()

        threading.Thread(target=complete, name="delve-execution", daemon=True).start()

    def _execute(
        self,
        command: str,
        *,
        wait_for_stop: bool,
        timeout_sec: int,
        reverse: bool = False,
        arguments: dict[str, object] | None = None,
    ) -> OperationSuccess[CommandExecutionInfo]:
        if reverse:
            if not self._bridge._request("Recorded").get("Recorded"):
                raise DelveRpcError(
                    "Reverse execution requires a recorded Delve target", "unsupported_combination"
                )
            command = {
                "continue": "rewind",
                "step": "reverseStep",
                "next": "reverseNext",
                "stepOut": "reverseStepOut",
                "stepInstruction": "reverseStepInstruction",
                "nextInstruction": "reverseNextInstruction",
            }[command]
        self._begin_execution(command, arguments)
        if wait_for_stop:
            if not self.done.wait(timeout_sec):
                raise DelveRpcError(
                    f"Timed out waiting for Delve {command}; the target may still be running",
                    "command_timeout",
                )
            if self._execution_error is not None:
                raise self._execution_error
        return OperationSuccess(
            CommandExecutionInfo(
                command=command,
                backend_data=self._bridge._data(
                    self._execution_result if self.done.is_set() else {"execution_state": "running"}
                ),
            )
        )

    @_operation
    def run(
        self,
        args: list[str] | None = None,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[CommandExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        if self._bridge._attached:
            raise DelveRpcError(
                "An attached target uses continue, not run/restart", "unsupported_combination"
            )
        if self.runtime.config is not None and self.runtime.config.core:
            raise DelveRpcError("Cannot execute a core dump", "unsupported_combination")
        if self.runtime.execution_state == "running" or not self.done.is_set():
            raise DelveRpcError("The target is already running", "inferior_running")
        if self._has_run or args is not None:
            self._bridge._request(
                "Restart", {"ResetArgs": args is not None, "NewArgs": args}, timeout_sec=timeout_sec
            )
            self.runtime.mark_inferior_paused("entry")
        return self._execute("continue", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)

    @_operation
    def continue_execution(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._execute(
            "continue", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def step(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._execute(
            "step", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def next(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._execute(
            "next", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def step_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._execute(
            "stepInstruction", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def next_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._execute(
            "nextInstruction", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def finish(
        self,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        self._execute(
            "stepOut", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )
        returns = (
            mappings(mapping(self.last_state.get("currentThread")).get("ReturnValues"))
            if self.done.is_set()
            else []
        )
        return OperationSuccess(
            FinishInfo(
                message="Finished frame" if self.done.is_set() else "Finish started",
                return_value=(
                    ", ".join(variable_value(value) for value in returns) if returns else None
                ),
                frame=(
                    self.runtime.last_stop_event.frame
                    if self.done.is_set() and self.runtime.last_stop_event is not None
                    else None
                ),
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                last_stop_event=(self.runtime.last_stop_event if self.done.is_set() else None),
            )
        )

    @_operation
    def interrupt(self) -> OperationSuccess[MessageResult]:
        self._interrupt_requested = True
        response = self._bridge._request("Command", {"name": "halt"})
        if self.done.is_set():
            self._client_state(response)
        return OperationSuccess(MessageResult("Interrupt requested", self._bridge._data(response)))

    @_operation
    def wait_for_stop(
        self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC, stop_reasons: tuple[str, ...] = ()
    ) -> OperationSuccess[WaitForStopInfo]:
        self._bridge._require_client()
        source = "existing" if self.runtime.execution_state != "running" else "waited"
        deadline = time.monotonic() + timeout_sec
        completed = self.done.wait(timeout_sec)
        if completed and self._execution_error is not None:
            raise self._execution_error
        if completed:
            self._client_state(self._bridge._request("State", {"NonBlocking": True}))
        while (
            completed and self.runtime.execution_state == "running" and time.monotonic() < deadline
        ):
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            self._client_state(
                self._bridge._request(
                    "State",
                    {"NonBlocking": True},
                    timeout_sec=max(0.1, deadline - time.monotonic()),
                )
            )
        stopped = completed and self.runtime.execution_state in {"paused", "exited"}
        matched = stopped and (
            not stop_reasons
            or self.runtime.stop_reason in stop_reasons
            or self.runtime.stop_kind in stop_reasons
        )
        return OperationSuccess(
            WaitForStopInfo(
                message="Target stopped" if stopped else "Timed out waiting for stop",
                matched=matched,
                timed_out=not stopped,
                source=source,
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                reason_filter=list(stop_reasons),
                last_stop_event=self.runtime.last_stop_event,
            )
        )

    @_operation
    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo]:
        self._execute(
            "call",
            wait_for_stop=True,
            timeout_sec=timeout_sec,
            arguments={"expr": function_call, "goroutineID": self.runtime.current_thread_id or -1},
        )
        returns = mappings(mapping(self.last_state.get("currentThread")).get("ReturnValues"))
        return OperationSuccess(
            FunctionCallInfo(function_call, ", ".join(variable_value(value) for value in returns))
        )

    @_operation
    def jump(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo]:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        pc = integer(self._inspection.locations(location)[0].get("pc"))
        try:
            self._client_state(self._bridge._request("SetExecutionPoint", {"Addr": pc}))
        except DelveRpcError as exc:
            if exc.code == "unsupported_operation":
                self._bridge._unsupported.add(ACTION_JUMP)
            raise
        self._execute("continue", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)
        return OperationSuccess(
            LocationExecutionInfo(
                command=ACTION_JUMP,
                backend_data=self._bridge._data(self._execution_result),
                location=location,
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                last_stop_event=(self.runtime.last_stop_event if self.done.is_set() else None),
            )
        )
