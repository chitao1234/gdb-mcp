"""Execution helpers for a composed SessionService."""

from __future__ import annotations

import logging
import signal
from typing import cast

from ..domain import (
    CommandExecutionInfo,
    CommandTranscriptEntry,
    DetachOnForkInfo,
    FinishInfo,
    FollowForkMode,
    FollowForkModeInfo,
    FrameRecord,
    FunctionCallInfo,
    InferiorAddInfo,
    InferiorListInfo,
    InferiorRecord,
    InferiorRemoveInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    StructuredPayload,
    WaitForStopInfo,
)
from ..transport import (
    MiClient,
    build_exec_arguments_command,
    extract_mi_result_payload,
    parse_mi_responses,
    quote_mi_string,
    wrap_cli_command,
)
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_TIMEOUT_SEC, INTERRUPT_RESPONSE_TIMEOUT_SEC
from .inferiors import inferior_ids, parse_inferiors_output
from .result_utils import command_result_payload
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)


def _augment_call_error(message: str, function_call: str) -> str:
    """Add a cast hint when GDB cannot infer the called function's return type."""

    if "unknown return type" not in message.lower():
        return message
    if function_call.lstrip().startswith("("):
        return message
    return f"{message} (cast the call, e.g. 'call (int){function_call}')"


def _exec_command(base: str, *, reverse: bool) -> str:
    """Return one MI execution command, optionally in the reverse direction."""

    return f"{base} --reverse" if reverse else base


def _is_undefined_mi_command(message: str) -> bool:
    """Return whether an error says this GDB has no such MI command.

    GDB 7.2 added `-add-inferior`/`-remove-inferior`; older releases answer with
    ``Undefined MI command: <name>``, which is the signal to use the CLI forms.
    """

    return "undefined mi command" in message.lower()


class SessionExecutionService:
    """Execution and command-control operations."""

    def __init__(
        self,
        runtime: SessionRuntime,
        command_runner: SessionCommandRunner,
        transport: MiClient,
    ):
        self._runtime = runtime
        self._command_runner = command_runner
        self._transport = transport

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a GDB command and return the parsed response."""

        return self._command_runner.execute_command_result(command, timeout_sec)

    def run(
        self,
        args: list[str] | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Run the program."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if args:
            result = self._command_runner.execute_command_result(
                build_exec_arguments_command(args), timeout_sec=timeout_sec
            )
            if isinstance(result, OperationError):
                return result

        return self._command_runner.execute_command_result(
            "-exec-run",
            timeout_sec=timeout_sec,
            allow_running_timeout=not wait_for_stop,
        )

    def add_inferior(
        self,
        *,
        executable: str | None = None,
        make_current: bool = False,
    ) -> OperationSuccess[InferiorAddInfo] | OperationError:
        """Create a new inferior and return the refreshed inventory summary."""

        inferior_id = self._create_inferior()
        if isinstance(inferior_id, OperationError):
            return inferior_id

        previous_inferior_id = self._runtime.current_inferior_id
        self._runtime.ensure_inferior(inferior_id)

        if executable is not None:
            selection_result = self._command_runner.execute_command_result(
                f"inferior {inferior_id}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if isinstance(selection_result, OperationError):
                return selection_result

            exec_file_result = self._command_runner.execute_command_result(
                f"exec-file {quote_mi_string(executable)}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if isinstance(exec_file_result, OperationError):
                return exec_file_result

        if make_current:
            if executable is None and previous_inferior_id != inferior_id:
                selection_result = self._command_runner.execute_command_result(
                    f"inferior {inferior_id}",
                    timeout_sec=DEFAULT_TIMEOUT_SEC,
                )
                if isinstance(selection_result, OperationError):
                    return selection_result
        elif (
            executable is not None
            and previous_inferior_id is not None
            and previous_inferior_id != inferior_id
        ):
            restore_result = self._command_runner.execute_command_result(
                f"inferior {previous_inferior_id}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if isinstance(restore_result, OperationError):
                return restore_result

        inventory_result = self._refresh_inferior_inventory()
        if isinstance(inventory_result, OperationError):
            return inventory_result

        record = self._inferior_record(inventory_result.value, inferior_id)
        if record is None:
            return OperationSuccess(
                InferiorAddInfo(
                    inferior_id=inferior_id,
                    is_current=make_current,
                    executable=executable,
                    current_inferior_id=inventory_result.value.current_inferior_id,
                    inferior_count=inventory_result.value.count,
                    message=f"Inferior {inferior_id} added",
                ),
                warnings=(
                    f"Inferior {inferior_id} was added, but it was missing from the refreshed inventory.",
                ),
            )

        return OperationSuccess(
            InferiorAddInfo(
                inferior_id=inferior_id,
                is_current=bool(record.is_current),
                display=record.display,
                description=record.description,
                connection=record.connection,
                executable=record.executable,
                current_inferior_id=inventory_result.value.current_inferior_id,
                inferior_count=inventory_result.value.count,
                message=f"Inferior {inferior_id} added",
            )
        )

    def _create_inferior(self) -> int | OperationError:
        """Create one inferior and return its id.

        Uses `-add-inferior` when this GDB has it (7.2+) and falls back to the
        CLI `add-inferior` on GDB 7.1, which only has the command-line form.
        """

        if self._runtime.inferior_mi_supported is not False:
            result = self._command_runner.execute_command_result(
                "-add-inferior",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if not isinstance(result, OperationError):
                self._runtime.inferior_mi_supported = True
                payload = extract_mi_result_payload(command_result_payload(result))
                inferior_token = payload.get("inferior") if isinstance(payload, dict) else None
                inferior_id = self._command_runner.parse_inferior_id_from_thread_group(
                    inferior_token
                )
                if inferior_id is None:
                    return OperationError(
                        message="GDB returned no inferior identifier for -add-inferior"
                    )
                return inferior_id
            if not _is_undefined_mi_command(result.message):
                return result
            self._runtime.inferior_mi_supported = False

        return self._create_inferior_legacy()

    def _create_inferior_legacy(self) -> int | OperationError:
        """Create an inferior through the CLI `add-inferior` (GDB 7.1).

        The CLI does not report the new inferior's number, so diff the inventory
        around the command to find it.
        """

        known_ids = set(self._runtime.inferior_states)
        result = self._command_runner.execute_command_result(
            "add-inferior",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        inventory_result = self._refresh_inferior_inventory()
        if isinstance(inventory_result, OperationError):
            return inventory_result

        created = [
            inferior_id
            for inferior_id in inferior_ids(inventory_result.value)
            if inferior_id not in known_ids
        ]
        if not created:
            return OperationError(message="GDB added no new inferior")
        return max(created)

    def remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        """Remove one inferior and return the refreshed inventory summary."""

        if self._runtime.inferior_mi_supported is not False:
            result = self._command_runner.execute_command_result(
                f"-remove-inferior i{inferior_id}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if not isinstance(result, OperationError):
                self._runtime.inferior_mi_supported = True
                return self._finish_remove_inferior(inferior_id)
            if not _is_undefined_mi_command(result.message):
                return result
            self._runtime.inferior_mi_supported = False

        legacy_result = self._command_runner.execute_command_result(
            f"remove-inferior {inferior_id}",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(legacy_result, OperationError):
            return legacy_result
        return self._finish_remove_inferior(inferior_id)

    def _finish_remove_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorRemoveInfo] | OperationError:
        """Refresh the inventory and report one removed inferior."""

        inventory_result = self._refresh_inferior_inventory()
        if isinstance(inventory_result, OperationError):
            return inventory_result

        return OperationSuccess(
            InferiorRemoveInfo(
                inferior_id=inferior_id,
                current_inferior_id=inventory_result.value.current_inferior_id,
                inferior_count=inventory_result.value.count,
                message=f"Inferior {inferior_id} removed",
            )
        )

    def attach_process(
        self, pid: int, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Attach GDB to a running process."""

        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        result = self._command_runner.execute_command_result(
            f"attach {pid}", timeout_sec=timeout_sec
        )
        if isinstance(result, OperationError):
            return result

        return result

    def set_follow_fork_mode(
        self, mode: FollowForkMode
    ) -> OperationSuccess[FollowForkModeInfo] | OperationError:
        """Set whether GDB follows the parent or child after fork/vfork."""

        result = self._command_runner.execute_command_result(
            f"set follow-fork-mode {mode}",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        self._runtime.mark_follow_fork_mode(mode)
        return OperationSuccess(
            FollowForkModeInfo(
                mode=mode,
                message=f"follow-fork-mode set to {mode}",
            )
        )

    def set_detach_on_fork(
        self, enabled: bool
    ) -> OperationSuccess[DetachOnForkInfo] | OperationError:
        """Set whether GDB detaches from the non-followed fork."""

        detach_on_fork = "on" if enabled else "off"
        result = self._command_runner.execute_command_result(
            f"set detach-on-fork {detach_on_fork}",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        self._runtime.mark_detach_on_fork(enabled)
        return OperationSuccess(
            DetachOnForkInfo(
                enabled=enabled,
                message=f"detach-on-fork set to {detach_on_fork}",
            )
        )

    def continue_execution(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Continue execution of the program, forward or backward."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")
        if self._runtime.execution_state == "running":
            return OperationError(
                message=(
                    "Inferior is already running. Use gdb_wait_for_stop to wait for the next "
                    "stop event or gdb_interrupt to force a pause."
                )
            )
        return self._command_runner.execute_command_result(
            _exec_command("-exec-continue", reverse=reverse),
            timeout_sec=timeout_sec,
            allow_running_timeout=not wait_for_stop,
        )

    def wait_for_stop(
        self,
        *,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        stop_reasons: tuple[str, ...] = (),
    ) -> OperationSuccess[WaitForStopInfo] | OperationError:
        """Wait for the inferior to stop, optionally matching one of several reasons."""

        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self._command_runner.is_gdb_alive():
            message = "GDB process has exited - session is no longer active"
            self._command_runner.handle_dead_transport(message)
            return OperationError(message=message)

        reason_filter = list(stop_reasons) or None
        if self._runtime.execution_state != "running":
            matched = not stop_reasons or self._runtime.stop_reason in stop_reasons
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command="wait_for_stop",
                    status="success",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationSuccess(
                WaitForStopInfo(
                    message=self._wait_result_message(
                        matched=matched,
                        timed_out=False,
                        stop_reason=self._runtime.stop_reason,
                        source="existing",
                    ),
                    matched=matched,
                    source="existing",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    reason_filter=reason_filter,
                    last_stop_event=self._runtime.last_stop_event,
                )
            )

        result = self._command_runner.wait_for_stop(timeout_sec)
        if "error" in result:
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command="wait_for_stop",
                    status="error",
                    fatal=bool(result.get("fatal", False)),
                    error=str(result["error"]),
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(message=str(result["error"]))

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)
        self._command_runner.update_runtime_after_command("wait_for_stop", parsed)

        if result.get("timed_out"):
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command="wait_for_stop",
                    status="timeout",
                    timed_out=True,
                    error=f"Timeout waiting for stop after {timeout_sec}s",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationSuccess(
                WaitForStopInfo(
                    message=self._wait_result_message(
                        matched=False,
                        timed_out=True,
                        stop_reason=self._runtime.stop_reason,
                        source="waited",
                    ),
                    matched=False,
                    timed_out=True,
                    source="waited",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    reason_filter=reason_filter,
                    last_stop_event=self._runtime.last_stop_event,
                )
            )

        matched = not stop_reasons or self._runtime.stop_reason in stop_reasons
        self._runtime.record_command_transcript(
            CommandTranscriptEntry(
                command="wait_for_stop",
                status="success",
                execution_state=self._runtime.execution_state,
                stop_reason=self._runtime.stop_reason,
                stop_kind=self._runtime.stop_kind,
                timestamp=self._runtime.time_module.time(),
            )
        )
        return OperationSuccess(
            WaitForStopInfo(
                message=self._wait_result_message(
                    matched=matched,
                    timed_out=False,
                    stop_reason=self._runtime.stop_reason,
                    source="waited",
                ),
                matched=matched,
                source="waited",
                execution_state=self._runtime.execution_state,
                stop_reason=self._runtime.stop_reason,
                reason_filter=reason_filter,
                last_stop_event=self._runtime.last_stop_event,
            )
        )

    def step(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step into the next source line, forward or backward."""
        return self._command_runner.execute_command_result(
            _exec_command("-exec-step", reverse=reverse),
            timeout_sec=timeout_sec,
            allow_running_timeout=not wait_for_stop,
        )

    def next(
        self,
        *,
        wait_for_stop: bool = True,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        reverse: bool = False,
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step over the next source line, forward or backward."""
        return self._command_runner.execute_command_result(
            _exec_command("-exec-next", reverse=reverse),
            timeout_sec=timeout_sec,
            allow_running_timeout=not wait_for_stop,
        )

    def finish(
        self,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        wait_for_stop: bool = True,
        reverse: bool = False,
    ) -> OperationSuccess[FinishInfo] | OperationError:
        """Finish the current frame and stop in the caller, forward or backward."""

        result = self._command_runner.execute_command_result(
            _exec_command("-exec-finish", reverse=reverse),
            timeout_sec=timeout_sec,
            allow_running_timeout=not wait_for_stop,
        )
        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        payload_mapping = raw_payload if isinstance(raw_payload, dict) else {}
        if not payload_mapping and self._runtime.last_stop_event is not None:
            payload_mapping = self._runtime.last_stop_event.details
        frame_payload = payload_mapping.get("frame")
        frame = cast(FrameRecord | None, frame_payload) if isinstance(frame_payload, dict) else None
        if frame is None and self._runtime.last_stop_event is not None:
            frame = self._runtime.last_stop_event.frame
        return_value = payload_mapping.get("return-value")
        result_var = payload_mapping.get("gdb-result-var")

        return OperationSuccess(
            FinishInfo(
                message="Frame finished",
                return_value=return_value if isinstance(return_value, str) else None,
                result_var=result_var if isinstance(result_var, str) else None,
                frame=frame,
                execution_state=self._runtime.execution_state,
                stop_reason=self._runtime.stop_reason,
                stop_kind=self._runtime.stop_kind,
                last_stop_event=self._runtime.last_stop_event,
            )
        )

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        """Interrupt (pause) a running program."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self._command_runner.is_gdb_alive():
            message = "GDB process has exited - session is no longer active"
            self._command_runner.handle_dead_transport(message)
            return OperationError(message=message)

        controller = self._transport.controller
        if controller is None:
            return OperationError(message="No active GDB session")

        gdb_process = controller.gdb_process
        if gdb_process is None:
            return OperationError(message="No GDB process running")

        result = self._command_runner.interrupt_and_wait_for_stop(
            send_interrupt=lambda: self._runtime.os_module.kill(gdb_process.pid, signal.SIGINT),
            timeout_sec=INTERRUPT_RESPONSE_TIMEOUT_SEC,
        )

        if "error" in result:
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command="interrupt",
                    sent_command="SIGINT",
                    status="error",
                    fatal=bool(result.get("fatal", False)),
                    error=str(result["error"]),
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(message=str(result["error"]))

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)
        self._command_runner.update_runtime_after_command("interrupt", parsed)
        parsed_result = cast(StructuredPayload, parsed.to_dict())

        if result.get("timed_out"):
            self._runtime.mark_inferior_running()
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command="interrupt",
                    sent_command="SIGINT",
                    status="timeout",
                    timed_out=True,
                    error="Interrupt sent but no stopped notification received",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationSuccess(
                MessageResult(
                    message="Interrupt sent but no stopped notification received",
                    result=parsed_result,
                    status="warning",
                )
            )

        stop_reason = self._extract_stop_reason(parsed_result)
        self._runtime.mark_inferior_paused(stop_reason)
        self._runtime.record_command_transcript(
            CommandTranscriptEntry(
                command="interrupt",
                sent_command="SIGINT",
                status="success",
                result_class=parsed.result_class,
                execution_state=self._runtime.execution_state,
                stop_reason=self._runtime.stop_reason,
                stop_kind=self._runtime.stop_kind,
                timestamp=self._runtime.time_module.time(),
            )
        )
        return OperationSuccess(
            MessageResult(message="Program interrupted (paused)", result=parsed_result)
        )

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        """Call a function in the target process."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self._command_runner.is_gdb_alive():
            message = "GDB process has exited - session is no longer active"
            self._command_runner.handle_dead_transport(message)
            return OperationError(message=message)

        command = f"call {function_call}"
        mi_command = wrap_cli_command(command)

        result = self._command_runner.send_command_and_wait_for_prompt(mi_command, timeout_sec)

        if "error" in result:
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command=command,
                    sent_command=mi_command,
                    status="error",
                    fatal=bool(result.get("fatal", False)),
                    error=str(result["error"]),
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(
                message=_augment_call_error(str(result["error"]), function_call),
                details={"function_call": function_call},
            )

        if result.get("timed_out"):
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command=command,
                    sent_command=mi_command,
                    status="timeout",
                    timed_out=True,
                    error=f"Timeout waiting for call to complete after {timeout_sec}s",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(
                message=f"Timeout waiting for call to complete after {timeout_sec}s",
                details={"function_call": function_call},
            )

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)
        self._command_runner.update_runtime_after_command(command, parsed)
        if parsed.is_error_result():
            self._runtime.record_command_transcript(
                CommandTranscriptEntry(
                    command=command,
                    sent_command=mi_command,
                    status="error",
                    result_class=parsed.result_class,
                    error=parsed.error_message() or "GDB returned an error",
                    execution_state=self._runtime.execution_state,
                    stop_reason=self._runtime.stop_reason,
                    stop_kind=self._runtime.stop_kind,
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(
                message=_augment_call_error(
                    parsed.error_message() or "GDB returned an error",
                    function_call,
                ),
                details={"function_call": function_call},
            )
        console_output = "".join(item for item in parsed.console if isinstance(item, str))
        self._runtime.record_command_transcript(
            CommandTranscriptEntry(
                command=command,
                sent_command=mi_command,
                status="success",
                result_class=parsed.result_class,
                execution_state=self._runtime.execution_state,
                stop_reason=self._runtime.stop_reason,
                stop_kind=self._runtime.stop_kind,
                timestamp=self._runtime.time_module.time(),
            )
        )

        return OperationSuccess(
            FunctionCallInfo(
                function_call=function_call,
                result=console_output.strip() if console_output else "(no return value)",
            )
        )

    @staticmethod
    def _extract_stop_reason(parsed_result: StructuredPayload) -> str | None:
        """Extract the first stop reason from a parsed MI result payload."""

        notify_records = parsed_result.get("notify")
        if not isinstance(notify_records, list):
            return None

        for notify in notify_records:
            if not isinstance(notify, dict) or notify.get("message") != "stopped":
                continue
            payload = notify.get("payload")
            if isinstance(payload, dict):
                reason = payload.get("reason")
                if isinstance(reason, str):
                    return reason
        return None

    @staticmethod
    def _wait_result_message(
        *,
        matched: bool,
        timed_out: bool,
        stop_reason: str | None,
        source: str,
    ) -> str:
        """Build a human-readable wait result message."""

        if timed_out:
            return "Timed out waiting for the inferior to stop"
        if matched:
            if stop_reason:
                return (
                    f"Inferior stopped for reason {stop_reason}"
                    if source == "waited"
                    else f"Inferior is already stopped for reason {stop_reason}"
                )
            return "Inferior stopped"
        if stop_reason:
            return f"Inferior stopped for reason {stop_reason}, but it did not match the requested filter"
        return "Inferior is not running and no matching stop reason is available"

    def _refresh_inferior_inventory(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """Refresh runtime inferior metadata from `info inferiors` output."""

        result = self._command_runner.execute_command_result(
            "info inferiors",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        payload = self._parse_inferiors_output(result.value.output or "")
        self._runtime.update_inferior_inventory(
            current_inferior_id=payload.current_inferior_id,
            count=payload.count,
            inferior_ids=inferior_ids(payload),
        )
        return OperationSuccess(payload)

    def _parse_inferiors_output(self, output: str) -> InferiorListInfo:
        """Parse `info inferiors` CLI output into a structured inventory snapshot."""

        return parse_inferiors_output(
            output,
            current_inferior_id=self._runtime.current_inferior_id,
        )

    @staticmethod
    def _inferior_record(payload: InferiorListInfo, inferior_id: int) -> InferiorRecord | None:
        """Return one inferior record by numeric ID."""

        return next(
            (record for record in payload.inferiors if record.inferior_id == inferior_id),
            None,
        )
