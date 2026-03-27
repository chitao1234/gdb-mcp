"""Shared command execution helpers for one debugger session."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

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
            self._runtime.mark_transport_terminated(failure_message)

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
            self._runtime.mark_transport_terminated(failure_message)

        return result.to_dict()

    def handle_dead_transport(self, message: str) -> None:
        """Transition the session into a terminal state after GDB has exited."""

        self._runtime.mark_transport_terminated(message)

    def execute_command_result(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> OperationSuccess[CommandExecutionInfo] | OperationError:
        """Execute a GDB command and normalize CLI vs MI results."""

        if not self._runtime.has_controller:
            return OperationError(message="No active GDB session")

        if not self.is_gdb_alive():
            logger.error("GDB process is not running when trying to execute: %s", command)
            message = "GDB process has exited - session is no longer active"
            self.handle_dead_transport(message)
            return OperationError(
                message=message,
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

        raw_responses = result.get("command_responses", [])
        command_responses = raw_responses if isinstance(raw_responses, list) else []
        parsed = parse_mi_responses(command_responses)

        if result.get("timed_out"):
            self._update_runtime_after_command(command, parsed)
            return OperationError(
                message=f"Timeout waiting for command response after {timeout_sec}s",
                details={"command": command},
            )

        if parsed.is_error_result():
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"command": command},
            )

        self._update_runtime_after_command(command, parsed)

        if cli_command:
            console_output = "".join(item for item in parsed.console if isinstance(item, str))
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    output=console_output.strip() if console_output else "(no output)",
                )
            )

        return OperationSuccess(CommandExecutionInfo(command=command, result=parsed.to_dict()))

    def _update_runtime_after_command(self, command: str, parsed: Any) -> None:
        """Update inferior execution state based on the parsed MI response."""

        stopped_reason: str | None = None
        exit_code: int | None = None
        saw_stopped = False
        for notify in parsed.notify:
            if notify.get("message") != "stopped":
                continue

            saw_stopped = True
            payload = notify.get("payload")
            if isinstance(payload, dict):
                reason_value = payload.get("reason")
                if isinstance(reason_value, str):
                    stopped_reason = reason_value
                exit_code = self._parse_exit_code(payload.get("exit-code"))
                frame_payload = payload.get("frame")
                if isinstance(frame_payload, dict):
                    self._runtime.mark_frame_selected(self._int_or_none(frame_payload.get("level")))
                thread_id = self._int_or_none(payload.get("thread-id"))
                if thread_id is not None:
                    self._runtime.mark_thread_selected(thread_id)
            break

        if saw_stopped:
            if stopped_reason in {"exited", "exited-normally", "exited-signalled"}:
                self._runtime.mark_inferior_exited(stopped_reason, exit_code)
            else:
                self._runtime.mark_inferior_paused(stopped_reason)
        elif parsed.result_class == "running":
            self._runtime.mark_inferior_running()

        normalized_command = command.strip().lower()
        if self._loads_target(normalized_command):
            self._runtime.target_loaded = True
            self._runtime.clear_attached_pid()
            if self._runtime.execution_state == "unknown":
                if normalized_command.startswith("core-file "):
                    self._runtime.mark_inferior_paused("core-file")
                else:
                    self._runtime.mark_inferior_not_started()
        elif normalized_command.startswith("attach ") and not parsed.is_error_result():
            self._runtime.target_loaded = True
            attached_pid = self._parse_attached_pid(normalized_command)
            if attached_pid is not None:
                self._runtime.mark_attached(attached_pid)
            if self._runtime.execution_state == "unknown":
                self._runtime.mark_inferior_paused("attached")
        elif normalized_command == "detach" and not parsed.is_error_result():
            self._runtime.clear_attached_pid()

    @staticmethod
    def _loads_target(command: str) -> bool:
        """Return whether a successful command loads an executable or core."""

        return command.startswith("file ") or command.startswith("core-file ")

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        """Parse a string or int field into an integer when possible."""

        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _parse_exit_code(value: object) -> int | None:
        """Parse a GDB exit-code field."""

        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value, 0)
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_attached_pid(command: str) -> int | None:
        """Parse an attach command and return the PID if present."""

        parts = command.split(maxsplit=1)
        if len(parts) != 2:
            return None
        pid_text = parts[1].strip()
        return int(pid_text) if pid_text.isdigit() else None
