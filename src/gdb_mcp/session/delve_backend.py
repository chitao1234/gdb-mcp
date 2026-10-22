"""Go debugging through Delve's streaming JSON-RPC API v2."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import BinaryIO, Literal, TypeVar
from urllib.parse import urlsplit

from typing_extensions import ParamSpec

from ..contracts import ACTION_JUMP, DEBUGGER_DELVE
from ..domain import (
    AddressRange,
    BackendData,
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CatchpointType,
    CommandExecutionInfo,
    CommandTranscriptEntry,
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    ExpressionValueInfo,
    FinishInfo,
    FollowForkMode,
    FrameInfo,
    FrameSelectionInfo,
    FunctionCallInfo,
    InferiorListInfo,
    InferiorRecord,
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
    StopKind,
    ThreadListInfo,
    ThreadRecord,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from ..domain.results import payload_to_mapping
from ..transport.delve_rpc import DelveRpcClient, DelveRpcError, RpcRequest
from .backends import BackendCapabilities, BackendOperation
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .debugger_args import debugger_args_problems
from .delve_values import (
    breakpoint_record,
    frame_record,
    integer,
    mapping,
    mappings,
    text,
    variable_record,
    variable_value,
)
from .inspection.register_classes import guess_register_family, is_vector_register
from .process_cleanup import cleanup_process
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .runtime import SessionRuntime
from .state import SessionState

P = ParamSpec("P")
T = TypeVar("T")


def _operation(
    function: Callable[P, OperationSuccess[T]],
) -> Callable[P, OperationSuccess[T] | OperationError]:
    @wraps(function)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> OperationSuccess[T] | OperationError:
        try:
            return function(*args, **kwargs)
        except DelveRpcError as exc:
            return OperationError(message=str(exc), code=exc.code)
        except (OSError, ValueError) as exc:
            return OperationError(message=str(exc))

    return invoke


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

_LOAD_CONFIG: dict[str, object] = {
    "FollowPointers": True,
    "MaxVariableRecurse": 2,
    "MaxStringLen": 1024,
    "MaxArrayValues": 64,
    "MaxStructFields": -1,
}


class DelveRpcBackend:
    """One selected dlv executable or external RPC connection per session."""

    def __init__(self, *, os_module: OsModuleProtocol, time_module: TimeModuleProtocol) -> None:
        self.runtime = SessionRuntime(os_module=os_module, time_module=time_module)
        self._client: DelveRpcClient | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._output_workers: list[threading.Thread] = []
        self._output: deque[str] = deque(maxlen=2000)
        self._output_lock = threading.Lock()
        self._command_lock = threading.RLock()
        self._execution_done = threading.Event()
        self._execution_done.set()
        self._execution_error: DelveRpcError | None = None
        self._execution_result: dict[str, object] = {}
        self._pending: RpcRequest | None = None
        self._last_state: dict[str, object] = {}
        self._last_stop_key: tuple[object, ...] | None = None
        self._generation = 0
        self._command = ""
        self._has_run = False
        self._attached = False
        self._version: dict[str, object] = {}
        self._unsupported: set[BackendOperation] = set()
        self._temporary: set[int] = set()
        self._interrupt_requested = False

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            DEBUGGER_DELVE, _OPERATIONS - self._unsupported, concurrent_control=True
        )

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
        parsed = urlsplit(endpoint)
        if parsed.scheme == "tcp" and parsed.hostname and parsed.port:
            connection = socket.create_connection(
                (parsed.hostname, parsed.port), timeout=timeout_sec
            )
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
        self.runtime.controller = self._client
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
        except (OSError, ValueError, DelveRpcError):
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
            self._has_run = False
            self._attached = False
            self._temporary.clear()
            self._unsupported.clear()
            self._version = {}
            self._last_state = {}
            self._last_stop_key = None
            self._command = ""
            self._pending = None
            self._execution_error = None
            self._execution_result = {}
            self._execution_done = threading.Event()
            self._execution_done.set()
            with self._output_lock:
                self._output.clear()
            try:
                if endpoint:
                    self._connect(endpoint, timeout_sec=DEFAULT_TIMEOUT_SEC)
                    self._attached = bool(self._request("AttachedToExistingProcess").get("Answer"))
                elif program:
                    self._spawn()
            except (OSError, ValueError, DelveRpcError) as exc:
                self._cleanup()
                self.runtime.mark_failed(str(exc))
                raise
            self.runtime.mark_ready()
            self.runtime.follow_fork_mode = None
            self.runtime.detach_on_fork = None
            if self._client is None:
                self.runtime.is_running = False
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
        with self._command_lock:
            self._generation += 1

        def shutdown() -> None:
            if client is not None and client.alive:
                if not self._execution_done.is_set():
                    client.request("Command", {"name": "halt"}, timeout_sec=2)
                client.request("Detach", {"Kill": not self._attached}, timeout_sec=2)

        try:
            if process is not None:
                cleanup_process(process, graceful_shutdown=shutdown if graceful else None)
        finally:
            if client is not None:
                client.close()
            self._execution_done.wait(max(0.0, deadline - time.monotonic()))
            for worker in self._output_workers:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
            self._output_workers.clear()
            self._client = None
            self._process = None
            self.runtime.controller = None

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
                self.runtime.last_failure_message = str(exc)
                if not client.alive:
                    self.runtime.mark_transport_terminated(str(exc))
        return OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=self._client is not None and self._client.alive,
                target_loaded=self.runtime.target_loaded,
                session_state=self.runtime.state,
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                exit_code=self.runtime.exit_code,
                current_inferior_id=self.runtime.current_inferior_id,
                inferior_count=self.runtime.inferior_count,
                inferior_states=self.runtime.inferiors_state_summary() or None,
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
            self.runtime.is_running = True
            return OperationSuccess(
                CommandExecutionInfo(
                    command="attach",
                    backend_data=self._data({"version": self._version, "State": self._last_state}),
                )
            )

    def _client_state(self, response: dict[str, object], *, record: bool = True) -> None:
        state = mapping(response.get("State"))
        if not state:
            raise DelveRpcError("Delve returned no debugger state", "protocol_error")
        with self._command_lock:
            if (
                not record
                and self._pending is not None
                and not self._execution_done.is_set()
                and not state.get("Running")
            ):
                return
            self._last_state = state
            self.runtime.target_loaded = True
            self.runtime.update_inferior_inventory(
                current_inferior_id=1, count=1, inferior_ids=(1,)
            )
            thread = mapping(state.get("currentThread"))
            goroutine = mapping(state.get("currentGoroutine"))
            goroutine_id = integer(goroutine.get("id")) or integer(thread.get("goroutineID"))
            previous_goroutine = self.runtime.current_thread_id
            self.runtime.mark_thread_selected(goroutine_id or None)
            if previous_goroutine != (goroutine_id or None) or self.runtime.current_frame is None:
                self.runtime.mark_frame_selected(0)
            if state.get("Running"):
                self.runtime.mark_inferior_running()
                return
            breakpoint = mapping(thread.get("breakPoint"))
            native_reason = text(state.get("stopReason"))
            reason = native_reason
            if state.get("exited"):
                reason = reason or "exited"
                self.runtime.mark_inferior_exited(reason, integer(state.get("exitStatus")))
            else:
                if integer(breakpoint.get("id")) == -1:
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
                self.runtime.mark_inferior_paused(reason, stop_kind=canonical)
            if not record:
                return
            frame = frame_record(goroutine.get("currentLoc") or thread)
            key = (
                self._generation,
                self.runtime.execution_state,
                reason,
                goroutine_id,
                frame.addr,
                integer(breakpoint.get("id")),
            )
            if key == self._last_stop_key:
                return
            self._last_stop_key = key
            self.runtime.record_stop_event(
                StopEvent(
                    execution_state=self.runtime.execution_state,
                    reason=reason,
                    stop_kind=self.runtime.stop_kind,
                    thread_id=goroutine_id or None,
                    inferior_id=1,
                    frame=frame,
                    breakpoint_number=str(breakpoint["id"]) if "id" in breakpoint else None,
                    exit_code=self.runtime.exit_code,
                    timestamp=time.time(),
                    backend_data=self._data({"State": state}),
                )
            )

    def _begin_execution(self, command: str, arguments: dict[str, object] | None = None) -> None:
        with self._command_lock:
            if not self._execution_done.is_set() or self.runtime.execution_state == "running":
                raise DelveRpcError("The target is already running", "inferior_running")
            if self.runtime.config is not None and self.runtime.config.core:
                raise DelveRpcError("Cannot execute a core dump", "unsupported_combination")
            payload = {"name": command, "ReturnInfoLoadConfig": _LOAD_CONFIG, **(arguments or {})}
            request = self._require_client().submit("Command", payload)
            self._generation += 1
            generation = self._generation
            self._command = command
            self._interrupt_requested = False
            self._pending = request
            self._execution_done = threading.Event()
            done = self._execution_done
            self._execution_error = None
            self._has_run = True
            self.runtime.mark_frame_selected(None)
            self.runtime.mark_inferior_running()

        def complete() -> None:
            try:
                response = request.wait()
                with self._command_lock:
                    if generation != self._generation:
                        return
                    self._execution_result = response
                    self._client_state(response)
                self._remove_hit_temporary_breakpoints()
            except DelveRpcError as exc:
                with self._command_lock:
                    if generation == self._generation:
                        self._execution_error = exc
                        self._pending = None
                        if self._client is not None and self._client.alive:
                            try:
                                self._client_state(
                                    self._request("State", {"NonBlocking": True}), record=False
                                )
                            except DelveRpcError:
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
            if not self._request("Recorded").get("Recorded"):
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
            if not self._execution_done.wait(timeout_sec):
                raise DelveRpcError(
                    f"Timed out waiting for Delve {command}; the target may still be running",
                    "command_timeout",
                )
            if self._execution_error is not None:
                raise self._execution_error
        return OperationSuccess(
            CommandExecutionInfo(
                command=command,
                backend_data=self._data(
                    self._execution_result
                    if self._execution_done.is_set()
                    else {"execution_state": "running"}
                ),
            )
        )

    @_operation
    def run(
        self,
        args: list[str] | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        wait_for_stop: bool = True,
    ) -> OperationSuccess[CommandExecutionInfo]:
        if self._attached:
            raise DelveRpcError(
                "An attached target uses continue, not run/restart", "unsupported_combination"
            )
        if self.runtime.config is not None and self.runtime.config.core:
            raise DelveRpcError("Cannot execute a core dump", "unsupported_combination")
        if self.runtime.execution_state == "running" or not self._execution_done.is_set():
            raise DelveRpcError("The target is already running", "inferior_running")
        if self._has_run or args is not None:
            self._request(
                "Restart", {"ResetArgs": args is not None, "NewArgs": args}, timeout_sec=timeout_sec
            )
            self.runtime.mark_inferior_paused("entry")
        return self._execute("continue", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)

    @_operation
    def continue_execution(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return self._execute(
            "continue", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def step(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return self._execute(
            "step", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def next(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return self._execute(
            "next", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def step_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return self._execute(
            "stepInstruction", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def next_instruction(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo]:
        return self._execute(
            "nextInstruction", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )

    @_operation
    def finish(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo]:
        self._execute(
            "stepOut", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec, reverse=reverse
        )
        returns = (
            mappings(mapping(self._last_state.get("currentThread")).get("ReturnValues"))
            if self._execution_done.is_set()
            else []
        )
        return OperationSuccess(
            FinishInfo(
                message="Finished frame" if self._execution_done.is_set() else "Finish started",
                return_value=(
                    ", ".join(variable_value(value) for value in returns) if returns else None
                ),
                frame=(
                    self.runtime.last_stop_event.frame
                    if self._execution_done.is_set() and self.runtime.last_stop_event is not None
                    else None
                ),
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                last_stop_event=(
                    self.runtime.last_stop_event if self._execution_done.is_set() else None
                ),
            )
        )

    @_operation
    def interrupt(self) -> OperationSuccess[MessageResult]:
        self._interrupt_requested = True
        response = self._request("Command", {"name": "halt"})
        if self._execution_done.is_set():
            self._client_state(response)
        return OperationSuccess(MessageResult("Interrupt requested", self._data(response)))

    @_operation
    def wait_for_stop(
        self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC, stop_reasons: tuple[str, ...] = ()
    ) -> OperationSuccess[WaitForStopInfo]:
        self._require_client()
        source = "existing" if self.runtime.execution_state != "running" else "waited"
        deadline = time.monotonic() + timeout_sec
        completed = self._execution_done.wait(timeout_sec)
        if completed and self._execution_error is not None:
            raise self._execution_error
        if completed:
            self._client_state(self._request("State", {"NonBlocking": True}))
        while (
            completed and self.runtime.execution_state == "running" and time.monotonic() < deadline
        ):
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            self._client_state(
                self._request(
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
        returns = mappings(mapping(self._last_state.get("currentThread")).get("ReturnValues"))
        return OperationSuccess(
            FunctionCallInfo(function_call, ", ".join(variable_value(value) for value in returns))
        )

    def _scope(self, thread_id: int | None = None, frame: int | None = None) -> dict[str, object]:
        self._require_client()
        if self.runtime.execution_state == "running":
            raise DelveRpcError("Pause the target before inspecting it", "inferior_running")
        return {
            "GoroutineID": (
                thread_id if thread_id is not None else self.runtime.current_thread_id or -1
            ),
            "Frame": frame if frame is not None else self.runtime.current_frame or 0,
        }

    @_operation
    def get_threads(self) -> OperationSuccess[ThreadListInfo]:
        self._scope()
        threads: list[ThreadRecord] = []
        start = 0
        while True:
            response = self._request("ListGoroutines", {"Start": start, "Count": 1000})
            for goroutine in mappings(response.get("Goroutines")):
                location = mapping(goroutine.get("userCurrentLoc")) or mapping(
                    goroutine.get("currentLoc")
                )
                values = dict(goroutine)
                values.update(
                    {
                        "id": str(integer(goroutine.get("id"))),
                        "name": text(mapping(location.get("function")).get("name")),
                        "state": "running" if integer(goroutine.get("threadID")) else "waiting",
                        "target-id": f"goroutine {integer(goroutine.get('id'))}",
                        "native_thread_id": integer(goroutine.get("threadID")),
                        "frame": frame_record(location).to_payload(),
                    }
                )
                threads.append(ThreadRecord.from_payload(values))
            next_start = integer(response.get("Nextg"), -1)
            if next_start < 0:
                break
            if next_start <= start:
                raise DelveRpcError("Delve goroutine pagination did not advance", "protocol_error")
            start = next_start
        return OperationSuccess(
            ThreadListInfo(
                threads,
                (
                    str(self.runtime.current_thread_id)
                    if self.runtime.current_thread_id is not None
                    else None
                ),
            )
        )

    def _stack(self, thread_id: int | None, max_frames: int) -> list[dict[str, object]]:
        scope = self._scope(thread_id)
        response = self._request("Stacktrace", {"Id": scope["GoroutineID"], "Depth": max_frames})
        return mappings(response.get("Locations"))[:max_frames]

    @_operation
    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo]:
        frames = [
            frame_record(location, index)
            for index, location in enumerate(self._stack(thread_id, max_frames))
        ]
        return OperationSuccess(
            BacktraceInfo(
                thread_id if thread_id is not None else self.runtime.current_thread_id, frames
            )
        )

    @_operation
    def get_frame_info(
        self, *, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[FrameInfo]:
        number = frame if frame is not None else self.runtime.current_frame or 0
        locations = self._stack(thread_id, number + 1)
        if number < 0 or number >= len(locations):
            raise DelveRpcError(f"Frame {number} is unavailable", "no_stack_frame")
        return OperationSuccess(FrameInfo(frame_record(locations[number], number)))

    @_operation
    def select_thread(self, thread_id: int) -> OperationSuccess[ThreadSelectionInfo]:
        self._scope(thread_id)
        response = self._request("Command", {"name": "switchGoroutine", "goroutineID": thread_id})
        self._client_state(response, record=False)
        self.runtime.mark_frame_selected(0)
        location = mapping(self._last_state.get("currentGoroutine")).get("currentLoc")
        return OperationSuccess(
            ThreadSelectionInfo(thread_id, str(thread_id), frame_record(location))
        )

    @_operation
    def select_frame(self, frame_number: int) -> OperationSuccess[FrameSelectionInfo]:
        if frame_number < 0:
            raise DelveRpcError("frame must be nonnegative", "validation_error")
        locations = self._stack(None, frame_number + 1)
        if frame_number >= len(locations):
            raise DelveRpcError(f"Frame {frame_number} is unavailable", "no_stack_frame")
        self.runtime.mark_frame_selected(frame_number)
        return OperationSuccess(
            FrameSelectionInfo(frame_number, frame_record(locations[frame_number], frame_number))
        )

    @_operation
    def evaluate_expression(
        self, expression: str, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[ExpressionValueInfo]:
        response = self._request(
            "Eval",
            {"Scope": self._scope(thread_id, frame), "Expr": expression, "Cfg": _LOAD_CONFIG},
        )
        variable = mapping(response.get("Variable"))
        if variable.get("unreadable"):
            raise DelveRpcError(text(variable["unreadable"]), "value_unavailable")
        return OperationSuccess(
            ExpressionValueInfo(
                expression, variable_value(variable), type=text(variable.get("type"))
            )
        )

    @_operation
    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo]:
        arguments: dict[str, object] = {"Scope": self._scope(thread_id, frame), "Cfg": _LOAD_CONFIG}
        locals_response = self._request("ListLocalVars", arguments)
        args_response = self._request("ListFunctionArgs", arguments)
        variables = [
            variable_record(value, argument=True) for value in mappings(args_response.get("Args"))
        ]
        variables += [
            variable_record(value) for value in mappings(locals_response.get("Variables"))
        ]
        return OperationSuccess(
            VariablesInfo(
                thread_id if thread_id is not None else self.runtime.current_thread_id,
                frame,
                variables,
            )
        )

    @_operation
    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo]:
        scope = self._scope(thread_id, frame)
        response = self._request(
            "ListRegisters", {"Scope": scope, "IncludeFp": include_vector_registers}
        )
        registers = mappings(response.get("Regs"))
        names = {name.casefold() for name in register_names or ()}
        family = guess_register_family([text(value.get("Name")) for value in registers])
        records: list[RegisterRecord] = []
        for register in registers:
            name = text(register.get("Name"))
            number = integer(register.get("DwarfNumber"))
            if register_numbers is not None and number not in register_numbers:
                continue
            if names and name.casefold() not in names:
                continue
            if not include_vector_registers and is_vector_register(family, name):
                continue
            value = text(register.get("Value"))
            if value_format == "natural":
                try:
                    value = str(int(value, 0))
                except ValueError:
                    pass
            records.append(RegisterRecord(number=number, name=name, value=value))
        if max_registers is not None:
            records = records[:max_registers]
        return OperationSuccess(RegistersInfo(records))

    def _address(self, expression: str) -> int:
        try:
            return int(expression, 0)
        except ValueError:
            variable = mapping(
                self._request(
                    "Eval", {"Scope": self._scope(), "Expr": expression, "Cfg": _LOAD_CONFIG}
                ).get("Variable")
            )
            value = text(variable.get("value"))
            if value:
                return int(value, 0)
            children = mappings(variable.get("children"))
            if children:
                return integer(children[0].get("addr"))
            return integer(variable.get("addr"))

    @_operation
    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo]:
        self._scope()
        begin = self._address(address) + offset
        if begin < 0 or count < 0:
            raise DelveRpcError("Memory address and count must be nonnegative", "validation_error")
        contents = bytearray()
        while len(contents) < count:
            requested = min(65536, count - len(contents))
            response = self._request(
                "ExamineMemory", {"Address": begin + len(contents), "Length": requested}
            )
            raw = response.get("Mem")
            if not isinstance(raw, str):
                raise DelveRpcError("Delve memory response is not base64", "protocol_error")
            data = base64.b64decode(raw, validate=True)
            if not data or len(data) > requested:
                raise DelveRpcError("Delve memory response has an invalid length", "protocol_error")
            contents.extend(data)
        blocks = (
            [
                MemoryBlockRecord(
                    begin=hex(begin),
                    end=hex(begin + len(contents)),
                    offset=hex(offset),
                    contents=contents.hex(),
                )
            ]
            if contents
            else []
        )
        return OperationSuccess(MemoryReadInfo(address, count, offset, blocks, len(contents)))

    def _locations(
        self, expression: str, thread_id: int | None = None, frame: int | None = None
    ) -> list[dict[str, object]]:
        response = self._request(
            "FindLocation", {"Scope": self._scope(thread_id, frame), "Loc": expression}
        )
        locations = mappings(response.get("Locations"))
        if not locations:
            raise DelveRpcError(f"No code location for {expression!r}", "not_found")
        return locations

    def _location(
        self,
        *,
        thread_id: int | None,
        frame: int | None,
        function: str | None,
        address: str | None,
        file: str | None,
        line: int | None,
    ) -> tuple[dict[str, object], InspectionLocation]:
        if function is not None:
            location = self._locations(function, thread_id, frame)[0]
            kind: Literal["current", "function", "address", "file_line"] = "function"
        elif address is not None:
            location = self._locations(f"*{hex(self._address(address))}", thread_id, frame)[0]
            kind = "address"
        elif file is not None and line is not None:
            location = self._locations(f"{file}:{line}", thread_id, frame)[0]
            kind = "file_line"
        else:
            number = frame if frame is not None else self.runtime.current_frame or 0
            stack = self._stack(thread_id, number + 1)
            if number < 0 or number >= len(stack):
                raise DelveRpcError("No stack frame available", "no_stack_frame")
            location = stack[number]
            kind = "current"
        normalized = InspectionLocation(
            kind=kind,
            function=text(mapping(location.get("function")).get("name")) or function,
            address=hex(integer(location.get("pc"))),
            file=text(location.get("file")) or file,
            fullname=text(location.get("file")) or file,
            line=integer(location.get("line")) or line,
        )
        return location, normalized

    @_operation
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
    ) -> OperationSuccess[SourceContextInfo]:
        self._scope(thread_id, frame)
        if file is not None and start_line is not None:
            location = InspectionLocation(
                kind="file_range", file=file, fullname=file, line=start_line
            )
            current_line = 0
        else:
            _, location = self._location(
                thread_id=thread_id,
                frame=frame,
                function=function,
                address=address,
                file=file,
                line=line,
            )
            current_line = location.line or 1
        path = Path(location.fullname or location.file or "")
        if (
            not path.is_absolute()
            and self.runtime.config is not None
            and self.runtime.config.working_dir
        ):
            path = Path(self.runtime.config.working_dir) / path
        source = path.read_text(encoding="utf-8", errors="replace").splitlines()
        first = start_line if start_line is not None else max(1, current_line - context_before)
        last = min(len(source), end_line if end_line is not None else current_line + context_after)
        if first < 1 or last < first:
            raise DelveRpcError("Requested source range is unavailable", "not_found")
        lines = [
            SourceLineRecord(
                line_number=number, text=source[number - 1], is_current=number == current_line
            )
            for number in range(first, last + 1)
        ]
        return OperationSuccess(
            SourceContextInfo(
                InspectionContextInfo(
                    thread_id if thread_id is not None else self.runtime.current_thread_id,
                    frame if frame is not None else self.runtime.current_frame,
                ),
                location,
                SourceRange(first, last),
                lines,
            )
        )

    @_operation
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
    ) -> OperationSuccess[DisassemblyInfo]:
        scope = self._scope(thread_id, frame)
        raw, location = self._location(
            thread_id=thread_id,
            frame=frame,
            function=function,
            address=start_address or address,
            file=file,
            line=line,
        )
        start = integer(raw.get("pc"))
        end = self._address(end_address) if end_address is not None else 0
        response = self._request(
            "Disassemble", {"Scope": scope, "StartPC": start, "EndPC": end, "Flavour": 0}
        )
        instructions: list[DisassemblyInstructionRecord] = []
        for item in mappings(response.get("Disassemble")):
            loc = mapping(item.get("Loc"))
            pc = integer(loc.get("pc"))
            if pc < start:
                continue
            encoded = item.get("Bytes")
            opcodes = base64.b64decode(encoded).hex(" ") if isinstance(encoded, str) else None
            instructions.append(
                DisassemblyInstructionRecord(
                    address=hex(pc),
                    instruction=text(item.get("Text")),
                    opcodes=opcodes,
                    function=text(mapping(loc.get("function")).get("name")),
                    file=text(loc.get("file")) if mode == "mixed" else None,
                    line=integer(loc.get("line")) if mode == "mixed" else None,
                    is_current=bool(item.get("AtPC")),
                )
            )
            if len(instructions) >= instruction_count:
                break
        return OperationSuccess(
            DisassemblyInfo(
                InspectionContextInfo(
                    thread_id if thread_id is not None else self.runtime.current_thread_id,
                    frame if frame is not None else self.runtime.current_frame,
                ),
                location,
                AddressRange(hex(start), hex(end) if end else None),
                mode,
                instructions,
            )
        )

    @_operation
    def set_breakpoint(
        self, location: str, condition: str | None = None, temporary: bool = False
    ) -> OperationSuccess[BreakpointInfo]:
        locations = self._locations(location)
        addresses: list[int] = []
        for loc in locations:
            pcs = loc.get("pcs")
            addresses.extend(
                [integer(pc) for pc in pcs] if isinstance(pcs, list) else [integer(loc.get("pc"))]
            )
        response = self._request(
            "CreateBreakpoint", {"Breakpoint": {"addrs": addresses, "Cond": condition or ""}}
        )
        breakpoint = mapping(response.get("Breakpoint"))
        if temporary:
            self._temporary.add(integer(breakpoint.get("id")))
        return OperationSuccess(BreakpointInfo(breakpoint_record(breakpoint, temporary=temporary)))

    @_operation
    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo]:
        response = self._request(
            "CreateWatchpoint",
            {
                "Scope": self._scope(),
                "Expr": expression,
                "Type": {"read": 1, "write": 2, "access": 3}[access],
            },
        )
        return OperationSuccess(BreakpointInfo(breakpoint_record(response)))

    @_operation
    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        self._scope()
        self._request("ClearBreakpoint", {"Id": number})
        return OperationSuccess(SessionMessage(f"Watchpoint {number} deleted"))

    @_operation
    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo]:
        self._scope()
        breakpoints = mappings(self._request("ListBreakpoints", {"All": False}).get("Breakpoints"))
        return OperationSuccess(
            BreakpointListInfo(
                [
                    breakpoint_record(value, temporary=integer(value.get("id")) in self._temporary)
                    for value in breakpoints
                ]
            )
        )

    def _breakpoint(self, number: int) -> dict[str, object]:
        self._scope()
        return mapping(self._request("GetBreakpoint", {"Id": number}).get("Breakpoint"))

    @_operation
    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo]:
        return OperationSuccess(
            BreakpointInfo(
                breakpoint_record(self._breakpoint(number), temporary=number in self._temporary)
            )
        )

    @_operation
    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        self._scope()
        self._request("ClearBreakpoint", {"Id": number})
        self._temporary.discard(number)
        return OperationSuccess(SessionMessage(f"Breakpoint {number} deleted"))

    def _remove_hit_temporary_breakpoints(self) -> None:
        hit = {
            integer(mapping(thread.get("breakPoint")).get("id"))
            for thread in mappings(self._last_state.get("Threads"))
        }
        hit.add(
            integer(
                mapping(mapping(self._last_state.get("currentThread")).get("breakPoint")).get("id")
            )
        )
        for number in hit & self._temporary:
            self._request("ClearBreakpoint", {"Id": number})
            self._temporary.discard(number)

    def _enable_breakpoint(self, number: int, enabled: bool) -> OperationSuccess[SessionMessage]:
        breakpoint = self._breakpoint(number)
        breakpoint["disabled"] = not enabled
        self._request("AmendBreakpoint", {"Breakpoint": breakpoint})
        return OperationSuccess(
            SessionMessage(f"Breakpoint {number} {'enabled' if enabled else 'disabled'}")
        )

    @_operation
    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        return self._enable_breakpoint(number, True)

    @_operation
    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage]:
        return self._enable_breakpoint(number, False)

    @_operation
    def update_breakpoint(
        self, number: int, *, condition: str | None = None, clear_condition: bool = False
    ) -> OperationSuccess[BreakpointInfo]:
        breakpoint = self._breakpoint(number)
        if clear_condition or condition is not None:
            breakpoint["Cond"] = "" if clear_condition else condition
            self._request("AmendBreakpoint", {"Breakpoint": breakpoint})
        return OperationSuccess(
            BreakpointInfo(breakpoint_record(breakpoint, temporary=number in self._temporary))
        )

    @_operation
    def jump(
        self, location: str, *, wait_for_stop: bool = True, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[LocationExecutionInfo]:
        pc = integer(self._locations(location)[0].get("pc"))
        try:
            self._client_state(self._request("SetExecutionPoint", {"Addr": pc}))
        except DelveRpcError as exc:
            if exc.code == "unsupported_operation":
                self._unsupported.add(ACTION_JUMP)
            raise
        self._execute("continue", wait_for_stop=wait_for_stop, timeout_sec=timeout_sec)
        return OperationSuccess(
            LocationExecutionInfo(
                command=ACTION_JUMP,
                backend_data=self._data(self._execution_result),
                location=location,
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                last_stop_event=(
                    self.runtime.last_stop_event if self._execution_done.is_set() else None
                ),
            )
        )

    @_operation
    def list_inferiors(self) -> OperationSuccess[InferiorListInfo]:
        self._require_client()
        pid = integer(self._last_state.get("Pid"))
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
        self, location: str, *, wait_for_stop: bool = True, timeout_sec: int = DEFAULT_TIMEOUT_SEC
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
