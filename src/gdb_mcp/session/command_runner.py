"""Shared command execution helpers for one debugger session."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

from ..domain import (
    CommandExecutionInfo,
    CommandTranscriptEntry,
    FollowForkMode,
    FrameRecord,
    OperationError,
    OperationSuccess,
    StopEvent,
    StructuredPayload,
    payload_to_mapping,
)
from ..transport import ParsedMiResponse, is_cli_command, parse_mi_responses, wrap_cli_command
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

    def wait_for_stop(self, timeout_sec: float) -> dict[str, object]:
        """Wait for a stopped notification without sending a debugger command."""

        result = self._runtime.transport.wait_for_stop(timeout_sec=timeout_sec)

        if result.fatal:
            failure_message = result.error or "GDB transport failed"
            self._runtime.mark_transport_terminated(failure_message)

        return result.to_dict()

    def handle_dead_transport(self, message: str) -> None:
        """Transition the session into a terminal state after GDB has exited."""

        self._runtime.mark_transport_terminated(message)

    def update_runtime_after_command(self, command: str, parsed: ParsedMiResponse) -> None:
        """Apply parsed MI execution effects to the mutable session runtime."""

        self._update_runtime_after_command(command, parsed)

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
            self._record_command_transcript(
                command=command,
                sent_command=command,
                status="error",
                error=message,
            )
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
            self._record_command_transcript(
                command=command,
                sent_command=actual_command,
                status="error",
                error=str(result["error"]),
                fatal=bool(result.get("fatal", False)),
            )
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
            self._record_command_transcript(
                command=command,
                sent_command=actual_command,
                status="timeout",
                parsed=parsed,
                timed_out=True,
                error=f"Timeout waiting for command response after {timeout_sec}s",
            )
            return OperationError(
                message=f"Timeout waiting for command response after {timeout_sec}s",
                details={"command": command},
            )

        if parsed.is_error_result():
            self._record_command_transcript(
                command=command,
                sent_command=actual_command,
                status="error",
                parsed=parsed,
                error=parsed.error_message() or "GDB returned an error",
            )
            return OperationError(
                message=parsed.error_message() or "GDB returned an error",
                details={"command": command},
            )

        self._update_runtime_after_command(command, parsed)
        self._record_command_transcript(
            command=command,
            sent_command=actual_command,
            status="success",
            parsed=parsed,
        )

        if cli_command:
            console_output = "".join(item for item in parsed.console if isinstance(item, str))
            return OperationSuccess(
                CommandExecutionInfo(
                    command=command,
                    output=console_output.strip() if console_output else "(no output)",
                )
            )

        return OperationSuccess(
            CommandExecutionInfo(
                command=command,
                result=cast(StructuredPayload, parsed.to_dict()),
            )
        )

    def _update_runtime_after_command(self, command: str, parsed: ParsedMiResponse) -> None:
        """Update inferior execution state based on the parsed MI response."""

        stopped_reason: str | None = None
        exit_code: int | None = None
        saw_stopped = False
        stop_event: StopEvent | None = None
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
            stop_event = self._build_stop_event(command, payload, stopped_reason, exit_code)
            break

        if saw_stopped:
            if stopped_reason in {"exited", "exited-normally", "exited-signalled"}:
                self._runtime.mark_inferior_exited(stopped_reason, exit_code)
            else:
                self._runtime.mark_inferior_paused(stopped_reason)
            if stop_event is not None:
                self._runtime.record_stop_event(stop_event)
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
        elif normalized_command.startswith("inferior ") and not parsed.is_error_result():
            inferior_id = self._parse_inferior_id(normalized_command)
            if inferior_id is not None:
                self._runtime.mark_inferior_selected(inferior_id)
        elif normalized_command.startswith("set follow-fork-mode ") and not parsed.is_error_result():
            follow_fork_mode = self._parse_follow_fork_mode(normalized_command)
            self._runtime.mark_follow_fork_mode(follow_fork_mode)
        elif normalized_command.startswith("set detach-on-fork ") and not parsed.is_error_result():
            detach_on_fork = self._parse_detach_on_fork(normalized_command)
            self._runtime.mark_detach_on_fork(detach_on_fork)

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

    @staticmethod
    def _parse_inferior_id(command: str) -> int | None:
        """Parse an inferior-selection command and return the selected inferior ID."""

        parts = command.split(maxsplit=1)
        if len(parts) != 2:
            return None
        inferior_id = parts[1].strip()
        return int(inferior_id) if inferior_id.isdigit() else None

    @staticmethod
    def _parse_follow_fork_mode(command: str) -> FollowForkMode | None:
        """Parse the configured follow-fork-mode from a normalized CLI command."""

        parts = command.split()
        if len(parts) != 3:
            return None
        mode = parts[2]
        if mode == "parent":
            return "parent"
        if mode == "child":
            return "child"
        return None

    @staticmethod
    def _parse_detach_on_fork(command: str) -> bool | None:
        """Parse the configured detach-on-fork value from a normalized CLI command."""

        parts = command.split()
        if len(parts) != 3:
            return None
        value = parts[2]
        if value == "on":
            return True
        if value == "off":
            return False
        return None

    def _build_stop_event(
        self,
        command: str,
        payload: object,
        stopped_reason: str | None,
        exit_code: int | None,
    ) -> StopEvent:
        """Build a structured stop event from one MI stopped notification."""

        payload_mapping: StructuredPayload = {}
        if isinstance(payload, dict):
            payload_mapping = cast(StructuredPayload, payload_to_mapping(payload))

        thread_id = self._int_or_none(payload_mapping.get("thread-id"))
        frame = self._frame_record(payload_mapping.get("frame"))
        signal_name = self._str_or_none(payload_mapping.get("signal-name"))
        signal_meaning = self._str_or_none(payload_mapping.get("signal-meaning"))
        breakpoint_number = self._extract_breakpoint_number(payload_mapping)
        execution_state = (
            "exited"
            if stopped_reason in {"exited", "exited-normally", "exited-signalled"}
            else "paused"
        )

        return StopEvent(
            execution_state=execution_state,
            reason=stopped_reason,
            command=command,
            thread_id=thread_id,
            frame=frame,
            signal_name=signal_name,
            signal_meaning=signal_meaning,
            breakpoint_number=breakpoint_number,
            exit_code=exit_code,
            timestamp=self._runtime.time_module.time(),
            details=payload_mapping,
        )

    def _record_command_transcript(
        self,
        *,
        command: str,
        sent_command: str,
        status: str,
        parsed: ParsedMiResponse | None = None,
        timed_out: bool = False,
        fatal: bool = False,
        error: str | None = None,
    ) -> None:
        """Record structured transcript metadata for one debugger command."""

        self._runtime.record_command_transcript(
            CommandTranscriptEntry(
                command=command,
                sent_command=sent_command,
                status=status,
                result_class=parsed.result_class if parsed is not None else None,
                timed_out=timed_out,
                fatal=fatal,
                error=error,
                execution_state=self._runtime.execution_state,
                stop_reason=self._runtime.stop_reason,
                timestamp=self._runtime.time_module.time(),
            )
        )

    @staticmethod
    def _frame_record(value: object) -> FrameRecord | None:
        """Normalize an MI frame mapping into the structured frame record type."""

        return cast(FrameRecord, value) if isinstance(value, dict) else None

    @classmethod
    def _extract_breakpoint_number(cls, payload: StructuredPayload) -> str | None:
        """Extract a breakpoint or watchpoint number from one stop payload."""

        breakpoint_number = cls._str_or_none(payload.get("bkptno"))
        if breakpoint_number is not None:
            return breakpoint_number

        for key in ("wpt", "awpt", "hw-awpt"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                nested_number = cls._str_or_none(nested.get("number"))
                if nested_number is not None:
                    return nested_number

        return None

    @staticmethod
    def _str_or_none(value: object) -> str | None:
        """Return a string-compatible scalar as text when possible."""

        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        return None
