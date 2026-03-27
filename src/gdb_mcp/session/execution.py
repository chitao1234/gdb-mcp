"""Execution helpers for a composed SessionService."""

from __future__ import annotations

import logging
import signal
from typing import Optional

from ..domain import (
    CommandExecutionInfo,
    FunctionCallInfo,
    MessageResult,
    OperationError,
    OperationSuccess,
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
        self, args: Optional[list[str]] = None
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Run the program."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if args:
            result = self._command_runner.execute_command_result(
                build_exec_arguments_command(args), timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(result, OperationError):
                return result

        return self._command_runner.execute_command_result("-exec-run", timeout_sec=DEFAULT_TIMEOUT_SEC)

    def continue_execution(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Continue execution of the program."""
        return self._command_runner.execute_command_result("-exec-continue", timeout_sec=DEFAULT_TIMEOUT_SEC)

    def step(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step into the next source line."""
        return self._command_runner.execute_command_result("-exec-step", timeout_sec=DEFAULT_TIMEOUT_SEC)

    def next(self) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Step over the next source line."""
        return self._command_runner.execute_command_result("-exec-next", timeout_sec=DEFAULT_TIMEOUT_SEC)

    def interrupt(self) -> OperationSuccess[MessageResult] | OperationError:
        """Interrupt (pause) a running program."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        controller = self._runtime.controller
        if not getattr(controller, "gdb_process", None):
            return OperationError(message="No GDB process running")

        result = self._command_runner.interrupt_and_wait_for_stop(
            send_interrupt=lambda: self._runtime.os_module.kill(
                controller.gdb_process.pid, signal.SIGINT
            ),
            timeout_sec=INTERRUPT_RESPONSE_TIMEOUT_SEC,
        )

        if "error" in result:
            return OperationError(message=str(result["error"]))

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed_result = parse_mi_responses(command_responses).to_dict()

        if result.get("timed_out"):
            return OperationSuccess(
                MessageResult(
                    message="Interrupt sent but no stopped notification received",
                    result=parsed_result,
                    status="warning",
                )
            )

        return OperationSuccess(MessageResult(message="Program interrupted (paused)", result=parsed_result))

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        """Call a function in the target process."""
        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self._command_runner.is_gdb_alive():
            return OperationError(message="GDB process has exited - cannot execute call")

        command = f"call {function_call}"
        mi_command = wrap_cli_command(command)

        result = self._command_runner.send_command_and_wait_for_prompt(mi_command, timeout_sec)

        if "error" in result:
            return OperationError(
                message=str(result["error"]),
                details={"function_call": function_call},
            )

        if result.get("timed_out"):
            return OperationError(
                message=f"Timeout waiting for call to complete after {timeout_sec}s",
                details={"function_call": function_call},
            )

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)
        if parsed.is_error_result():
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"function_call": function_call},
            )
        console_output = "".join(item for item in parsed.console if isinstance(item, str))

        return OperationSuccess(
            FunctionCallInfo(
                function_call=function_call,
                result=console_output.strip() if console_output else "(no return value)",
            )
        )
