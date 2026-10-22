"""LLDB backend driven exclusively through the native SB API bridge."""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Mapping
from typing import Literal, cast

from ..contracts import (
    ACTION_JUMP,
    ACTION_UNTIL,
    DEBUGGER_LLDB,
    ActionJumpName,
    ActionUntilName,
)
from ..domain import (
    BackendData,
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CatchpointType,
    CommandExecutionInfo,
    CommandTranscriptEntry,
    DetachOnForkInfo,
    DisassemblyInfo,
    ExecutionState,
    ExpressionValueInfo,
    FinishInfo,
    FollowForkMode,
    FollowForkModeInfo,
    FrameInfo,
    FrameSelectionInfo,
    FunctionCallInfo,
    InferiorAddInfo,
    InferiorListInfo,
    InferiorRemoveInfo,
    InferiorSelectionInfo,
    LocationExecutionInfo,
    MemoryReadInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
    SourceContextInfo,
    StartupDiagnostics,
    StopEvent,
    StructuredPayload,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from ..domain.errors import DebuggerDecodeError
from ..domain.parsing import as_execution_state, as_stop_kind
from ..domain.results import payload_to_mapping, result_to_envelope
from .backends import BackendCapabilities
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .lldb_breakpoints import LldbBreakpoints, parse_breakpoint_location
from .lldb_bridge import BridgeClient
from .lldb_bridge_build import prepare_bridge
from .lldb_bridge_types import BridgeError
from .lldb_inspection import LldbInspection
from .lldb_values import frame as frame_record
from .lldb_values import integer, operation_error, text
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .requests import (
    DEFAULT_EXECUTION_OPTIONS,
    DisassemblyRequest,
    ExecutionOptions,
    SourceContextRequest,
)
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)


def _structured(value: Mapping[str, object]) -> StructuredPayload:
    return payload_to_mapping(value)


class LldbSbBackend:
    """One LLDB debugger driven through the JSON bridge."""

    def __init__(self, *, os_module: OsModuleProtocol, time_module: TimeModuleProtocol) -> None:
        self.runtime = SessionRuntime(os_module=os_module, time_module=time_module)
        self._client: BridgeClient | None = None
        self._capabilities = BackendCapabilities.full_surface(DEBUGGER_LLDB)
        self._state_detail: Literal["crashed"] | None = None
        self._last_stop_key: tuple[object, ...] | None = None
        self._breakpoints = LldbBreakpoints(self)
        self._inspection = LldbInspection(self.runtime, self)

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    @property
    def has_controller(self) -> bool:
        """Return whether this backend owns a bridge handle."""

        return self._client is not None

    # -- bridge helpers -----------------------------------------------------

    def _require_client(self) -> BridgeClient:
        if self._client is None:
            raise BridgeError("No active LLDB session", "not_started")
        return self._client

    def _request(self, operation: str, **payload: object) -> Mapping[str, object]:
        timeout_ms = payload.get("timeout_ms")
        request_timeout = (
            float(timeout_ms) / 1000.0 + 5.0
            if isinstance(timeout_ms, (int, float)) and timeout_ms > 0
            else float(DEFAULT_TIMEOUT_SEC + 5)
        )
        response = self._require_client().request(operation, timeout_sec=request_timeout, **payload)
        return cast(Mapping[str, object], response)

    # -- mapping ------------------------------------------------------------

    def _apply_status(self, payload: Mapping[str, object]) -> SessionStatusSnapshot:
        execution = as_execution_state(text(payload, "execution_state")) or "unknown"
        detail = text(payload, "state_detail")
        self._state_detail = "crashed" if detail == "crashed" else None
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
            state_detail=self._state_detail,
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
            state_detail=self._state_detail,
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

    # -- breakpoint inventory ----------------------------------------------

    # -- lifecycle ----------------------------------------------------------

    def start(
        self,
        config: SessionConfig,
    ) -> OperationSuccess[SessionStartupInfo] | OperationError:
        with self.runtime.lifecycle_lock:
            if self._client is not None:
                return OperationError(message="Session already running. Stop it first.")
            program = config.program
            debugger = config.debugger
            args = config.args
            init_commands = config.init_commands
            env = config.env
            debugger_path = config.debugger_path
            debugger_args = config.debugger_args
            working_dir = config.working_dir
            core = config.core
            endpoint = config.endpoint
            replay = config.rr_replay
            if debugger != DEBUGGER_LLDB:
                return OperationError(
                    message=f"LLDB backend cannot start debugger {debugger!r}",
                    code="unsupported_combination",
                )
            if debugger_args:
                return OperationError(
                    message="debugger_args is not supported by the LLDB SB API backend",
                    code="unsupported_combination",
                )
            if replay:
                return OperationError(
                    message=(
                        "LLDB cannot start rr replay itself, and connecting LLDB to rr "
                        "disables rr's reverse stepping. Use debugger='gdb' when you need "
                        "rr reverse execution. If forward replay is enough, start "
                        "`rr replay -s <port> <trace>` yourself, then call "
                        "gdb_session_connect with debugger='lldb', "
                        "endpoint='connect://127.0.0.1:<port>', and program set to the "
                        "trace's executable."
                    ),
                    code="unsupported_combination",
                )
            if endpoint and (args or core):
                return OperationError(
                    message="A remote LLDB endpoint owns the target; do not also pass args or core.",
                    code="unsupported_combination",
                )
            if args and core:
                return OperationError(
                    message="Cannot combine program arguments with core-dump analysis.",
                    code="unsupported_combination",
                )
            if working_dir and not self.runtime.os_module.path.isdir(working_dir):
                return OperationError(message=f"Working directory does not exist: {working_dir}")
            self.runtime.begin_startup(config)
            try:
                launch = prepare_bridge(debugger_path)
                client = BridgeClient(launch)
                self._client = client
                initialized = client.request("initialize")
                env_entries = [f"{key}={value}" for key, value in env.items()]
                client.request(
                    "configure_launch",
                    program=program,
                    args=list(args),
                    env=env_entries,
                    working_dir=working_dir,
                )
                if program or core or endpoint:
                    target = client.request("create_target", program=program)
                    if integer(target, "inferior_id") is None:
                        raise BridgeError("LLDB did not create a target", "not_found")
                if core:
                    client.request("load_core", path=core)
                if endpoint:
                    client.request("connect_remote", endpoint=endpoint)
                initialization_results: list[StructuredPayload] = []
                for command in init_commands:
                    result = self.execute_command(command)
                    initialization_results.append(result_to_envelope(result))
                    if isinstance(result, OperationError):
                        message = result.message
                        self._cleanup_failed_start(f"Init command {command!r} failed: {message}")
                        return OperationError(
                            message=f"Init command {command!r} failed: {message}",
                            details={
                                "diagnostics": payload_to_mapping(
                                    StartupDiagnostics(
                                        environment_results=[],
                                        initialization_results=initialization_results,
                                        backend_data=BackendData(
                                            debugger=DEBUGGER_LLDB,
                                            console_output="",
                                            data=payload_to_mapping(initialized),
                                        ),
                                    )
                                )
                            },
                        )
                status = client.request("status")
                snapshot = self._apply_status(status)
                self.runtime.mark_ready()
                self.runtime.mark_detach_on_fork(None)
                if snapshot.inferior_count is not None:
                    self.runtime.update_inferior_inventory(
                        current_inferior_id=snapshot.current_inferior_id,
                        count=snapshot.inferior_count,
                        inferior_ids=tuple(range(1, snapshot.inferior_count + 1)),
                    )
                self.runtime.mark_target_loaded(snapshot.target_loaded)
                return OperationSuccess(
                    SessionStartupInfo(
                        message="LLDB session started",
                        program=program,
                        core=core,
                        target_loaded=snapshot.target_loaded,
                        execution_state=snapshot.execution_state,
                        stop_reason=snapshot.stop_reason,
                        stop_kind=snapshot.stop_kind,
                        exit_code=snapshot.exit_code,
                        diagnostics=StartupDiagnostics(
                            environment_results=[],
                            initialization_results=initialization_results,
                            backend_data=BackendData(
                                debugger=DEBUGGER_LLDB,
                                console_output="",
                                data=payload_to_mapping(initialized),
                            ),
                        ),
                        endpoint=endpoint,
                    )
                )
            except BridgeError as exc:
                logger.exception("Could not start LLDB session")
                message = str(exc)
                if exc.code == "not_started":
                    message += (
                        " (the bridge may have been built against an incompatible C library; "
                        "set LLDB_BRIDGE_CXX to a compiler matching the selected liblldb, "
                        "or point LLDB_BRIDGE_EXECUTABLE at a prebuilt bridge)"
                    )
                self._cleanup_failed_start(message)
                return OperationError(message=message, code=exc.code)
            except OSError as exc:
                logger.error("Could not start LLDB session: %s", exc)
                message = f"Failed to start LLDB: {exc}"
                self._cleanup_failed_start(message)
                return OperationError(message=message)
            except DebuggerDecodeError:
                self._cleanup_failed_start("LLDB returned malformed debugger data")
                raise
            except Exception as exc:
                logger.exception("Could not start LLDB session")
                self._cleanup_failed_start(f"Failed to start LLDB: {exc}")
                raise

    def _cleanup_failed_start(self, message: str) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("Ignoring LLDB bridge cleanup failure: %s", exc)
        self._client = None
        self.runtime.mark_failed(message)

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        with self.runtime.lifecycle_lock:
            if self._client is None:
                return OperationError(message="No active session")
            config = self.runtime.config
            detach = bool(
                (config is not None and config.endpoint is not None)
                or self.runtime.attached_pid is not None
            )
            try:
                self._client.close(detach=detach)
            except (BridgeError, OSError) as exc:
                self.runtime.mark_failed(str(exc))
                if isinstance(exc, BridgeError):
                    return operation_error(exc)
                return OperationError(message=str(exc))
            except Exception as exc:
                self.runtime.mark_failed(str(exc))
                raise
            finally:
                self._client = None
            self.runtime.mark_stopped()
            return OperationSuccess(SessionMessage(message="LLDB session stopped"))

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        if self._client is None:
            return OperationSuccess(
                SessionStatusSnapshot(
                    debugger_alive=False,
                    target_loaded=False,
                    session_state=self.runtime.state,
                    execution_state="unknown",
                )
            )
        try:
            return OperationSuccess(self._apply_status(self._request("status")))
        except BridgeError as exc:
            if exc.code == "not_started":
                self._cleanup_failed_start(str(exc))
            return OperationSuccess(
                SessionStatusSnapshot(
                    debugger_alive=self.runtime.is_running,
                    target_loaded=self.runtime.target_loaded,
                    session_state=self.runtime.state,
                    execution_state=self.runtime.execution_state,
                    state_detail=self._state_detail,
                    stop_reason=self.runtime.stop_reason,
                    stop_kind=self.runtime.stop_kind,
                    exit_code=self.runtime.exit_code,
                    current_inferior_id=self.runtime.current_inferior_id,
                    inferior_count=self.runtime.inferior_count,
                    inferior_states=self.runtime.inferiors_state_summary() or None,
                )
            )

    # -- execution ----------------------------------------------------------

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        try:
            result = self._request("command", command=command)
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
            self._request("launch", **launch_payload)
            result = (
                self._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._request("status")
            )
            self._apply_status(result)
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
            result = self._request(
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
            result = self._request("target_delete", inferior_id=inferior_id)
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
                self._request("create_target", program="")
            result = self._request("attach", pid=pid)
            self._apply_status(result)
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
            self._request("setting_set", name="target.process.follow-fork-mode", value=mode)
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
            self._request("continue")
            result = (
                self._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._request("status")
            )
            self._apply_status(result)
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
            result = self._request("wait", timeout_ms=timeout_sec * 1000)
            snapshot = self._apply_status(result)
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
            self._request("step", kind=kind)
            result = (
                self._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._request("status")
            )
            self._apply_status(result)
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
            result = self._request("return_from_frame", value=value or "")
            self._apply_status(result)
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
            result = self._request(
                "execute_location",
                relocate=action == ACTION_JUMP,
                kind=kind,
                **fields,
            )
            snapshot = self._apply_status(result)
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
                waited = self._request("wait", timeout_ms=timeout_sec * 1000)
                snapshot = self._apply_status(waited)
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
            self._request("step", kind="out")
            result = (
                self._request("wait", timeout_ms=timeout_sec * 1000)
                if wait_for_stop
                else self._request("status")
            )
            snapshot = self._apply_status(result)
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
            result = self._request("interrupt")
            self._apply_status(result)
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
            result = self._request(
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

    # -- inferior/context inspection ---------------------------------------

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        return self._inspection.list_inferiors()

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        return self._inspection.select_inferior(inferior_id)

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        return self._inspection.get_threads()

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        return self._inspection.get_backtrace(thread_id, max_frames)

    def get_frame_info(
        self, *, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[FrameInfo] | OperationError:
        return self._inspection.get_frame_info(thread_id=thread_id, frame=frame)

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        return self._inspection.select_thread(thread_id)

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        return self._inspection.select_frame(frame_number)

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        return self._inspection.evaluate_expression(expression, thread_id, frame)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        return self._inspection.get_variables(thread_id, frame)

    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        return self._inspection.get_registers(
            thread_id,
            frame,
            register_numbers,
            register_names,
            include_vector_registers,
            max_registers,
            value_format,
        )

    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        return self._inspection.read_memory(address, count, offset=offset)

    def disassemble(
        self, request: DisassemblyRequest
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        return self._inspection.disassemble(request)

    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        return self._inspection.get_source_context(request)

    # -- breakpoints --------------------------------------------------------

    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        return self._breakpoints.set_breakpoint(location, condition, temporary)

    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        return self._breakpoints.set_watchpoint(expression, access=access)

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._breakpoints.delete_watchpoint(number)

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        return self._breakpoints.set_catchpoint(kind, argument=argument, temporary=temporary)

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        return self._breakpoints.list_breakpoints()

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        return self._breakpoints.get_breakpoint(number)

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._breakpoints.delete_breakpoint(number)

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._breakpoints.enable_breakpoint(number)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._breakpoints.disable_breakpoint(number)

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        return self._breakpoints.update_breakpoint(
            number, condition=condition, clear_condition=clear_condition
        )
