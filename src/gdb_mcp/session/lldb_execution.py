"""LLDB backend driven exclusively through the native SB API bridge."""

from __future__ import annotations

import time
from collections.abc import Mapping

from ..contracts import (
    ACTION_JUMP,
    ACTION_UNTIL,
    DEBUGGER_LLDB,
    ActionJumpName,
    ActionUntilName,
)
from ..domain import (
    BackendData,
    CommandExecutionInfo,
    CommandTranscriptEntry,
    DetachOnForkInfo,
    FinishInfo,
    FollowForkMode,
    FollowForkModeInfo,
    FunctionCallInfo,
    InferiorAddInfo,
    InferiorRemoveInfo,
    LocationExecutionInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    StructuredPayload,
    WaitForStopInfo,
)
from ..domain.errors import DebuggerDecodeError
from ..domain.results import payload_to_mapping
from .constants import DEFAULT_TIMEOUT_SEC
from .lldb_breakpoints import LldbRequest, parse_breakpoint_location
from .lldb_bridge_types import BridgeError
from .lldb_events import LldbEvents
from .lldb_inspection import LldbInspection
from .lldb_values import frame as frame_record
from .lldb_values import integer, operation_error, text
from .requests import (
    DEFAULT_EXECUTION_OPTIONS,
    ExecutionOptions,
)
from .runtime import SessionRuntime


class LldbExecution:
    """LLDB execution operations over the native bridge."""

    def __init__(
        self,
        runtime: SessionRuntime,
        bridge: LldbRequest,
        events: LldbEvents,
        inspection: LldbInspection,
    ) -> None:
        self.runtime = runtime
        self._bridge = bridge
        self._events = events
        self._inspection = inspection

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        try:
            result = self._bridge._request("command", command=command)
            output = str(result.get("output") or "") + str(result.get("error") or "")
            succeeded = bool(result.get("succeeded"))
            self.runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command=command,
                    sent_command=command,
                    status="success" if succeeded else "error",
                    result_class="lldb",
                    error=None if succeeded else output.strip() or None,
                    execution_state=self.runtime.execution_state,
                    stop_reason=self.runtime.stop_reason,
                    stop_kind=self.runtime.stop_kind,
                    timestamp=time.time(),
                )
            )
            if not succeeded:
                return OperationError(
                    message=output.strip() or "LLDB command failed",
                )
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output=output,
                        data=_structured(
                            {
                                name: value
                                for name, value in result.items()
                                if name not in {"output", "error", "succeeded"}
                            }
                        ),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def run(
        self,
        args: list[str] | None = None,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        try:
            launch_payload: dict[str, object] = {}
            if args is not None:
                launch_payload["args"] = args
            self._bridge._request("launch", **launch_payload)
            result = (
                self._bridge._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._bridge._request("status")
            )
            self._events.apply(result)
            return OperationSuccess(
                CommandExecutionInfo(
                    command="run",
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output="",
                        data=_structured(result),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def add_inferior(
        self, *, executable: str | None = None, make_current: bool = False
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        try:
            result = self._bridge._request(
                "target_create", executable=executable or "", make_current=make_current
            )
            inferior_id = integer(result, "inferior_id")
            if inferior_id is None:
                raise DebuggerDecodeError("LLDB target_create response has no inferior_id")
            self._inspection.refresh_inferiors()
            return OperationSuccess(
                InferiorAddInfo(
                    inferior_id=inferior_id,
                    display=text(result, "display"),
                    description=text(result, "description"),
                    executable=text(result, "executable"),
                    current_inferior_id=self.runtime.current_inferior_id,
                    inferior_count=self.runtime.inferior_count,
                    message="Inferior created",
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        try:
            result = self._bridge._request("target_delete", inferior_id=inferior_id)
            self._inspection.refresh_inferiors()
            return OperationSuccess(
                InferiorRemoveInfo(
                    inferior_id=inferior_id,
                    current_inferior_id=integer(result, "current_inferior_id"),
                    inferior_count=self.runtime.inferior_count,
                    message=f"Inferior {inferior_id} removed",
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        try:
            if not self.runtime.target_loaded:
                self._bridge._request("create_target", program="")
            result = self._bridge._request("attach", pid=pid)
            self._events.apply(result)
            self.runtime.mark_attached(pid)
            return OperationSuccess(
                CommandExecutionInfo(
                    command="attach",
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output="",
                        data=_structured(result),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def set_follow_fork_mode(
        self, mode: FollowForkMode
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError:
        try:
            self._bridge._request("setting_set", name="target.process.follow-fork-mode", value=mode)
            self.runtime.mark_follow_fork_mode(mode)
            return OperationSuccess(
                FollowForkModeInfo(mode=mode, message=f"Follow-fork mode set to {mode}")
            )
        except BridgeError as exc:
            return operation_error(exc)

    def set_detach_on_fork(
        self, enabled: bool
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError:
        return OperationError(
            message=(
                "LLDB has no detach-on-fork setting. Use target.process.follow-fork-mode "
                "through set_follow_fork_mode; the non-followed process is left detached."
            ),
            code="unsupported_operation",
        )

    def continue_execution(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        if reverse:
            return OperationError(
                message="LLDB reverse execution is not supported", code="unsupported_combination"
            )
        try:
            self._bridge._request("continue")
            result = (
                self._bridge._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._bridge._request("status")
            )
            self._events.apply(result)
            return OperationSuccess(
                CommandExecutionInfo(
                    command="continue",
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output="",
                        data=_structured(result),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def wait_for_stop(
        self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC, stop_reasons: tuple[str, ...] = ()
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        try:
            result = self._bridge._request("wait", timeout_ms=timeout_sec * 1000)
            snapshot = self._events.apply(result)
            reason = snapshot.stop_reason
            matched = not stop_reasons or (reason in stop_reasons if reason is not None else False)
            return OperationSuccess(
                WaitForStopInfo(
                    message="Stop observed" if matched else "No matching stop observed",
                    matched=matched,
                    timed_out=bool(result.get("timed_out")),
                    source="waited",
                    execution_state=snapshot.execution_state,
                    stop_reason=reason,
                    stop_kind=snapshot.stop_kind,
                    reason_filter=list(stop_reasons) or None,
                    last_stop_event=self.runtime.last_stop_event,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def step(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._step("into", wait_for_stop, timeout_sec, reverse)

    def next(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._step("over", wait_for_stop, timeout_sec, reverse)

    def _step(
        self, kind: str, wait_for_stop: bool, timeout_sec: int, reverse: bool
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        if reverse:
            return OperationError(
                message="LLDB reverse execution is not supported", code="unsupported_combination"
            )
        try:
            self._bridge._request("step", kind=kind)
            result = (
                self._bridge._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._bridge._request("status")
            )
            self._events.apply(result)
            return OperationSuccess(
                CommandExecutionInfo(
                    command=kind,
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output="",
                        data=_structured(result),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def step_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._step("instruction_into", wait_for_stop, timeout_sec, reverse)

    def next_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        return self._step("instruction_over", wait_for_stop, timeout_sec, reverse)

    def return_from_frame(
        self, *, value: str | None = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        try:
            result = self._bridge._request("return_from_frame", value=value or "")
            self._events.apply(result)
            return OperationSuccess(
                CommandExecutionInfo(
                    command="return",
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output="",
                        data=_structured(result),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def jump(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Relocate through LLDB, then resume without restricting the function."""
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop

        return self._execute_location(
            ACTION_JUMP, location, wait_for_stop=wait_for_stop, timeout_sec=timeout_sec
        )

    def until(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Use LLDB's native until plan, including its return boundary and errors."""
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop

        return self._execute_location(
            ACTION_UNTIL, location, wait_for_stop=wait_for_stop, timeout_sec=timeout_sec
        )

    def _execute_location(
        self,
        action: ActionJumpName | ActionUntilName,
        location: str,
        *,
        wait_for_stop: bool,
        timeout_sec: int,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        try:
            destination = location.strip()
            fields: dict[str, object]
            if destination.lstrip("+-").isdigit():
                kind, fields = "line", {"line": destination}
            else:
                mode, fields = parse_breakpoint_location(destination)
                kind = "file_line" if mode == "location" else "address"
                if mode == "function":
                    fields = {"address": fields["name"]}
            previous_stop = self.runtime.last_stop_event
            result = self._bridge._request(
                "execute_location",
                relocate=action == ACTION_JUMP,
                kind=kind,
                **fields,
            )
            snapshot = self._events.apply(result)
            command = text(result, "command") or action
            if not result.get("succeeded"):
                return OperationError(
                    (text(result, "console_output") or "")
                    + (text(result, "error") or "LLDB execution failed"),
                    details={
                        "relocated": bool(result.get("relocated")),
                        "execution_state": snapshot.execution_state,
                        "backend_data": _structured(result),
                    },
                )
            console_output = text(result, "console_output") or ""
            if wait_for_stop and snapshot.execution_state == "running":
                waited = self._bridge._request("wait", timeout_ms=timeout_sec * 1000)
                snapshot = self._events.apply(waited)
                result = {**result, **waited}
                if waited.get("timed_out") and snapshot.execution_state == "running":
                    return OperationError(
                        f"Timeout waiting for execution to stop after {timeout_sec}s",
                        code="command_timeout",
                        details={
                            "relocated": bool(result.get("relocated")),
                            "execution_state": snapshot.execution_state,
                            "backend_data": _structured(result),
                        },
                    )
            event = self.runtime.last_stop_event
            if event is previous_stop:
                event = None
            return OperationSuccess(
                LocationExecutionInfo(
                    command=command,
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output=console_output,
                        data=_structured(result),
                    ),
                    location=location,
                    execution_state=snapshot.execution_state,
                    stop_reason=event.reason if event is not None else None,
                    stop_kind=event.stop_kind if event is not None else None,
                    last_stop_event=event,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def finish(
        self,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        timeout_sec, wait_for_stop = options.timeout_sec, options.wait_for_stop
        if reverse:
            return OperationError(
                message="LLDB reverse execution is not supported", code="unsupported_combination"
            )
        try:
            self._bridge._request("step", kind="out")
            result = (
                self._bridge._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._bridge._request("status")
            )
            snapshot = self._events.apply(result)
            return OperationSuccess(
                FinishInfo(
                    message="LLDB finish",
                    return_value=text(result, "return_value"),
                    frame=frame_record(result.get("frame")),
                    execution_state=snapshot.execution_state,
                    stop_reason=snapshot.stop_reason,
                    stop_kind=snapshot.stop_kind,
                    last_stop_event=self.runtime.last_stop_event,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        try:
            result = self._bridge._request("interrupt")
            self._events.apply(result)
            return OperationSuccess(
                MessageResult(
                    message="LLDB interrupted",
                    backend_data=BackendData(
                        debugger=DEBUGGER_LLDB,
                        console_output="",
                        data=_structured(result),
                    ),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        try:
            result = self._bridge._request(
                "evaluate", expression=function_call, timeout_ms=timeout_sec * 1000
            )
            return OperationSuccess(
                FunctionCallInfo(
                    function_call=function_call,
                    result=str(result.get("value") or ""),
                    type=text(result, "type"),
                    summary=text(result, "summary"),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)


def _structured(value: Mapping[str, object]) -> StructuredPayload:
    return payload_to_mapping(value)
