"""Execution helpers for a composed SessionService."""

from __future__ import annotations

import logging
import signal
from typing import Optional, cast

from ..domain import (
    CommandTranscriptEntry,
    CommandExecutionInfo,
    FunctionCallInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
    StructuredPayload,
)
from ..transport import (
    build_exec_arguments_command,
    parse_mi_responses,
    wrap_cli_command,
)
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_TIMEOUT_SEC, INTERRUPT_RESPONSE_TIMEOUT_SEC
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)


class SessionExecutionService:
    """Execution and command-control operations."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a GDB command and return the parsed response."""

        return self._command_runner.execute_command_result(command, timeout_sec)

    def run(
        self,
        args: Optional[list[str]] = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
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

        return self._command_runner.execute_command_result("-exec-run", timeout_sec=timeout_sec)

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

        self._runtime.target_loaded = True
        if self._runtime.execution_state == "unknown":
            self._runtime.mark_inferior_paused("attached")

        return result

    def continue_execution(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Continue execution of the program."""
        return self._command_runner.execute_command_result(
            "-exec-continue", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

    def step(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step into the next source line."""
        return self._command_runner.execute_command_result(
            "-exec-step", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

    def next(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step over the next source line."""
        return self._command_runner.execute_command_result(
            "-exec-next", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        """Interrupt (pause) a running program."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self._command_runner.is_gdb_alive():
            message = "GDB process has exited - session is no longer active"
            self._command_runner.handle_dead_transport(message)
            return OperationError(message=message)

        controller = self._runtime.controller
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
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(
                message=str(result["error"]),
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
                    timestamp=self._runtime.time_module.time(),
                )
            )
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
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
