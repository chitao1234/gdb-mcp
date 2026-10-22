"""LLDB backend driven exclusively through the native SB API bridge."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal, cast

from ..contracts import (
    DEBUGGER_LLDB,
)
from ..domain import (
    BackendData,
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CatchpointType,
    CommandExecutionInfo,
    DetachOnForkInfo,
    DisassemblyInfo,
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
    StructuredPayload,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from ..domain.errors import DebuggerDecodeError
from ..domain.results import payload_to_mapping, result_to_envelope
from .backends import BackendCapabilities
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .lldb_breakpoints import LldbBreakpoints
from .lldb_bridge import BridgeClient
from .lldb_bridge_build import prepare_bridge
from .lldb_bridge_types import BridgeError
from .lldb_events import LldbEvents
from .lldb_execution import LldbExecution
from .lldb_inspection import LldbInspection
from .lldb_values import integer, operation_error
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
        self._breakpoints = LldbBreakpoints(self)
        self._events = LldbEvents(self.runtime, self._breakpoints)
        self._inspection = LldbInspection(self.runtime, self)
        self._execution = LldbExecution(self.runtime, self, self._events, self._inspection)

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
                snapshot = self._events.apply(status)
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
            return OperationSuccess(self._events.apply(self._request("status")))
        except BridgeError as exc:
            if exc.code == "not_started":
                self._cleanup_failed_start(str(exc))
            return OperationSuccess(
                SessionStatusSnapshot(
                    debugger_alive=self.runtime.is_running,
                    target_loaded=self.runtime.target_loaded,
                    session_state=self.runtime.state,
                    execution_state=self.runtime.execution_state,
                    state_detail=self._events.state_detail,
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
        return self._execution.execute_command(command, timeout_sec)

    def run(
        self,
        args: list[str] | None = None,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.run(args, options)

    def add_inferior(
        self, *, executable: str | None = None, make_current: bool = False
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        return self._execution.add_inferior(executable=executable, make_current=make_current)

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        return self._execution.remove_inferior(inferior_id)

    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.attach_process(pid, timeout_sec)

    def set_follow_fork_mode(
        self, mode: FollowForkMode
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError:
        return self._execution.set_follow_fork_mode(mode)

    def set_detach_on_fork(
        self, enabled: bool
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError:
        return self._execution.set_detach_on_fork(enabled)

    def continue_execution(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.continue_execution(options=options, reverse=reverse)

    def wait_for_stop(
        self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC, stop_reasons: tuple[str, ...] = ()
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        return self._execution.wait_for_stop(timeout_sec=timeout_sec, stop_reasons=stop_reasons)

    def step(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.step(options=options, reverse=reverse)

    def next(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.next(options=options, reverse=reverse)

    def step_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.step_instruction(options=options, reverse=reverse)

    def next_instruction(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.next_instruction(options=options, reverse=reverse)

    def return_from_frame(
        self, *, value: str | None = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.return_from_frame(value=value)

    def jump(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        return self._execution.jump(location, options=options)

    def until(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        return self._execution.until(location, options=options)

    def finish(
        self,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        return self._execution.finish(options, reverse)

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        return self._execution.interrupt()

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        return self._execution.call_function(function_call, timeout_sec)

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
