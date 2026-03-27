"""Transport client responsible for synchronous GDB/MI command exchange."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Any, Callable

from .mi_models import MiTransportResponse

logger = logging.getLogger(__name__)

_LIVENESS_CHECK_INTERVAL_SEC = 1.0


class MiClient:
    """Owns the GDB controller and the low-level MI command transport."""

    def __init__(
        self,
        *,
        controller_factory: Callable[..., Any],
        initial_command_token: int,
        poll_timeout_sec: float,
    ):
        self._controller_factory = controller_factory
        self._controller: Any = None
        self._command_token = initial_command_token
        self._poll_timeout_sec = poll_timeout_sec
        self._command_lock = threading.Lock()

    @property
    def controller(self) -> Any:
        """Return the active pygdbmi controller if one exists."""

        return self._controller

    @controller.setter
    def controller(self, value: Any) -> None:
        """Override the controller, mainly for tests."""

        self._controller = value

    def start(
        self,
        *,
        command: list[str],
        time_to_check_for_additional_output_sec: float,
        cwd: str | None = None,
    ) -> Any:
        """Start a new GDB controller process."""

        controller_kwargs = {
            "command": command,
            "time_to_check_for_additional_output_sec": time_to_check_for_additional_output_sec,
        }
        if cwd is not None:
            controller_kwargs["cwd"] = cwd

        self._controller = self._controller_factory(
            **controller_kwargs,
        )
        return self._controller

    def exit(self) -> None:
        """Stop the current controller if one exists."""

        if self._controller is None:
            return

        try:
            self._controller.exit()
        finally:
            self._controller = None

    def is_alive(self) -> bool:
        """Check whether the underlying GDB process is still alive."""

        if not self._controller:
            return False

        try:
            if not hasattr(self._controller, "gdb_process"):
                return True

            gdb_process = self._controller.gdb_process

            if not isinstance(gdb_process, subprocess.Popen):
                return True

            poll_result = gdb_process.poll()
            if poll_result is not None:
                logger.error("GDB process exited with code %s", poll_result)
            return poll_result is None
        except Exception as exc:
            logger.debug("Exception checking if GDB alive: %s, assuming alive", exc)
            return True

    def send_command_and_wait_for_prompt(
        self,
        command: str,
        *,
        timeout_sec: float,
    ) -> MiTransportResponse:
        """Send one command and collect responses until the result record is received."""

        if not self._controller:
            return MiTransportResponse(timed_out=True, error="No active GDB session")

        with self._command_lock:
            token = self._command_token
            self._command_token += 1

            tokenized_command = f"{token}{command}"
            logger.debug("Sending tokenized command: %s", tokenized_command)

            try:
                self._controller.io_manager.stdin.write((tokenized_command + "\n").encode())
                self._controller.io_manager.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                logger.error("Failed to send command: %s", exc)
                return MiTransportResponse(error=f"Failed to send command: {exc}")

            command_responses: list[dict[str, Any]] = []
            async_notifications: list[dict[str, Any]] = []
            start_time = time.monotonic()
            last_activity_time = start_time
            last_alive_check = 0.0
            saw_result_record = False
            saw_stop_after_running = False
            result_class: str | None = None

            while time.monotonic() - start_time < timeout_sec:
                elapsed = time.monotonic() - start_time
                if elapsed - last_alive_check >= _LIVENESS_CHECK_INTERVAL_SEC:
                    if not self.is_alive():
                        exit_code = self._extract_exit_code()
                        error_details = f"GDB process exited unexpectedly after {elapsed:.1f}s"
                        if exit_code is not None:
                            if exit_code == -9:
                                error_details += " (exit code -9: killed, likely out of memory)"
                            elif exit_code == -6:
                                error_details += " (exit code -6: aborted, possibly assertion failure)"
                            elif exit_code == -11:
                                error_details += " (exit code -11: segmentation fault)"
                            else:
                                error_details += f" (exit code {exit_code})"

                        logger.error(error_details)
                        return MiTransportResponse(
                            command_responses=command_responses,
                            async_notifications=async_notifications,
                            error=error_details,
                        )

                    last_alive_check = elapsed
                    inactive_time = time.monotonic() - last_activity_time
                    logger.debug(
                        "Still waiting for response... (total: %.1fs, inactive: %.1fs)",
                        elapsed,
                        inactive_time,
                    )

                try:
                    responses = self._controller.get_gdb_response(
                        timeout_sec=self._poll_timeout_sec,
                        raise_error_on_timeout=False,
                    )
                except (BrokenPipeError, OSError) as exc:
                    logger.error("Communication error while reading responses: %s", exc)
                    return MiTransportResponse(
                        command_responses=command_responses,
                        async_notifications=async_notifications,
                        error=f"Communication error: {exc}",
                    )

                if not responses:
                    if saw_result_record and (result_class != "running" or saw_stop_after_running):
                        return MiTransportResponse(
                            command_responses=command_responses,
                            async_notifications=async_notifications,
                        )
                    continue

                last_activity_time = time.monotonic()

                for response in responses:
                    response_type = response.get("type")
                    response_token = response.get("token")

                    logger.debug(
                        "Received: type=%s, token=%s, message=%s",
                        response_type,
                        response_token,
                        response.get("message"),
                    )

                    payload = response.get("payload", "")
                    if response_type in ("console", "log") and self._is_fatal_payload(payload):
                        logger.error("GDB internal fatal error detected: %s", payload)
                        self.exit()
                        return MiTransportResponse(
                            command_responses=command_responses,
                            async_notifications=async_notifications,
                            error=f"GDB internal fatal error: {str(payload).strip()}",
                            fatal=True,
                        )

                    if response_type == "result" and response_token == token:
                        command_responses.append(response)
                        result_class = (
                            response.get("message") if isinstance(response.get("message"), str) else None
                        )
                        saw_result_record = True
                        logger.debug(
                            "Received result record for token %s with class %s",
                            token,
                            result_class,
                        )
                        continue

                    if response_token == token or response_token is None:
                        command_responses.append(response)
                        if (
                            saw_result_record
                            and result_class == "running"
                            and response_type == "notify"
                            and response.get("message") == "stopped"
                        ):
                            saw_stop_after_running = True
                    else:
                        async_notifications.append(response)
                        logger.info(
                            "Async notification (token=%s): %s - %s",
                            response_token,
                            response.get("message"),
                            response.get("payload"),
                        )

            elapsed = time.monotonic() - start_time
            logger.warning(
                "Timeout: no GDB output for %ss (total elapsed: %.1fs)",
                timeout_sec,
                elapsed,
            )
            return MiTransportResponse(
                command_responses=command_responses,
                async_notifications=async_notifications,
                timed_out=True,
            )

    def interrupt_and_wait_for_stop(
        self,
        *,
        send_interrupt: Callable[[], None],
        timeout_sec: float,
    ) -> MiTransportResponse:
        """Interrupt the inferior while preserving serialized access to the MI stream."""

        if not self._controller:
            return MiTransportResponse(error="No active GDB session")

        acquired = self._command_lock.acquire(blocking=False)
        if not acquired:
            return MiTransportResponse(error="Cannot interrupt while another command is in progress")

        try:
            send_interrupt()

            command_responses: list[dict[str, Any]] = []
            start_time = time.monotonic()

            while time.monotonic() - start_time < timeout_sec:
                try:
                    responses = self._controller.get_gdb_response(
                        timeout_sec=self._poll_timeout_sec,
                        raise_error_on_timeout=False,
                    )
                except (BrokenPipeError, OSError) as exc:
                    logger.error("Communication error while waiting for interrupt response: %s", exc)
                    return MiTransportResponse(
                        command_responses=command_responses,
                        error=f"Communication error: {exc}",
                    )

                if not responses:
                    continue

                for response in responses:
                    command_responses.append(response)
                    response_type = response.get("type")
                    payload = response.get("payload", "")

                    if response_type in ("console", "log") and self._is_fatal_payload(payload):
                        logger.error("GDB internal fatal error detected during interrupt: %s", payload)
                        self.exit()
                        return MiTransportResponse(
                            command_responses=command_responses,
                            error=f"GDB internal fatal error: {str(payload).strip()}",
                            fatal=True,
                        )

                    if response_type == "notify" and response.get("message") == "stopped":
                        return MiTransportResponse(command_responses=command_responses)

            return MiTransportResponse(command_responses=command_responses, timed_out=True)
        except Exception as exc:
            logger.error("Failed to interrupt program: %s", exc)
            return MiTransportResponse(error=f"Failed to interrupt: {exc}")
        finally:
            self._command_lock.release()

    def _extract_exit_code(self) -> int | None:
        """Return the current process exit code when it is available."""

        if not self._controller or not hasattr(self._controller, "gdb_process"):
            return None

        gdb_process = self._controller.gdb_process
        if not isinstance(gdb_process, subprocess.Popen):
            return None

        try:
            return gdb_process.poll()
        except Exception:
            return None

    @staticmethod
    def _is_fatal_payload(payload: Any) -> bool:
        """Detect unrecoverable fatal error text emitted by GDB itself."""

        if not payload:
            return False

        payload_lower = str(payload).lower()
        return "internal-error" in payload_lower or "fatal error internal to gdb" in payload_lower
