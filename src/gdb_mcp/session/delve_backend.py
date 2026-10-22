"""Go debugging through Delve's streaming JSON-RPC API v2."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import BinaryIO, Literal
from urllib.parse import urlsplit

from ..contracts import ACTION_JUMP, DEBUGGER_DELVE
from ..domain import (
    BackendData,
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CatchpointType,
    CommandExecutionInfo,
    DisassemblyInfo,
    ExpressionValueInfo,
    FinishInfo,
    FollowForkMode,
    FrameInfo,
    FrameSelectionInfo,
    FunctionCallInfo,
    InferiorListInfo,
    InferiorRecord,
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
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from ..domain.results import payload_to_mapping
from ..transport.delve_rpc import DelveRpcClient, DelveRpcError
from .backends import BackendCapabilities, BackendOperation
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .debugger_args import debugger_args_problems
from .delve_breakpoints import DelveBreakpoints
from .delve_execution import DelveExecution
from .delve_inspection import DelveInspection
from .delve_protocols import operation as _operation
from .delve_values import (
    integer,
)
from .process_cleanup import cleanup_process
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .requests import (
    DEFAULT_EXECUTION_OPTIONS,
    DisassemblyRequest,
    ExecutionOptions,
    SourceContextRequest,
)
from .runtime import SessionRuntime
from .state import SessionState

_OPERATIONS: frozenset[BackendOperation] = frozenset(
    {
        "start",
        "stop",
        "status",
        "run",
        "attach",
        "continue",
        "step",
        "next",
        "step_instruction",
        "next_instruction",
        "finish",
        "interrupt",
        "wait_for_stop",
        "call_function",
        "list_inferiors",
        "select_inferior",
        "threads",
        "backtrace",
        "frame_info",
        "select_thread",
        "select_frame",
        "evaluate",
        "variables",
        "registers",
        "read_memory",
        "disassemble",
        "source",
        "set_breakpoint",
        "set_watchpoint",
        "delete_watchpoint",
        "list_breakpoints",
        "get_breakpoint",
        "delete_breakpoint",
        "enable_breakpoint",
        "disable_breakpoint",
        "update_breakpoint",
        ACTION_JUMP,
    }
)


class DelveRpcBackend:
    """One selected dlv executable or external RPC connection per session."""

    def __init__(self, *, os_module: OsModuleProtocol, time_module: TimeModuleProtocol) -> None:
        self.runtime = SessionRuntime(os_module=os_module, time_module=time_module)
        self._client: DelveRpcClient | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._output_workers: list[threading.Thread] = []
        self._output: deque[str] = deque(maxlen=2000)
        self._output_lock = threading.Lock()
        self._attached = False
        self._version: dict[str, object] = {}
        self._unsupported: set[BackendOperation] = set()
        self._inspection = DelveInspection(self.runtime, self)
        self._breakpoints = DelveBreakpoints(self, self._inspection)
        self._execution = DelveExecution(self.runtime, self, self._inspection, self._breakpoints)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            DEBUGGER_DELVE, _OPERATIONS - self._unsupported, concurrent_control=True
        )

    @property
    def has_controller(self) -> bool:
        """Return whether Delve owns an RPC client."""

        return self._client is not None

    def _require_client(self) -> DelveRpcClient:
        if self._client is None:
            raise DelveRpcError(
                "Delve has not started; attach a process or start a session with program",
                "not_started",
            )
        return self._client

    def _data(self, values: dict[str, object]) -> BackendData:
        with self._output_lock:
            output = "".join(self._output)
        return BackendData(DEBUGGER_DELVE, output, payload_to_mapping(values))

    def _request(
        self,
        method: str,
        arguments: dict[str, object] | None = None,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> dict[str, object]:
        return self._require_client().request(method, arguments, timeout_sec=timeout_sec)

    def _handshake(self, *, timeout_sec: float) -> None:
        self._request("SetApiVersion", {"APIVersion": 2}, timeout_sec=timeout_sec)
        version = self._request("GetVersion", timeout_sec=timeout_sec)
        if version.get("APIVersion") != 2:
            raise DelveRpcError("Delve did not select RPC API v2", "protocol_error")
        self._version = version
        self._client_state(self._request("State", {"NonBlocking": True}, timeout_sec=timeout_sec))

    def _connect(self, endpoint: str, *, timeout_sec: float) -> None:
        try:
            parsed = urlsplit(endpoint)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise DelveRpcError(str(exc)) from exc
        if parsed.scheme == "tcp" and host and port:
            connection = socket.create_connection((host, port), timeout=timeout_sec)
        elif parsed.scheme == "unix" and parsed.path and not parsed.netloc:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                connection.settimeout(timeout_sec)
                connection.connect(parsed.path)
            except OSError:
                connection.close()
                raise
        else:
            raise DelveRpcError(
                "Delve endpoint must be tcp://host:port or unix:///path", "validation_error"
            )
        self._client = DelveRpcClient(connection)
        self._handshake(timeout_sec=timeout_sec)

    def _spawn(self, *, pid: int | None = None, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> None:
        config = self.runtime.config
        if config is None:
            raise DelveRpcError("Session has not been configured", "not_started")
        selected = config.debugger_path or self.runtime.os_module.environ.get("DLV_PATH") or "dlv"
        executable = shutil.which(selected)
        if executable is None:
            raise DelveRpcError(f"Could not find Delve executable: {selected}", "not_found")
        command = [
            executable,
            "--headless",
            "--api-version=2",
            "--listen=127.0.0.1:0",
            *config.debugger_args,
        ]
        if pid is not None:
            command += ["attach", str(pid)]
            if config.program:
                command.append(config.program)
        elif config.core:
            if not config.program:
                raise DelveRpcError("Delve core debugging requires program", "validation_error")
            command += ["core", config.program, config.core]
        elif config.program:
            command += ["exec", config.program, "--", *config.args]
        else:
            raise DelveRpcError("Attach a process before using this Delve session", "not_started")
        env = dict(self.runtime.os_module.environ)
        env.update(config.env)
        ready = threading.Event()
        addresses: list[str] = []
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=config.working_dir,
            env=env,
        )
        self._process = process

        def drain(stream: BinaryIO | None) -> None:
            if stream is None:
                return
            startup_output = ""
            try:
                while chunk := os.read(stream.fileno(), 65536):
                    decoded = chunk.decode("utf-8", errors="replace")
                    with self._output_lock:
                        self._output.append(decoded)
                    startup_output = (startup_output + decoded)[-16384:]
                    match = re.search(r"API server listening at: (\S+)[\r\n]", startup_output)
                    if match:
                        addresses.append(match.group(1))
                        ready.set()
            except (OSError, ValueError):
                pass

        for stream in (process.stdout, process.stderr):
            worker = threading.Thread(
                target=drain, args=(stream,), name="delve-output", daemon=True
            )
            self._output_workers.append(worker)
            worker.start()
        deadline = time.monotonic() + timeout_sec
        try:
            while not ready.wait(min(0.05, max(0.0, deadline - time.monotonic()))):
                if process.poll() is not None or time.monotonic() >= deadline:
                    with self._output_lock:
                        output = "".join(self._output).strip()
                    raise DelveRpcError(
                        output or "Delve did not announce its RPC endpoint", "not_started"
                    )
            self._connect(
                f"tcp://{addresses[0]}", timeout_sec=max(0.1, deadline - time.monotonic())
            )
            self._attached = pid is not None
            if pid is not None:
                self.runtime.mark_attached(pid)
        except Exception:
            self._cleanup()
            raise

    @_operation
    def start(
        self,
        config: SessionConfig,
    ) -> OperationSuccess[SessionStartupInfo]:
        with self.runtime.lifecycle_lock:
            program = config.program
            debugger = config.debugger
            args = config.args
            init_commands = config.init_commands
            env = config.env
            debugger_args = config.debugger_args
            working_dir = config.working_dir
            core = config.core
            endpoint = config.endpoint
            replay = config.rr_replay
            if self.runtime.state not in {
                SessionState.CREATED,
                SessionState.STOPPED,
                SessionState.FAILED,
            }:
                raise DelveRpcError("Session already started; stop it first")
            if debugger not in {None, DEBUGGER_DELVE}:
                raise DelveRpcError(
                    f"Delve cannot start debugger {debugger!r}", "unsupported_combination"
                )
            if replay:
                raise DelveRpcError(
                    "Use an externally started Delve replay server with gdb_session_connect",
                    "unsupported_combination",
                )
            if init_commands:
                raise DelveRpcError(
                    "Delve RPC has no terminal command interpreter; use structured tools for initialization",
                    "unsupported_combination",
                )
            if core and args:
                raise DelveRpcError("Cannot combine core and args", "unsupported_combination")
            if endpoint and any((args, core, env, debugger_args)):
                raise DelveRpcError(
                    "An external Delve server owns its launch configuration",
                    "unsupported_combination",
                )
            if core and not program:
                raise DelveRpcError("Delve core debugging requires program", "validation_error")
            problems = debugger_args_problems(debugger_args, DEBUGGER_DELVE)
            if problems:
                raise DelveRpcError("; ".join(problems), "validation_error")
            if working_dir and not Path(working_dir).is_dir():
                raise DelveRpcError(f"Working directory does not exist: {working_dir}", "not_found")
            self.runtime.begin_startup(config)
            self._attached = False
            self._breakpoints.reset()
            self._unsupported.clear()
            self._version = {}
            self._execution.reset()
            with self._output_lock:
                self._output.clear()
            try:
                if endpoint:
                    self._connect(endpoint, timeout_sec=DEFAULT_TIMEOUT_SEC)
                    self._attached = bool(self._request("AttachedToExistingProcess").get("Answer"))
                elif program:
                    self._spawn()
            except Exception as exc:
                self._cleanup()
                self.runtime.mark_failed(str(exc))
                raise
            self.runtime.mark_ready()
            self.runtime.mark_follow_fork_mode(None)
            self.runtime.mark_detach_on_fork(None)
            if self._client is None:
                self.runtime.mark_debugger_running(False)
                self.runtime.update_inferior_inventory(
                    current_inferior_id=None, count=0, inferior_ids=()
                )
                self.runtime.execution_state = "not_started"
            diagnostics = StartupDiagnostics(
                [], [], self._data({"version": self._version, "deferred": self._client is None})
            )
            return OperationSuccess(
                SessionStartupInfo(
                    message=(
                        "Delve session configured; startup deferred until attach"
                        if self._client is None
                        else "Delve RPC session started"
                    ),
                    program=program,
                    core=core,
                    target_loaded=self.runtime.target_loaded,
                    execution_state=self.runtime.execution_state,
                    stop_reason=self.runtime.stop_reason,
                    stop_kind=self.runtime.stop_kind,
                    exit_code=self.runtime.exit_code,
                    diagnostics=diagnostics,
                    endpoint=endpoint,
                )
            )

    def _cleanup(self, *, graceful: bool = False) -> None:
        deadline = time.monotonic() + 10
        client, process = self._client, self._process
        self._execution.cancel_generation()

        def shutdown() -> None:
            if client is not None and client.alive:
                if not self._execution.done.is_set():
                    client.request("Command", {"name": "halt"}, timeout_sec=2)
                client.request("Detach", {"Kill": not self._attached}, timeout_sec=2)

        try:
            if process is not None:
                cleanup_process(process, graceful_shutdown=shutdown if graceful else None)
        finally:
            if client is not None:
                client.close()
            self._execution.done.wait(max(0.0, deadline - time.monotonic()))
            for worker in self._output_workers:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
            self._output_workers.clear()
            self._client = None
            self._process = None

    @_operation
    def stop(self) -> OperationSuccess[SessionMessage]:
        with self.runtime.lifecycle_lock:
            try:
                self._cleanup(graceful=True)
            finally:
                self.runtime.mark_stopped()
            return OperationSuccess(SessionMessage("Delve session stopped"))

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        client = self._client
        if client is not None:
            try:
                self._client_state(
                    self._request("State", {"NonBlocking": True}, timeout_sec=2), record=False
                )
            except DelveRpcError as exc:
                self.runtime.record_failure_message(str(exc))
                if not client.alive:
                    self.runtime.mark_transport_terminated(str(exc))
        return OperationSuccess(
            self.runtime.status_snapshot(
                debugger_alive=self._client is not None and self._client.alive,
            )
        )

    @_operation
    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo]:
        with self.runtime.lifecycle_lock:
            if pid <= 0:
                raise DelveRpcError("pid must be positive", "validation_error")
            if self._client is not None:
                raise DelveRpcError(
                    "This Delve session already owns a target; start an empty session to attach",
                    "unsupported_combination",
                )
            if self.runtime.state != SessionState.READY:
                raise DelveRpcError("Start a Delve session before attaching", "not_started")
            self._spawn(pid=pid, timeout_sec=timeout_sec)
            self.runtime.mark_debugger_running(True)
            return OperationSuccess(
                CommandExecutionInfo(
                    command="attach",
                    backend_data=self._data(
                        {"version": self._version, "State": self._execution.last_state}
                    ),
                )
            )

    def _client_state(self, response: dict[str, object], *, record: bool = True) -> None:
        self._execution._client_state(response, record=record)

    def run(
        self,
        args: list[str] | None = None,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.run(args, options)

    def continue_execution(
        self,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        return self._execution.continue_execution(options=options, reverse=reverse)

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

    def finish(
        self,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        return self._execution.finish(options, reverse)

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        return self._execution.interrupt()

    def wait_for_stop(
        self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC, stop_reasons: tuple[str, ...] = ()
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        return self._execution.wait_for_stop(timeout_sec=timeout_sec, stop_reasons=stop_reasons)

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        return self._execution.call_function(function_call, timeout_sec)

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
        self, expression: str, thread_id: int | None = None, frame: int | None = None
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

    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        return self._inspection.get_source_context(request)

    def disassemble(
        self, request: DisassemblyRequest
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        return self._inspection.disassemble(request)

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
        self, number: int, *, condition: str | None = None, clear_condition: bool = False
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        return self._breakpoints.update_breakpoint(
            number, condition=condition, clear_condition=clear_condition
        )

    def jump(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationSuccess[LocationExecutionInfo] | OperationError:
        return self._execution.jump(location, options=options)

    @_operation
    def list_inferiors(self) -> OperationSuccess[InferiorListInfo]:
        self._require_client()
        pid = integer(self._execution.last_state.get("Pid"))
        return OperationSuccess(
            InferiorListInfo(
                [
                    InferiorRecord(
                        inferior_id=1,
                        display=f"process {pid}",
                        executable=self.runtime.config.program if self.runtime.config else None,
                        execution_state=self.runtime.execution_state,
                        stop_reason=self.runtime.stop_reason,
                        exit_code=self.runtime.exit_code,
                    )
                ],
                1,
            )
        )

    @_operation
    def select_inferior(self, inferior_id: int) -> OperationSuccess[InferiorSelectionInfo]:
        self._require_client()
        if inferior_id != 1:
            raise DelveRpcError(
                f"Invalid inferior {inferior_id}; this Delve session has inferior 1", "not_found"
            )
        return OperationSuccess(
            InferiorSelectionInfo(
                1, executable=self.runtime.config.program if self.runtime.config else None
            )
        )

    @staticmethod
    def _unsupported_operation(operation: str) -> OperationError:
        return OperationError(
            message=f"Delve does not support {operation} through this tool",
            code="unsupported_operation",
        )

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationError:
        return self._unsupported_operation("terminal commands; use the structured debugging tools")

    def return_from_frame(self, *, value: str | None = None) -> OperationError:
        return self._unsupported_operation("forcing a frame to return")

    def until(
        self,
        location: str,
        *,
        options: ExecutionOptions = DEFAULT_EXECUTION_OPTIONS,
    ) -> OperationError:
        return self._unsupported_operation("frame-scoped until")

    def add_inferior(
        self, *, executable: str | None = None, make_current: bool = False
    ) -> OperationError:
        return self._unsupported_operation("adding an inferior; use another session")

    def remove_inferior(self, inferior_id: int) -> OperationError:
        return self._unsupported_operation("removing an inferior; stop the session")

    def set_follow_fork_mode(self, mode: FollowForkMode) -> OperationError:
        return self._unsupported_operation("GDB follow-fork mode")

    def set_detach_on_fork(self, enabled: bool) -> OperationError:
        return self._unsupported_operation("GDB detach-on-fork mode")

    def set_catchpoint(
        self, kind: CatchpointType, *, argument: str | None = None, temporary: bool = False
    ) -> OperationError:
        return self._unsupported_operation("GDB catchpoints")
