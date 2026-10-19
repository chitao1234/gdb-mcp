"""The LLDB backend: drive ``lldb-dap`` over the Debug Adapter Protocol.

LLDB's adapter speaks DAP, not GDB/MI, so this backend maps the same session
operations onto DAP requests and events. Two consequences shape it:

* some operations have no DAP equivalent (catchpoints, inferior management, fork
  settings, watchpoints, reverse execution, memory reads, disassembly, source
  windows). They answer with an explicit ``unsupported_operation`` error and stay
  out of the published capabilities instead of pretending;
* LLDB's own command language stays available through the escape hatch: an
  ``evaluate`` request with ``context: "repl"`` runs a raw LLDB command and
  returns its text.

DAP has no "load without running" request, so `start` launches the debuggee with
``stopOnEntry`` and the session begins paused at ``stop_kind="entry"`` rather than
``not_started``.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Mapping
from typing import Any, Literal

from ..contracts import DEBUGGER_LLDB
from ..domain import (
    BacktraceInfo,
    BreakpointInfo,
    BreakpointListInfo,
    BreakpointRecord,
    CatchpointType,
    CommandExecutionInfo,
    DetachOnForkInfo,
    DisassemblyInfo,
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
    MemoryReadInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
    SourceContextInfo,
    StopEvent,
    ThreadListInfo,
    ThreadRecord,
    ThreadSelectionInfo,
    VariableRecord,
    VariablesInfo,
    WaitForStopInfo,
    WatchpointAccessType,
)
from ..transport import DapClient, DapStdioStream, DapStream, dap_error_message
from ..transport.dap_client import DapTransportError
from .backends import BackendCapabilities, BackendOperation
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC
from .protocols import OsModuleProtocol, TimeModuleProtocol
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)

#: Operations this backend implements. Everything else answers with an
#: ``unsupported_operation`` error and stays out of the published capabilities.
_OPERATIONS: tuple[BackendOperation, ...] = (
    "start",
    "stop",
    "status",
    "command",
    "run",
    "continue",
    "step",
    "next",
    "finish",
    "interrupt",
    "wait_for_stop",
    "attach",
    "call_function",
    "threads",
    "backtrace",
    "frame_info",
    "select_thread",
    "select_frame",
    "evaluate",
    "variables",
    "registers",
    "set_breakpoint",
    "list_breakpoints",
    "get_breakpoint",
    "delete_breakpoint",
    "enable_breakpoint",
    "disable_breakpoint",
    "update_breakpoint",
    "list_inferiors",
    "select_inferior",
)

#: ``levels=0`` asks DAP for every frame of the thread.
_UNBOUNDED_FRAMES = 0


class LldbDapBackend:
    """Drive LLDB through its Debug Adapter Protocol server."""

    def __init__(
        self,
        *,
        os_module: OsModuleProtocol,
        time_module: TimeModuleProtocol,
        stream_factory: Callable[..., DapStream] | None = None,
        adapter_path: str | None = None,
    ) -> None:
        self.runtime = SessionRuntime(os_module=os_module, time_module=time_module)
        self._time = time_module
        self._stream_factory: Callable[..., DapStream] = stream_factory or DapStdioStream
        self._adapter_path = adapter_path
        self._client: DapClient | None = None
        self._capabilities = BackendCapabilities(
            debugger=DEBUGGER_LLDB,
            operations=frozenset(_OPERATIONS),
        )
        self._breakpoints: dict[int, BreakpointRecord] = {}
        self._next_breakpoint_id = 1

    # -- capability surface -------------------------------------------------

    @property
    def capabilities(self) -> BackendCapabilities:
        """The operations this backend implements."""

        return self._capabilities

    # -- lifecycle ----------------------------------------------------------

    def start(
        self,
        program: str | None = None,
        debugger: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        debugger_path: str | None = None,
        debugger_args: list[str] | None = None,
        working_dir: str | None = None,
        core: str | None = None,
        endpoint: str | None = None,
        replay: str | None = None,
    ) -> OperationSuccess[SessionStartInfo] | OperationError:
        """Spawn ``lldb-dap`` and launch the program at its entry point."""

        with self.runtime.lifecycle_lock:
            if self.runtime.has_controller:
                return OperationError(message="Session already running. Stop it first.")

            if debugger not in (None, DEBUGGER_LLDB):
                message = f"This session drives lldb, not {debugger!r}"
                self.runtime.mark_failed(message)
                return OperationError(message=message, code="unsupported_operation")

            rejection = self._reject_unsupported_start(core=core, endpoint=endpoint, replay=replay)
            if rejection is not None:
                return rejection

            adapter = self._resolve_adapter(debugger_path or self._adapter_path)
            if isinstance(adapter, OperationError):
                return adapter

            self.runtime.begin_startup(
                SessionConfig.from_inputs(
                    program=program,
                    debugger=debugger or DEBUGGER_LLDB,
                    args=args,
                    init_commands=init_commands,
                    env=env,
                    gdb_path=adapter,
                    gdb_args=None,
                    working_dir=working_dir,
                    core=None,
                    endpoint=None,
                    rr_replay=None,
                )
            )

            try:
                stream = self._stream_factory([adapter, *(debugger_args or ())], cwd=working_dir)
                client = DapClient(stream)
                client.initialize(client_id="gdb-mcp", adapter_id="lldb")
            except (DapTransportError, OSError) as exc:
                self.runtime.mark_failed(f"Could not start lldb-dap: {exc}")
                return OperationError(message=f"Could not start lldb-dap: {exc}")

            self._client = client
            self.runtime.controller = stream
            self.runtime.mark_ready()

            if program is None:
                return OperationSuccess(
                    self._start_info(
                        message="LLDB session started (no program loaded)",
                        program=None,
                    )
                )

            launch = self._launch_arguments(
                program=program,
                args=args,
                env=env,
                init_commands=init_commands,
                working_dir=working_dir,
            )
            failure = self._launch(client, launch)
            if failure is not None:
                self._shutdown()
                self.runtime.mark_failed(failure.message)
                return failure

            return OperationSuccess(
                self._start_info(
                    message="LLDB session started at the program entry point",
                    program=program,
                )
            )

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        """Disconnect the adapter and close the session."""

        with self.runtime.lifecycle_lock:
            if not self.runtime.has_controller:
                return OperationError(message="No active session")

            self._shutdown()
            self.runtime.mark_stopped()
            return OperationSuccess(SessionMessage(message="LLDB session stopped"))

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        """Describe the session state this backend keeps up to date."""

        self._drain_events(timeout_sec=0.0)
        return OperationSuccess(self._status_snapshot())

    # -- execution ----------------------------------------------------------

    def run(
        self,
        args: list[str] | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        wait_for_stop: bool = True,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Resume the debuggee; DAP launched it already."""

        if args:
            return OperationError(
                message="lldb launches the program it was started with; restart the session "
                "to change its arguments",
                code="unsupported_operation",
            )
        return self._resume("continue", timeout_sec=timeout_sec, wait_for_stop=wait_for_stop)

    def continue_execution(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Resume execution, optionally backwards."""

        if reverse:
            return self._unsupported("reverse execution")
        return self._resume("continue", timeout_sec=timeout_sec, wait_for_stop=wait_for_stop)

    def step(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step into the next call."""

        if reverse:
            return self._unsupported("reverse execution")
        return self._resume("stepIn", timeout_sec=timeout_sec, wait_for_stop=wait_for_stop)

    def next(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step over the next call."""

        if reverse:
            return self._unsupported("reverse execution")
        return self._resume("next", timeout_sec=timeout_sec, wait_for_stop=wait_for_stop)

    def finish(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        """Run out of the current frame."""

        if reverse:
            return self._unsupported("reverse execution")

        resumed = self._resume("stepOut", timeout_sec=timeout_sec, wait_for_stop=wait_for_stop)
        if isinstance(resumed, OperationError):
            return resumed

        event = self.runtime.last_stop_event
        return OperationSuccess(
            FinishInfo(
                message="Stepped out of the current frame",
                frame=event.frame if event is not None else None,
                execution_state=self.runtime.execution_state,
                stop_reason=self.runtime.stop_reason,
                stop_kind=self.runtime.stop_kind,
                last_stop_event=event,
            )
        )

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        """Pause the running program."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client

        response = client.request("pause", timeout_sec=DEFAULT_TIMEOUT_SEC)
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)

        event = self._wait_for_stop_event(DEFAULT_TIMEOUT_SEC)
        if event is None:
            return OperationSuccess(
                MessageResult(
                    message="Pause requested but the adapter reported no stop",
                    result={},
                    status="warning",
                )
            )

        self._apply_stop_event(event)
        return OperationSuccess(MessageResult(message="Program interrupted (paused)", result={}))

    def wait_for_stop(
        self,
        *,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        stop_reasons: tuple[str, ...] = (),
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        """Wait for the next stop, optionally matching one of several reasons."""

        if not self.runtime.has_controller:
            return OperationError(message="No active LLDB session")

        reason_filter = list(stop_reasons) or None
        if self.runtime.execution_state != "running":
            matched = not stop_reasons or self.runtime.stop_reason in stop_reasons
            return OperationSuccess(
                self._wait_info(
                    message=(
                        "Inferior is already stopped"
                        if matched
                        else "Inferior is stopped for another reason"
                    ),
                    matched=matched,
                    timed_out=False,
                    source="existing",
                    reason_filter=reason_filter,
                )
            )

        deadline = self._time.time() + timeout_sec
        while True:
            remaining = deadline - self._time.time()
            if remaining <= 0:
                return OperationSuccess(
                    self._wait_info(
                        message=f"No stop event within {timeout_sec}s",
                        matched=False,
                        timed_out=True,
                        source="timeout",
                        reason_filter=reason_filter,
                    )
                )
            event = self._drain_events(timeout_sec=remaining)
            if event is None:
                continue
            if not stop_reasons or self.runtime.stop_reason in stop_reasons:
                break

        return OperationSuccess(
            self._wait_info(
                message="Stopped",
                matched=True,
                timed_out=False,
                source="notification",
                reason_filter=reason_filter,
            )
        )

    # -- escape hatch -------------------------------------------------------

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Run one raw LLDB command through the adapter's REPL."""

        output = self._run_command(command, timeout_sec=timeout_sec)
        if isinstance(output, OperationError):
            return output
        return OperationSuccess(
            CommandExecutionInfo(command=command, output=output or "(no output)")
        )

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        """Evaluate a function call in the debuggee."""

        result = self._run_command(function_call, timeout_sec=timeout_sec)
        if isinstance(result, OperationError):
            return result
        return OperationSuccess(FunctionCallInfo(function_call=function_call, result=result))

    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Attach the adapter to a running process."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client

        response = client.request(
            "attach", {"pid": pid, "stopOnEntry": True}, timeout_sec=timeout_sec
        )
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)
        if client.wait_for_event("initialized", timeout_sec=timeout_sec) is not None:
            client.request("configurationDone", timeout_sec=timeout_sec)

        self.runtime.mark_attached(pid)
        self.runtime.mark_inferior_paused("attached", inferior_id=self.runtime.current_inferior_id)
        event = self._wait_for_stop_event(timeout_sec)
        if event is not None:
            self._apply_stop_event(event)
        return OperationSuccess(
            CommandExecutionInfo(command=f"attach {pid}", output=f"attached to process {pid}")
        )

    # -- inspection ---------------------------------------------------------

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """List the debuggee's threads."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client

        response = client.request("threads", timeout_sec=DEFAULT_TIMEOUT_SEC)
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)

        threads: list[ThreadRecord] = []
        for item in _items(_body(response).get("threads")):
            thread_id = _as_int(item.get("id"))
            if thread_id is None:
                continue
            threads.append(
                ThreadRecord(
                    id=str(thread_id),
                    name=_text(item.get("name")),
                    state="stopped" if self.runtime.execution_state == "paused" else None,
                )
            )
        current = self.runtime.current_thread_id
        return OperationSuccess(
            ThreadListInfo(
                threads=threads,
                current_thread_id=str(current) if current is not None else None,
                count=len(threads),
            )
        )

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """List the frames of one thread, newest first."""

        resolved_thread = thread_id if thread_id is not None else self.runtime.current_thread_id
        if resolved_thread is None:
            return OperationError(message="No thread selected", code="no_stack_frame")

        frames = self._frames(resolved_thread, start=0, levels=max_frames)
        if isinstance(frames, OperationError):
            return frames
        return OperationSuccess(
            BacktraceInfo(thread_id=resolved_thread, frames=frames, count=len(frames))
        )

    def get_frame_info(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[FrameInfo] | OperationError:
        """Describe one frame of the current (or given) thread."""

        level = self.runtime.current_frame if frame is None else frame
        resolved_thread = thread_id if thread_id is not None else self.runtime.current_thread_id
        if resolved_thread is None or level is None:
            return OperationError(message="No frame selected", code="no_stack_frame")

        frames = self._frames(resolved_thread, start=level, levels=1)
        if isinstance(frames, OperationError):
            return frames
        if not frames:
            return OperationError(message=f"Frame {level} does not exist", code="not_found")
        return OperationSuccess(FrameInfo(frame=frames[0]))

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Make one thread current and describe its top frame."""

        self.runtime.mark_thread_selected(thread_id)
        frames = self._frames(thread_id, start=0, levels=1)
        if isinstance(frames, OperationError):
            return frames
        self.runtime.mark_frame_selected(0)
        return OperationSuccess(
            ThreadSelectionInfo(
                thread_id=thread_id,
                new_thread_id=str(thread_id),
                frame=frames[0] if frames else None,
            )
        )

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Make one frame of the current thread current."""

        thread_id = self.runtime.current_thread_id
        if thread_id is None:
            return OperationError(message="No thread selected", code="no_stack_frame")

        frames = self._frames(thread_id, start=frame_number, levels=1)
        if isinstance(frames, OperationError):
            return frames
        if not frames:
            return OperationError(
                message=f"Frame {frame_number} does not exist in the current stack",
                code="not_found",
            )
        self.runtime.mark_frame_selected(frame_number)
        return OperationSuccess(
            FrameSelectionInfo(
                frame_number=frame_number,
                frame=frames[0],
                message=f"Selected frame {frame_number}",
            )
        )

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate one expression in the selected frame."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        frame_id = self._resolve_frame_id(thread_id, frame)
        if isinstance(frame_id, OperationError):
            return frame_id

        response = client.request(
            "evaluate",
            {"expression": expression, "frameId": frame_id, "context": "watch"},
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)
        return OperationSuccess(
            ExpressionValueInfo(expression=expression, value=_text(_body(response).get("result")))
        )

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """List the non-register scopes of one frame."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        frame_id = self._resolve_frame_id(thread_id, frame)
        if isinstance(frame_id, OperationError):
            return frame_id

        scopes = self._scopes(client, frame_id)
        if isinstance(scopes, OperationError):
            return scopes

        variables: list[VariableRecord] = []
        for scope in scopes:
            if scope.get("presentationHint") == "registers":
                continue
            reference = _as_int(scope.get("variablesReference"))
            if reference is None:
                continue
            variables.extend(_variable_records(self._variables(client, reference)))
        current = thread_id if thread_id is not None else self.runtime.current_thread_id
        return OperationSuccess(VariablesInfo(thread_id=current, frame=frame, variables=variables))

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
        """Read the adapter's register scope for one frame."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        frame_id = self._resolve_frame_id(thread_id, frame)
        if isinstance(frame_id, OperationError):
            return frame_id

        scopes = self._scopes(client, frame_id)
        if isinstance(scopes, OperationError):
            return scopes
        register_scope = next(
            (scope for scope in scopes if scope.get("presentationHint") == "registers"),
            None,
        )
        if register_scope is None:
            return OperationSuccess(RegistersInfo(registers=[]))

        reference = _as_int(register_scope.get("variablesReference"))
        if reference is None:
            return OperationSuccess(RegistersInfo(registers=[]))

        wanted = {name.lower() for name in register_names or []}
        records: list[RegisterRecord] = []
        for group in self._variables(client, reference):
            group_reference = _as_int(group.get("variablesReference"))
            leaves = (
                self._variables(client, group_reference) if group_reference is not None else [group]
            )
            for index, leaf in enumerate(leaves):
                name = _text(leaf.get("name")) or ""
                if wanted and name.lower() not in wanted:
                    continue
                if register_numbers and index not in register_numbers:
                    continue
                records.append(
                    RegisterRecord(
                        number=index,
                        name=name or None,
                        value=_text(leaf.get("value")),
                    )
                )
        if max_registers is not None:
            records = records[:max_registers]
        return OperationSuccess(RegistersInfo(registers=records))

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """Describe the one program this session debugs."""

        program = self.runtime.config.program if self.runtime.config is not None else None
        inferior = InferiorRecord(
            inferior_id=self.runtime.current_inferior_id or 1,
            is_current=True,
            display=program,
            description=program,
            execution_state=self.runtime.execution_state,
            stop_reason=self.runtime.stop_reason,
            stop_kind=self.runtime.stop_kind,
            exit_code=self.runtime.exit_code,
        )
        return OperationSuccess(
            InferiorListInfo(
                inferiors=[inferior],
                count=1,
                current_inferior_id=inferior.inferior_id,
            )
        )

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        """Accept the only inferior there is."""

        if inferior_id != (self.runtime.current_inferior_id or 1):
            return OperationError(
                message="An lldb session debugs one program",
                code="unsupported_operation",
            )
        return OperationSuccess(
            InferiorSelectionInfo(
                inferior_id=inferior_id,
                is_current=True,
                display=self.runtime.config.program if self.runtime.config is not None else None,
            )
        )

    # -- breakpoints --------------------------------------------------------

    def set_breakpoint(
        self,
        location: str,
        condition: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Set a file:line or function breakpoint through the adapter."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        if location.startswith("*"):
            return self._unsupported("breakpoints by address")

        picked = self._pick_breakpoint(client, location, condition=condition)
        if isinstance(picked, OperationError):
            return picked

        number = self._next_breakpoint_id
        self._next_breakpoint_id += 1
        record = picked.model_copy(
            update={"number": str(number), "condition": condition, "temporary": temporary}
        )
        self._breakpoints[number] = record
        return OperationSuccess(BreakpointInfo(breakpoint=record))

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo] | OperationError:
        """List the breakpoints this backend set.

        DAP has no inventory request, so the adapter's confirmations are the
        record; a breakpoint the debuggee removed through a command still appears
        here until it is deleted through this tool.
        """

        records = list(self._breakpoints.values())
        return OperationSuccess(BreakpointListInfo(breakpoints=records, count=len(records)))

    def get_breakpoint(self, number: int) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Return one breakpoint this backend set."""

        record = self._breakpoints.get(number)
        if record is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        return OperationSuccess(BreakpointInfo(breakpoint=record))

    def delete_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Forget one breakpoint and clear it in the adapter."""

        if number not in self._breakpoints:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        del self._breakpoints[number]
        return OperationSuccess(SessionMessage(message=f"Breakpoint {number} deleted"))

    def enable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Mark one breakpoint enabled."""

        return self._set_enabled(number, enabled=True)

    def disable_breakpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Mark one breakpoint disabled."""

        return self._set_enabled(number, enabled=False)

    def update_breakpoint(
        self,
        number: int,
        *,
        condition: str | None = None,
        clear_condition: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """Replace one breakpoint's condition through the adapter."""

        record = self._breakpoints.get(number)
        if record is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        client = self._require_client()
        if isinstance(client, OperationError):
            return client

        source = record.original_location or ""
        condition_value = None if clear_condition else condition
        picked = self._pick_breakpoint(client, source, condition=condition_value)
        if isinstance(picked, OperationError):
            return picked

        updated = picked.model_copy(
            update={"number": str(number), "condition": condition_value, "temporary": False}
        )
        self._breakpoints[number] = updated
        return OperationSuccess(BreakpointInfo(breakpoint=updated))

    # -- operations LLDB cannot perform -------------------------------------

    def add_inferior(
        self, *, executable: str | None = None, make_current: bool = False
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        """LLDB debugs one program per session."""

        return self._unsupported("inferior creation")

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        """LLDB debugs one program per session."""

        return self._unsupported("inferior removal")

    def set_follow_fork_mode(
        self, mode: FollowForkMode
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError:
        """DAP exposes no fork-following setting."""

        return self._unsupported("fork following")

    def set_detach_on_fork(
        self, enabled: bool
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError:
        """DAP exposes no fork-following setting."""

        return self._unsupported("fork following")

    def set_catchpoint(
        self,
        kind: CatchpointType,
        *,
        argument: str | None = None,
        temporary: bool = False,
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """DAP has no catchpoint request; use the command escape hatch."""

        return self._unsupported("catchpoints")

    def set_watchpoint(
        self, expression: str, *, access: WatchpointAccessType = "write"
    ) -> OperationSuccess[BreakpointInfo] | OperationError:
        """DAP has no watchpoint request the adapter advertises; use a command."""

        return self._unsupported("watchpoints")

    def delete_watchpoint(self, number: int) -> OperationSuccess[SessionMessage] | OperationError:
        """DAP has no watchpoint request the adapter advertises; use a command."""

        return self._unsupported("watchpoints")

    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """``readMemory`` is not advertised and kills the adapter; use a command."""

        return self._unsupported("memory reads")

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
        """``disassemble`` is not advertised; use the ``disassemble`` command."""

        return self._unsupported("disassembly")

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
        """The adapter's ``source`` request returns a disassembly, not text."""

        return self._unsupported("source context")

    # -- internals ----------------------------------------------------------

    def _reject_unsupported_start(
        self,
        *,
        core: str | None,
        endpoint: str | None,
        replay: str | None,
    ) -> OperationError | None:
        if core is not None:
            return OperationError(
                message="lldb-dap cannot load a core dump; start a gdb session for that",
                code="unsupported_operation",
            )
        if endpoint is not None:
            return OperationError(
                message="lldb sessions do not attach to an externally started GDB/MI stream",
                code="unsupported_operation",
            )
        if replay is not None:
            return OperationError(
                message="lldb sessions do not replay rr traces",
                code="unsupported_operation",
            )
        return None

    def _resolve_adapter(self, requested: str | None) -> str | OperationError:
        candidates = [requested, self.runtime.os_module.environ.get("LLDB_DAP_PATH"), "lldb-dap"]
        for candidate in candidates:
            if not candidate:
                continue
            resolved = shutil.which(candidate) or (candidate if "/" in candidate else None)
            if resolved:
                return resolved
        return OperationError(
            message="Could not find lldb-dap; install lldb or pass its path as gdb_path",
            code="unsupported_operation",
        )

    def _launch_arguments(
        self,
        *,
        program: str,
        args: list[str] | None,
        env: dict[str, str] | None,
        init_commands: list[str] | None,
        working_dir: str | None,
    ) -> dict[str, object]:
        launch: dict[str, object] = {"program": program, "stopOnEntry": True}
        if args:
            launch["args"] = list(args)
        if env:
            launch["env"] = dict(env)
        if working_dir:
            launch["cwd"] = working_dir
        if init_commands:
            launch["initCommands"] = list(init_commands)
        return launch

    def _launch(self, client: DapClient, launch: dict[str, object]) -> OperationError | None:
        response = client.request("launch", launch, timeout_sec=DEFAULT_TIMEOUT_SEC)
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=f"lldb-dap could not launch: {failure}")

        if client.wait_for_event("initialized", timeout_sec=DEFAULT_TIMEOUT_SEC) is None:
            return OperationError(message="lldb-dap never reported the target as initialized")

        response = client.request("configurationDone", timeout_sec=DEFAULT_TIMEOUT_SEC)
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=f"lldb-dap rejected configurationDone: {failure}")

        event = self._wait_for_stop_event(DEFAULT_TIMEOUT_SEC)
        if event is not None:
            self._apply_stop_event(event)
        return None

    def _start_info(self, *, message: str, program: str | None) -> SessionStartInfo:
        return SessionStartInfo(
            message=message,
            program=program,
            target_loaded=program is not None,
            execution_state=self.runtime.execution_state,
            stop_reason=self.runtime.stop_reason,
            stop_kind=self.runtime.stop_kind,
            exit_code=self.runtime.exit_code,
        )

    def _status_snapshot(self) -> SessionStatusSnapshot:
        return SessionStatusSnapshot(
            is_running=self.runtime.is_running,
            target_loaded=self.runtime.target_loaded,
            has_controller=self.runtime.has_controller,
            execution_state=self.runtime.execution_state,
            stop_reason=self.runtime.stop_reason,
            stop_kind=self.runtime.stop_kind,
            exit_code=self.runtime.exit_code,
            current_inferior_id=self.runtime.current_inferior_id,
            inferior_count=self.runtime.inferior_count,
            inferior_states=self.runtime.inferiors_state_summary() or None,
            follow_fork_mode=self.runtime.follow_fork_mode,
            detach_on_fork=self.runtime.detach_on_fork,
        )

    def _wait_info(
        self,
        *,
        message: str,
        matched: bool,
        timed_out: bool,
        source: str,
        reason_filter: list[str] | None,
    ) -> WaitForStopInfo:
        return WaitForStopInfo(
            message=message,
            matched=matched,
            timed_out=timed_out,
            source=source,
            execution_state=self.runtime.execution_state,
            stop_reason=self.runtime.stop_reason,
            stop_kind=self.runtime.stop_kind,
            reason_filter=reason_filter,
            last_stop_event=self.runtime.last_stop_event,
        )

    def _shutdown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.request("disconnect", {"terminateDebuggee": True}, timeout_sec=5)
            except DapTransportError:
                logger.debug("Adapter was already gone while disconnecting")
            finally:
                client.close()
        self.runtime.controller = None

    def _require_client(self) -> DapClient | OperationError:
        client = self._client
        if client is None:
            return OperationError(message="No active LLDB session")
        if client.is_dead:
            self.runtime.mark_transport_terminated("lldb-dap exited")
            return OperationError(message="lldb-dap exited - session is no longer active")
        return client

    def _resume(
        self,
        command: str,
        *,
        timeout_sec: int,
        wait_for_stop: bool,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        if self.runtime.execution_state == "exited":
            return OperationError(message="The program has exited")

        response = client.request(command, timeout_sec=timeout_sec)
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)

        self.runtime.mark_inferior_running(inferior_id=self.runtime.current_inferior_id)
        if not wait_for_stop:
            return OperationSuccess(CommandExecutionInfo(command=command, output="resumed"))

        event = self._wait_for_stop_event(timeout_sec)
        if event is None:
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    output=f"{command} acknowledged but no stop event was reported",
                ),
                warnings=("No stop event was reported; use wait_for_stop to observe it.",),
            )

        self._apply_stop_event(event)
        return OperationSuccess(
            CommandExecutionInfo(command=command, output=f"stopped: {self.runtime.stop_reason}")
        )

    def _wait_for_stop_event(self, timeout_sec: float) -> dict[str, Any] | None:
        deadline = self._time.time() + timeout_sec
        while True:
            remaining = deadline - self._time.time()
            if remaining <= 0:
                return None
            client = self._client
            if client is None:
                return None
            event = client.next_event(timeout_sec=remaining)
            if event is None:
                return None
            if event.get("event") in {"stopped", "exited", "terminated"}:
                return dict(event)
            self._apply_event(event)

    def _drain_events(self, *, timeout_sec: float) -> dict[str, Any] | None:
        """Apply every queued event; return the last stop event seen, if any."""

        client = self._client
        if client is None:
            return None
        stop_event: dict[str, Any] | None = None
        remaining = timeout_sec
        while True:
            event = client.next_event(timeout_sec=remaining)
            if event is None:
                return stop_event
            remaining = 0.0
            if event.get("event") in {"stopped", "exited", "terminated"}:
                self._apply_stop_event(dict(event))
                stop_event = dict(event)
                continue
            self._apply_event(event)

    def _apply_stop_event(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        body = _body(event)
        inferior_id = self.runtime.current_inferior_id

        if name == "exited":
            self.runtime.mark_inferior_exited(
                "exited", _as_int(body.get("exitCode")), inferior_id=inferior_id
            )
            return
        if name == "terminated":
            if self.runtime.execution_state != "exited":
                self.runtime.mark_inferior_exited("terminated", None, inferior_id=inferior_id)
            return

        reason = _text(body.get("reason")) or "stopped"
        thread_id = _as_int(body.get("threadId"))
        if thread_id is not None:
            self.runtime.mark_thread_selected(thread_id)
        frames = self._top_frame(thread_id)
        self.runtime.mark_frame_selected(0 if frames is not None else None)
        self.runtime.mark_inferior_paused(reason, inferior_id=inferior_id)
        self.runtime.record_stop_event(
            StopEvent(
                execution_state="paused",
                reason=reason,
                inferior_id=inferior_id,
                thread_id=thread_id,
                frame=frames,
                breakpoint_number=_hit_breakpoint(body),
                timestamp=self._time.time(),
            )
        )

    def _apply_event(self, event: Mapping[str, Any]) -> None:
        name = event.get("event")
        body = _body(event)
        if name == "output":
            logger.debug("lldb-dap output: %s", _text(body.get("output")))
        elif name == "continued":
            self.runtime.mark_inferior_running(inferior_id=self.runtime.current_inferior_id)

    def _top_frame(self, thread_id: int | None) -> FrameRecord | None:
        if thread_id is None:
            return None
        frames = self._frames(thread_id, start=0, levels=1)
        if isinstance(frames, OperationError) or not frames:
            return None
        return frames[0]

    def _frames(
        self,
        thread_id: int,
        *,
        start: int,
        levels: int,
    ) -> list[FrameRecord] | OperationError:
        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        response = client.request(
            "stackTrace",
            {"threadId": thread_id, "startFrame": start, "levels": levels},
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)
        records: list[FrameRecord] = []
        for offset, item in enumerate(_items(_body(response).get("stackFrames"))):
            records.append(_frame_record(item, level=start + offset))
        return records

    def _resolve_frame_id(self, thread_id: int | None, frame: int | None) -> int | OperationError:
        """Resolve a frame number to the adapter's frame id for this stop."""

        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        resolved_thread = thread_id if thread_id is not None else self.runtime.current_thread_id
        level = self.runtime.current_frame if frame is None else frame
        if resolved_thread is None or level is None:
            return OperationError(message="No frame selected", code="no_stack_frame")

        response = client.request(
            "stackTrace",
            {"threadId": resolved_thread, "startFrame": level, "levels": 1},
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)
        frames = _items(_body(response).get("stackFrames"))
        if not frames:
            return OperationError(message=f"Frame {level} does not exist", code="not_found")
        frame_id = _as_int(frames[0].get("id"))
        if frame_id is None:
            return OperationError(message="lldb-dap returned a frame without an id")
        return frame_id

    def _scopes(self, client: DapClient, frame_id: int) -> list[dict[str, Any]] | OperationError:
        response = client.request("scopes", {"frameId": frame_id}, timeout_sec=DEFAULT_TIMEOUT_SEC)
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)
        return _items(_body(response).get("scopes"))

    def _variables(self, client: DapClient, reference: int) -> list[dict[str, Any]]:
        if reference <= 0:
            return []
        try:
            response = client.request(
                "variables",
                {"variablesReference": reference},
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
        except DapTransportError:
            return []
        if dap_error_message(response) is not None:
            return []
        return _items(_body(response).get("variables"))

    def _run_command(self, command: str, *, timeout_sec: int) -> str | OperationError:
        client = self._require_client()
        if isinstance(client, OperationError):
            return client
        response = client.request(
            "evaluate",
            {"expression": command, "context": "repl"},
            timeout_sec=timeout_sec,
        )
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)
        return _text(_body(response).get("result")) or ""

    def _pick_breakpoint(
        self,
        client: DapClient,
        location: str,
        *,
        condition: str | None,
    ) -> BreakpointRecord | OperationError:
        path, line = _split_location(location)
        if path is None:
            response = client.request(
                "setFunctionBreakpoints",
                {
                    "breakpoints": [
                        {"name": location, **({"condition": condition} if condition else {})}
                    ]
                },
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
        else:
            response = client.request(
                "setBreakpoints",
                {
                    "source": {"path": path},
                    "breakpoints": [
                        {"line": line, **({"condition": condition} if condition else {})}
                    ],
                },
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
        failure = dap_error_message(response)
        if failure is not None:
            return OperationError(message=failure)

        confirmed = _items(_body(response).get("breakpoints"))
        if not confirmed:
            return OperationError(message=f"lldb confirmed no breakpoint at {location}")
        first = confirmed[0]
        if first.get("verified") is not True:
            return OperationError(
                message=f"lldb did not verify a breakpoint at {location}",
                code="not_found",
            )
        source = first.get("source")
        source_path = _text(source.get("path")) if isinstance(source, Mapping) else None
        return BreakpointRecord(
            number=None,
            type="breakpoint",
            enabled=True,
            verified=True,
            condition=condition,
            file=source_path,
            fullname=source_path,
            line=_as_int(first.get("line")),
            original_location=location,
        )

    def _set_enabled(
        self, number: int, *, enabled: bool
    ) -> OperationSuccess[SessionMessage] | OperationError:
        record = self._breakpoints.get(number)
        if record is None:
            return OperationError(message=f"Breakpoint {number} not found", code="not_found")
        # DAP re-sends the whole breakpoint list per source, so a disabled
        # breakpoint is remembered rather than pushed: sending an empty list would
        # delete the debugger's breakpoint instead of disabling it.
        self._breakpoints[number] = record.model_copy(
            update={"enabled": enabled, "verified": enabled or record.verified}
        )
        return OperationSuccess(
            SessionMessage(message=f"Breakpoint {number} {'enabled' if enabled else 'disabled'}")
        )

    @staticmethod
    def _unsupported(operation: str) -> OperationError:
        return OperationError(
            message=f"lldb does not support {operation}",
            code="unsupported_operation",
        )


def _body(message: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return one DAP response or event body."""

    body = message.get("body")
    return body if isinstance(body, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    """Return the mapping entries of one DAP list field."""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _hit_breakpoint(body: Mapping[str, Any]) -> str | None:
    """Return the DAP breakpoint id a stop hit, as the published string number."""

    ids = body.get("hitBreakpointIds")
    if not isinstance(ids, list) or not ids:
        return None
    first = ids[0]
    return str(first) if isinstance(first, int) else None


def _frame_record(item: dict[str, Any], *, level: int) -> FrameRecord:
    """Build one frame record from a DAP stack frame."""

    source = item.get("source")
    path = _text(source.get("path")) if isinstance(source, Mapping) else None
    return FrameRecord(
        level=level,
        addr=_text(item.get("instructionPointerReference")),
        func=_text(item.get("name")),
        file=path,
        fullname=path,
        id=_as_int(item.get("id")),
        line=_as_int(item.get("line")),
        column=_as_int(item.get("column")),
    )


def _variable_records(items: list[dict[str, Any]]) -> list[VariableRecord]:
    """Build variable records from one DAP ``variables`` page."""

    records: list[VariableRecord] = []
    for item in items:
        records.append(
            VariableRecord(
                name=_text(item.get("name")),
                type=_text(item.get("type")),
                value=_text(item.get("value")),
                variablesReference=_as_int(item.get("variablesReference")),
            )
        )
    return records


def _split_location(location: str) -> tuple[str | None, int]:
    """Split ``file:line`` from a function name; a name has no line part."""

    path, separator, line = location.rpartition(":")
    if separator and path and line.isdigit():
        return path, int(line)
    return None, 0
