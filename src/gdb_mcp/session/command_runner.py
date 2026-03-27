"""Shared command execution helpers for one debugger session."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..domain import CommandExecutionInfo, OperationError, OperationSuccess
from ..transport import is_cli_command, parse_mi_responses, wrap_cli_command
from .constants import DEFAULT_TIMEOUT_SEC
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)


class SessionCommandRunner:
    """Own command sending, liveness checks, and result normalization."""

    def __init__(self, runtime: SessionRuntime):
        self._runtime = runtime

    def is_gdb_alive(self) -> bool:
        """Check if the GDB process is still running."""

        return self._runtime.transport.is_alive()

    def send_command_and_wait_for_prompt(
        self, command: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC
    ) -> dict[str, object]:
        """Send a command through transport and update fatal session state."""

        result = self._runtime.transport.send_command_and_wait_for_prompt(
            command, timeout_sec=timeout_sec
        )

        if result.fatal:
            failure_message = result.error or "GDB transport failed"
            self._runtime.mark_failed(failure_message)

        return result.to_dict()

    def interrupt_and_wait_for_stop(
        self,
        *,
        send_interrupt: Callable[[], None],
        timeout_sec: float,
    ) -> dict[str, object]:
        """Interrupt the inferior while reusing transport serialization rules."""

        result = self._runtime.transport.interrupt_and_wait_for_stop(
            send_interrupt=send_interrupt,
            timeout_sec=timeout_sec,
        )

        if result.fatal:
            failure_message = result.error or "GDB transport failed"
            self._runtime.mark_failed(failure_message)

        return result.to_dict()

    def execute_command_result(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a GDB command and normalize CLI vs MI results."""

        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self.is_gdb_alive():
            logger.error("GDB process is not running when trying to execute: %s", command)
            return OperationError(
                message="GDB process has exited - cannot execute command",
                details={"command": command},
            )

        cli_command = is_cli_command(command)
        actual_command = wrap_cli_command(command) if cli_command else command

        if cli_command:
            logger.debug("Wrapping CLI command: %s -> %s", command, actual_command)

        result = self.send_command_and_wait_for_prompt(actual_command, timeout_sec)

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

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)

        if parsed.is_error_result():
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"command": command},
            )

        if cli_command:
            console_output = "".join(item for item in parsed.console if isinstance(item, str))
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    output=console_output.strip() if console_output else "(no output)",
                )
            )

        return OperationSuccess(CommandExecutionInfo(command=command, result=parsed.to_dict()))
