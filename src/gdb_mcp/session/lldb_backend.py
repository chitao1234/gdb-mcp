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
    LocationKind,
)
from ..domain import (
    AddressRange,
    BackendData,
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    BreakpointRecord,
    CatchpointType,
    CommandExecutionInfo,
    CommandTranscriptEntry,
    DetachOnForkInfo,
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    ExecutionState,
    ExpressionValueInfo,
    FinishInfo,
    FollowForkMode,
    FollowForkModeInfo,
    FrameInfo,
    FrameRecord,
    FrameSelectionInfo,
    FunctionCallInfo,
    InferiorAddInfo,
    InferiorListInfo,
    InferiorRecord,
    InferiorRemoveInfo,
    InferiorSelectionInfo,
    InspectionContextInfo,
    InspectionLocation,
    LocationExecutionInfo,
    MemoryBlockRecord,
    MemoryReadInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
    SourceContextInfo,
    SourceLineRecord,
    SourceRange,
    StartupDiagnostics,
    StopEvent,
    StructuredPayload,
    ThreadListInfo,
    ThreadRecord,
    ThreadSelectionInfo,
    VariableRecord,
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
from .lldb_bridge import BridgeClient
from .lldb_bridge_build import prepare_bridge
from .lldb_bridge_types import BridgeError
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)

_LLDB_CATCHPOINTS = frozenset({"throw", "rethrow", "catch"})


def _operation_error(exc: BridgeError) -> OperationError:
    return OperationError(message=str(exc), code=exc.code)


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
        # Public breakpoint numbers are one namespace. Native LLDB breakpoint
        # and watchpoint ids are separate, so each public number records the
        # native kind and id.
        self._public_native: dict[int, tuple[str, int]] = {}
        self._native_public: dict[tuple[str, int], int] = {}
        self._origins: dict[int, str] = {}
        self._watch_specs: dict[int, tuple[str | None, WatchpointAccessType | None]] = {}
        self._catchpoints: set[int] = set()
        self._next_public = 1

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

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return value
        raise DebuggerDecodeError(f"LLDB value must be a mapping, got {type(value).__name__}")

    @staticmethod
    def _mappings(value: object) -> list[Mapping[str, object]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise DebuggerDecodeError(
                f"LLDB value must be a list of mappings, got {type(value).__name__}"
            )
        records: list[Mapping[str, object]] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise DebuggerDecodeError(
                    f"LLDB list item {index} must be a mapping, got {type(item).__name__}"
                )
            records.append(item)
        return records

    @staticmethod
    def _text(payload: Mapping[str, object], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        raise DebuggerDecodeError(f"LLDB field {key!r} must be text, got {type(value).__name__}")

    @staticmethod
    def _integer(payload: Mapping[str, object], key: str) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise DebuggerDecodeError(
            f"LLDB field {key!r} must be an integer, got {type(value).__name__}"
        )

    def _frame(self, payload: object) -> FrameRecord | None:
        mapping = self._mapping(payload)
        return FrameRecord.from_payload(mapping) if mapping else None

    def _thread(self, payload: object) -> ThreadRecord | None:
        mapping = dict(self._mapping(payload))
        for key in ("id", "target-id"):
            value = mapping.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                mapping[key] = str(value)
        return ThreadRecord.from_payload(mapping) if mapping else None

    def _apply_status(self, payload: Mapping[str, object]) -> SessionStatusSnapshot:
        execution = as_execution_state(self._text(payload, "execution_state")) or "unknown"
        detail = self._text(payload, "state_detail")
        self._state_detail = "crashed" if detail == "crashed" else None
        reason = self._text(payload, "stop_reason")
        stop_kind = as_stop_kind(payload.get("stop_kind"))
        exit_code = self._integer(payload, "exit_code")
        target_loaded = bool(payload.get("target_loaded"))
        current_inferior = self._integer(payload, "current_inferior_id")
        inferior_count = self._integer(payload, "inferior_count")
        self.runtime.target_loaded = target_loaded
        if inferior_count is not None:
            self.runtime.update_inferior_inventory(
                current_inferior_id=current_inferior,
                count=inferior_count,
                inferior_ids=tuple(range(1, inferior_count + 1)),
            )
        current_thread = self._integer(payload, "current_thread_id")
        current_frame = self._integer(payload, "current_frame")
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
        self.runtime.is_running = True
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
        reason = self._text(payload, "stop_reason")
        frame = self._frame(payload.get("frame"))
        breakpoint_id = self._integer(payload, "breakpoint_id")
        watchpoint_id = self._integer(payload, "watchpoint_id")
        key = (
            self._integer(payload, "stop_id"),
            reason,
            self._integer(payload, "current_thread_id"),
            frame.addr if frame is not None else None,
            self._integer(payload, "exit_code"),
            breakpoint_id,
            watchpoint_id,
            self._text(payload, "state_detail"),
        )
        if self._last_stop_key == key:
            return
        self._last_stop_key = key
        signal_number = self._integer(payload, "signal")
        signal_name = self._text(payload, "signal_name")
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
                self._native_public.get(("watchpoint", watchpoint_id), watchpoint_id)
            )
        elif breakpoint_id is not None:
            breakpoint_number = str(
                self._native_public.get(("breakpoint", breakpoint_id), breakpoint_id)
            )
        event = StopEvent(
            execution_state=execution,
            state_detail=self._state_detail,
            reason=reason,
            stop_kind=as_stop_kind(payload.get("stop_kind")),
            inferior_id=self._integer(payload, "current_inferior_id"),
            thread_id=self._integer(payload, "current_thread_id"),
            frame=frame,
            signal_name=signal_name,
            signal_meaning=signal_meaning,
            signal_code=self._integer(payload, "signal_code"),
            fault_address=self._text(payload, "fault_address"),
            breakpoint_number=breakpoint_number,
            exit_code=self._integer(payload, "exit_code"),
            timestamp=time.time(),
            backend_data=(
                BackendData(debugger="lldb", console_output="", data=details) if details else None
            ),
        )
        self.runtime.record_stop_event(event)

    # -- breakpoint inventory ----------------------------------------------

    def _native_number(self, payload: Mapping[str, object]) -> int:
        number = self._text(payload, "number")
        if number is None:
            raise DebuggerDecodeError("LLDB breakpoint response has no number")
        try:
            return int(number)
        except ValueError as exc:
            raise DebuggerDecodeError(
                f"LLDB breakpoint number must be an integer: {number!r}"
            ) from exc

    def _public_for_native(self, kind: str, native: int) -> int:
        key = (kind, native)
        existing = self._native_public.get(key)
        if existing is not None:
            return existing
        public = self._next_public
        self._next_public += 1
        self._native_public[key] = public
        self._public_native[public] = key
        return public

    def _apply_breakpoint(
        self, record: BreakpointRecord, kind: str, native: int
    ) -> BreakpointRecord:
        public = self._public_for_native("breakpoint" if kind == "catchpoint" else kind, native)
        update: dict[str, object] = {"number": str(public)}
        if kind == "watchpoint":
            update["type"] = "watchpoint"
        elif kind == "catchpoint" or public in self._catchpoints:
            update["type"] = "catchpoint"
        origin = self._origins.get(public)
        if origin is not None:
            update["original_location"] = origin
        if kind == "watchpoint":
            spec, access = self._watch_specs.get(public, (None, None))
            watch = record.watch
            watch_update: dict[str, object] = {}
            if watch is not None:
                if watch.access is None and access is not None:
                    watch_update["access"] = access
                if watch.spec is None and spec is not None:
                    watch_update["spec"] = spec
            if watch_update and watch is not None:
                update["watch"] = watch.model_copy(update=watch_update)
        return record.model_copy(update=update)

    def _refresh_breakpoints(self) -> None:
        seen: set[tuple[str, int]] = set()
        response = self._request("breakpoint_list")
        for payload in self._mappings(response.get("breakpoints")):
            mapping = dict(payload)
            native = self._native_number(mapping)
            record = BreakpointRecord.from_payload(mapping)
            self._apply_breakpoint(record, "breakpoint", native)
            seen.add(("breakpoint", native))
        response = self._request("watchpoint_list")
        for payload in self._mappings(response.get("watchpoints")):
            mapping = dict(payload)
            native = self._native_number(mapping)
            record = BreakpointRecord.from_payload(mapping)
            self._apply_breakpoint(record, "watchpoint", native)
            seen.add(("watchpoint", native))
        for public, native_key in list(self._public_native.items()):
            if native_key not in seen:
                self._public_native.pop(public, None)
                self._native_public.pop(native_key, None)
                self._origins.pop(public, None)
                self._watch_specs.pop(public, None)
                self._catchpoints.discard(public)

    def _all_breakpoints(self) -> list[BreakpointRecord]:
        self._refresh_breakpoints()
        records: list[BreakpointRecord] = []
        for public, (kind, native) in sorted(self._public_native.items()):
            if kind == "watchpoint":
                payload = self._request("watchpoint_get", id=native)
            else:
                payload = self._request("breakpoint_get", id=native)
            record = BreakpointRecord.from_payload(dict(payload))
            records.append(self._apply_breakpoint(record, kind, native))
        return sorted(records, key=lambda item: int(item.number or "0"))

    def _lookup_native(self, number: int) -> tuple[str, int] | None:
        return self._public_native.get(number)

    def _get_breakpoint_record(self, number: int) -> BreakpointRecord:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None:
            raise BridgeError(f"Breakpoint {number} not found", "not_found")
        kind, native_id = native
        operation = "watchpoint_get" if kind == "watchpoint" else "breakpoint_get"
        payload = self._request(operation, id=native_id)
        return self._apply_breakpoint(BreakpointRecord.from_payload(dict(payload)), kind, native_id)

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
                    if self._integer(target, "inferior_id") is None:
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
                self.runtime.target_loaded = snapshot.target_loaded
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
            except Exception as exc:
                self.runtime.mark_failed(str(exc))
                return OperationError(message=str(exc))
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
            return _operation_error(exc)

    def run(
        self,
        args: list[str] | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        wait_for_stop: bool = True,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
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
            return _operation_error(exc)

    def add_inferior(
        self, *, executable: str | None = None, make_current: bool = False
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        try:
            result = self._request(
                "target_create", executable=executable or "", make_current=make_current
            )
            inferior_id = self._integer(result, "inferior_id")
            if inferior_id is None:
                raise DebuggerDecodeError("LLDB target_create response has no inferior_id")
            self._refresh_inferiors()
            return OperationSuccess(
                InferiorAddInfo(
                    inferior_id=inferior_id,
                    display=self._text(result, "display"),
                    description=self._text(result, "description"),
                    executable=self._text(result, "executable"),
                    current_inferior_id=self.runtime.current_inferior_id,
                    inferior_count=self.runtime.inferior_count,
                    message="Inferior created",
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        try:
            result = self._request("target_delete", inferior_id=inferior_id)
            self._refresh_inferiors()
            return OperationSuccess(
                InferiorRemoveInfo(
                    inferior_id=inferior_id,
                    current_inferior_id=self._integer(result, "current_inferior_id"),
                    inferior_count=self.runtime.inferior_count,
                    message=f"Inferior {inferior_id} removed",
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

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
            return _operation_error(exc)

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
            return _operation_error(exc)

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
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
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
            return _operation_error(exc)

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
            return _operation_error(exc)

    def step(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._step("into", wait_for_stop, timeout_sec, reverse)

    def next(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
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
            return _operation_error(exc)

    def step_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._step("instruction_into", wait_for_stop, timeout_sec, reverse)

    def next_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
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
            return _operation_error(exc)

    def jump(
        self,
        location: str,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Relocate through LLDB, then resume without restricting the function."""

        return self._execute_location(
            ACTION_JUMP, location, wait_for_stop=wait_for_stop, timeout_sec=timeout_sec
        )

    def until(
        self,
        location: str,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        """Use LLDB's native until plan, including its return boundary and errors."""

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
            text = location.strip()
            fields: dict[str, object]
            if text.lstrip("+-").isdigit():
                kind, fields = "line", {"line": text}
            else:
                mode, fields = self._parse_breakpoint_location(text)
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
            command = self._text(result, "command") or action
            if not result.get("succeeded"):
                return OperationError(
                    (self._text(result, "console_output") or "")
                    + (self._text(result, "error") or "LLDB execution failed"),
                    details={
                        "relocated": bool(result.get("relocated")),
                        "execution_state": snapshot.execution_state,
                        "backend_data": _structured(result),
                    },
                )
            console_output = self._text(result, "console_output") or ""
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
            return _operation_error(exc)

    def finish(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
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
                    return_value=self._text(result, "return_value"),
                    frame=self._frame(result.get("frame")),
                    execution_state=snapshot.execution_state,
                    stop_reason=snapshot.stop_reason,
                    stop_kind=snapshot.stop_kind,
                    last_stop_event=self.runtime.last_stop_event,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

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
            return _operation_error(exc)

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
                    type=self._text(result, "type"),
                    summary=self._text(result, "summary"),
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    # -- inferior/context inspection ---------------------------------------

    def _refresh_inferiors(self) -> list[InferiorRecord]:
        response = self._request("target_list")
        targets = self._mappings(response.get("targets"))
        records: list[InferiorRecord] = []
        for payload in targets:
            record = InferiorRecord.from_payload(
                {key: value for key, value in payload.items() if key != "is_current"}
            )
            records.append(record)
        current = self._integer(response, "current_inferior_id")
        count = len(records)
        self.runtime.update_inferior_inventory(
            current_inferior_id=current,
            count=count,
            inferior_ids=tuple(
                int(record.inferior_id) for record in records if record.inferior_id is not None
            ),
        )
        for record in records:
            if record.inferior_id is None:
                continue
            state = record.execution_state
            if state == "paused":
                self.runtime.mark_inferior_paused(
                    record.stop_reason, inferior_id=record.inferior_id
                )
            elif state == "running":
                self.runtime.mark_inferior_running(inferior_id=record.inferior_id)
            elif state == "exited":
                self.runtime.mark_inferior_exited(
                    record.stop_reason, record.exit_code, inferior_id=record.inferior_id
                )
            elif state == "not_started":
                self.runtime.mark_inferior_not_started(inferior_id=record.inferior_id)
        return records

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        try:
            records = self._refresh_inferiors()
            return OperationSuccess(
                InferiorListInfo(
                    inferiors=records,
                    current_inferior_id=self.runtime.current_inferior_id,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        try:
            payload = self._request("target_select", inferior_id=inferior_id)
            self.runtime.mark_inferior_selected(inferior_id)
            return OperationSuccess(
                InferiorSelectionInfo(
                    inferior_id=inferior_id,
                    display=self._text(payload, "display"),
                    description=self._text(payload, "description"),
                    executable=self._text(payload, "executable"),
                    message=f"Inferior {inferior_id} selected",
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        try:
            payload = self._request("threads")
            records = [
                record
                for item in self._mappings(payload.get("threads"))
                if (record := self._thread(item)) is not None
            ]
            current = self._integer(payload, "current_thread_id")
            self.runtime.mark_thread_selected(current)
            return OperationSuccess(
                ThreadListInfo(
                    threads=records,
                    current_thread_id=str(current) if current is not None else None,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        try:
            payload = self._request(
                "backtrace",
                thread_id=thread_id if thread_id is not None else -1,
                max_frames=max_frames,
            )
            frames = [
                record
                for item in self._mappings(payload.get("frames"))
                if (record := self._frame(item)) is not None
            ]
            return OperationSuccess(
                BacktraceInfo(
                    thread_id=self._integer(payload, "thread_id"),
                    frames=frames,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def get_frame_info(
        self, *, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[FrameInfo] | OperationError:
        try:
            payload = self._request(
                "frame",
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame if frame is not None else -1,
            )
            return OperationSuccess(FrameInfo(frame=self._frame(payload.get("frame"))))
        except BridgeError as exc:
            return _operation_error(exc)

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        try:
            payload = self._request("select_thread", thread_id=thread_id)
            self.runtime.mark_thread_selected(thread_id)
            self.runtime.mark_frame_selected(0)
            return OperationSuccess(
                ThreadSelectionInfo(
                    thread_id=thread_id,
                    new_thread_id=str(thread_id),
                    frame=self._frame(payload.get("frame")),
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        try:
            payload = self._request("select_frame", frame=frame_number)
            self.runtime.mark_frame_selected(frame_number)
            return OperationSuccess(
                FrameSelectionInfo(
                    frame_number=frame_number,
                    frame=self._frame(payload.get("frame")),
                    message=f"Frame {frame_number} selected",
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        try:
            payload = self._request(
                "evaluate",
                expression=expression,
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame if frame is not None else -1,
            )
            return OperationSuccess(
                ExpressionValueInfo(
                    expression=expression,
                    value=payload.get("value"),
                    type=self._text(payload, "type"),
                    summary=self._text(payload, "summary"),
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        try:
            payload = self._request(
                "variables",
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame,
            )
            variables = [
                VariableRecord.from_payload(item)
                for item in self._mappings(payload.get("variables"))
            ]
            return OperationSuccess(
                VariablesInfo(
                    thread_id=self._integer(payload, "thread_id"),
                    frame=frame,
                    variables=variables,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

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
        try:
            payload = self._request(
                "registers",
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame if frame is not None else -1,
                max_registers=max_registers if max_registers is not None else -1,
                value_format=value_format,
            )
            records = [
                RegisterRecord.from_payload(item)
                for item in self._mappings(payload.get("registers"))
            ]
            if register_numbers is not None:
                wanted = set(register_numbers)
                records = [record for record in records if record.number in wanted]
            if register_names is not None:
                wanted_names = {name.lower() for name in register_names}
                records = [
                    record for record in records if (record.name or "").lower() in wanted_names
                ]
            if max_registers is not None:
                records = records[:max_registers]
            return OperationSuccess(RegistersInfo(registers=records))
        except BridgeError as exc:
            return _operation_error(exc)

    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        try:
            payload = self._request("memory", address=address, count=count, offset=offset)
            begin = self._text(payload, "address")
            data = self._text(payload, "data")
            read = self._integer(payload, "read")
            if begin is None or data is None or read is None:
                raise DebuggerDecodeError(
                    "LLDB memory response requires address, data, and read count"
                )
            try:
                end_address = hex(int(begin, 16) + read)
            except ValueError as exc:
                raise DebuggerDecodeError(
                    f"LLDB memory address is not hexadecimal: {begin!r}"
                ) from exc
            block = MemoryBlockRecord(
                begin=begin,
                offset="0x0",
                end=end_address,
                contents=data,
            )
            return OperationSuccess(
                MemoryReadInfo(
                    address=address,
                    requested_bytes=count,
                    offset=offset,
                    blocks=[block],
                    captured_bytes=read,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def disassemble(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        start_address: str | None = None,
        end_address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        instruction_count: int = 32,
        mode: Literal["assembly", "mixed"] = "mixed",
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        try:
            payload = self._request(
                "disassemble",
                function=function or "",
                address=address or "",
                start_address=start_address or "",
                end_address=end_address or "",
                file=file or "",
                line=line or 0,
                instruction_count=instruction_count,
                mode=mode,
            )
            instructions = [
                DisassemblyInstructionRecord.from_payload(item)
                for item in self._mappings(payload.get("instructions"))
            ]
            first = instructions[0] if instructions else None
            scope: LocationKind
            if function:
                scope = "function"
            elif file:
                scope = "file_line"
            elif start_address is not None and end_address is not None:
                scope = "address_range"
            elif address:
                scope = "address"
            else:
                scope = "current"
            return OperationSuccess(
                DisassemblyInfo(
                    context=InspectionContextInfo(thread_id=thread_id, frame=frame),
                    location=InspectionLocation(
                        kind=scope,
                        function=self._text(payload, "function") or function,
                        address=address,
                        file=first.file if first is not None else file,
                        fullname=first.fullname if first is not None else file,
                        line=first.line if first is not None else line,
                    ),
                    range=AddressRange(
                        start_address=self._text(payload, "start_address"),
                        end_address=self._text(payload, "end_address"),
                    ),
                    mode=mode,
                    instructions=instructions,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    def get_source_context(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        context_before: int = 5,
        context_after: int = 5,
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        try:
            payload = self._request(
                "source",
                function=function or "",
                address=address or "",
                file=file or "",
                line=line or 0,
                start_line=start_line or 0,
                end_line=end_line or 0,
                context_before=context_before,
                context_after=context_after,
            )
            lines = [
                SourceLineRecord.from_payload(item) for item in self._mappings(payload.get("lines"))
            ]
            scope: LocationKind
            if function:
                scope = "function"
            elif address:
                scope = "address"
            elif file is not None and start_line is not None:
                scope = "file_range"
            elif file:
                scope = "file_line"
            else:
                scope = "current"
            return OperationSuccess(
                SourceContextInfo(
                    context=InspectionContextInfo(thread_id=thread_id, frame=frame),
                    location=InspectionLocation(
                        kind=scope,
                        function=self._text(payload, "function") or function,
                        address=address,
                        file=str(payload.get("file") or file or ""),
                        fullname=self._text(payload, "fullname"),
                        line=self._integer(payload, "line") or line,
                    ),
                    range=SourceRange(
                        start_line=self._integer(payload, "start_line") or start_line or 1,
                        end_line=self._integer(payload, "end_line") or end_line or len(lines),
                    ),
                    lines=lines,
                )
            )
        except BridgeError as exc:
            return _operation_error(exc)

    # -- breakpoints --------------------------------------------------------

    @staticmethod
    def _parse_breakpoint_location(location: str) -> tuple[str, dict[str, object]]:
        text = location.strip()
        if text.startswith("*"):
            return "address", {"address": text[1:]}
        if ":" in text:
            file, _, tail = text.rpartition(":")
            if file and tail.isdigit():
                return "location", {"file": file, "line": int(tail)}
        return "function", {"name": text}

    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        try:
            mode, fields = self._parse_breakpoint_location(location)
            payload = self._request(
                "breakpoint_create",
                mode=mode,
                condition=condition or "",
                temporary=temporary,
                original=location,
                **fields,
            )
            native = self._native_number(payload)
            public = self._public_for_native("breakpoint", native)
            self._origins[public] = location
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), "breakpoint", native
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return _operation_error(exc)

    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        try:
            read = access in {"read", "access"}
            write = access in {"write", "access"}
            payload = self._request(
                "watchpoint_create",
                expression=expression,
                read=read,
                write=write,
                access=access,
            )
            native = self._native_number(payload)
            public = self._public_for_native("watchpoint", native)
            self._origins[public] = expression
            self._watch_specs[public] = (expression, access)
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), "watchpoint", native
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return _operation_error(exc)

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        native = self._lookup_native(number)
        if native is None or native[0] != "watchpoint":
            return OperationError(message=f"Watchpoint {number} not found", code="not_found")
        return self._delete_breakpoint(number, expected="watchpoint")

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        if kind not in _LLDB_CATCHPOINTS:
            return OperationError(
                message=(
                    f"LLDB supports exception catchpoints (throw, rethrow, catch), not {kind!r}. "
                    "Fork, exec and syscall events are reported as stops when they occur."
                ),
                code="unsupported_combination",
            )
        try:
            payload = self._request(
                "breakpoint_create",
                mode="exception",
                catch_exception=kind == "catch",
                throw_exception=kind in {"throw", "rethrow"},
                temporary=temporary,
                original=kind,
            )
            native = self._native_number(payload)
            public = self._public_for_native("breakpoint", native)
            self._catchpoints.add(public)
            self._origins[public] = kind
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), "catchpoint", native
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return _operation_error(exc)

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        try:
            records = self._all_breakpoints()
            return OperationSuccess(BreakpointListInfo(breakpoints=records))
        except BridgeError as exc:
            return _operation_error(exc)

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        try:
            return OperationSuccess(BreakpointInfo(breakpoint=self._get_breakpoint_record(number)))
        except BridgeError as exc:
            return _operation_error(exc)

    def _delete_breakpoint(
        self, number: int, *, expected: str | None = None
    ) -> OperationSuccess[SessionMessage] | OperationError:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None or (expected is not None and native[0] != expected):
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        kind, native_id = native
        operation = "watchpoint_delete" if kind == "watchpoint" else "breakpoint_delete"
        try:
            self._request(operation, id=native_id)
        except BridgeError as exc:
            return _operation_error(exc)
        self._public_native.pop(number, None)
        self._native_public.pop(native, None)
        self._origins.pop(number, None)
        self._watch_specs.pop(number, None)
        self._catchpoints.discard(number)
        label = "Watchpoint" if kind == "watchpoint" else "Breakpoint"
        return OperationSuccess(SessionMessage(message=f"{label} {number} deleted"))

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._delete_breakpoint(number)

    def _set_enabled(
        self, number: int, enabled: bool
    ) -> OperationSuccess[SessionMessage] | OperationError:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        kind, native_id = native
        operation = "watchpoint_enable" if kind == "watchpoint" else "breakpoint_enable"
        try:
            self._request(operation, id=native_id, enabled=enabled)
        except BridgeError as exc:
            return _operation_error(exc)
        label = "Watchpoint" if kind == "watchpoint" else "Breakpoint"
        word = "enabled" if enabled else "disabled"
        return OperationSuccess(SessionMessage(message=f"{label} {number} {word}"))

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._set_enabled(number, True)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        return self._set_enabled(number, False)

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        native = self._lookup_native(number)
        if native is None:
            self._refresh_breakpoints()
            native = self._lookup_native(number)
        if native is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        kind, native_id = native
        operation = "watchpoint_condition" if kind == "watchpoint" else "breakpoint_condition"
        try:
            payload = self._request(
                operation,
                id=native_id,
                condition="" if clear_condition else (condition or ""),
            )
            record = self._apply_breakpoint(
                BreakpointRecord.from_payload(dict(payload)), kind, native_id
            )
            return OperationSuccess(BreakpointInfo(breakpoint=record))
        except BridgeError as exc:
            return _operation_error(exc)
