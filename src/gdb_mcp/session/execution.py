"""Command execution helpers for SessionService."""

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
from ..transport import is_cli_command, parse_mi_responses, wrap_cli_command
from .constants import DEFAULT_TIMEOUT_SEC, INTERRUPT_RESPONSE_TIMEOUT_SEC, POLL_TIMEOUT_SEC

logger = logging.getLogger(__name__)


class SessionExecutionMixin:
    """Execution and command-control methods used by SessionService."""

    def _execute_command_result(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a GDB command and return the parsed response."""
        if not self.controller:
            return OperationError(message="No active GDB session")

        if not self._is_gdb_alive():
            logger.error("GDB process is not running when trying to execute: %s", command)
            return OperationError(
                message="GDB process has exited - cannot execute command",
                details={"command": command},
            )

        cli_command = is_cli_command(command)
        actual_command = wrap_cli_command(command) if cli_command else command

        if cli_command:
            logger.debug("Wrapping CLI command: %s -> %s", command, actual_command)

        result = self._send_command_and_wait_for_prompt(actual_command, timeout_sec)

        if "error" in result:
            return OperationError(
                message=str(result["error"]),
                fatal=bool(result.get("fatal", False)),
                details={"command": command},
            )

        if result.get("timed_out"):
            return OperationError(
                message=f"Timeout waiting for command response after {timeout_sec}s",
                details={"command": command},
            )

        command_responses = result.get("command_responses", [])
        parsed = parse_mi_responses(command_responses)

        if parsed.is_error_result():
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"command": command},
            )

        if cli_command:
            console_output = "".join(parsed.console)
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    output=console_output.strip() if console_output else "(no output)",
                )
            )

        return OperationSuccess(CommandExecutionInfo(command=command, result=parsed.to_dict()))

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a GDB command and return the parsed response."""

        return self._execute_command_result(command, timeout_sec)

    def run(self, args: Optional[list[str]] = None) -> dict[str, object]:
        """Run the program."""
        if not self.controller:
            return OperationError(message="No active GDB session")

        if args:
            arg_str = " ".join(args)
            result = self._execute_command_result(f"-exec-arguments {arg_str}")
            if isinstance(result, OperationError):
                return result

        return self._execute_command_result("-exec-run")

    def continue_execution(self) -> dict[str, object]:
        """Continue execution of the program."""
        return self._execute_command_result("-exec-continue")

    def step(self) -> dict[str, object]:
        """Step into the next source line."""
        return self._execute_command_result("-exec-step")

    def next(self) -> dict[str, object]:
        """Step over the next source line."""
        return self._execute_command_result("-exec-next")

    def interrupt(self) -> dict[str, object]:
        """Interrupt (pause) a running program."""
        if not self.controller:
            return OperationError(message="No active GDB session")

        if not self.controller.gdb_process:
            return OperationError(message="No GDB process running")

        try:
            self._os.kill(self.controller.gdb_process.pid, signal.SIGINT)

            start_time = self._time.time()
            all_responses: list[dict[str, object]] = []
            stopped_received = False

            while self._time.time() - start_time < INTERRUPT_RESPONSE_TIMEOUT_SEC:
                responses = self.controller.get_gdb_response(
                    timeout_sec=POLL_TIMEOUT_SEC, raise_error_on_timeout=False
                )

                if responses:
                    all_responses.extend(responses)
                    for resp in responses:
                        if resp.get("type") == "notify" and resp.get("message") == "stopped":
                            stopped_received = True
                            break

                if stopped_received:
                    break

            result = parse_mi_responses(all_responses).to_dict()

            if not stopped_received:
                return OperationSuccess(
                    MessageResult(
                        message="Interrupt sent but no stopped notification received",
                        result=result,
                        status="warning",
                    )
                )

            return OperationSuccess(
                MessageResult(message="Program interrupted (paused)", result=result)
            )
        except Exception as exc:
            logger.error("Failed to interrupt program: %s", exc)
            return OperationError(message=f"Failed to interrupt: {str(exc)}")

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[FunctionCallInfo] | OperationError:
        """Call a function in the target process."""
        if not self.controller:
            return OperationError(message="No active GDB session")

        if not self._is_gdb_alive():
            return OperationError(message="GDB process has exited - cannot execute call")

        command = f"call {function_call}"
        mi_command = wrap_cli_command(command)

        result = self._send_command_and_wait_for_prompt(mi_command, timeout_sec)

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

        parsed = parse_mi_responses(result.get("command_responses", []))
        if parsed.is_error_result():
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"function_call": function_call},
            )
        console_output = "".join(parsed.console)

        return OperationSuccess(
            FunctionCallInfo(
                function_call=function_call,
                result=console_output.strip() if console_output else "(no return value)",
            )
        )
