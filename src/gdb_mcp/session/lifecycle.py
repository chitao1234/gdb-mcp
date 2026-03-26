"""Lifecycle and transport coordination methods for SessionService."""

from __future__ import annotations

import logging
from typing import Optional

from ..domain import OperationError, OperationSuccess, SessionMessage, SessionStartInfo, SessionStatusSnapshot, result_to_mapping
from ..transport import parse_mi_responses
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC, FILE_LOAD_TIMEOUT_SEC, INIT_COMMAND_DELAY_SEC
from .state import SessionState

logger = logging.getLogger(__name__)


class SessionLifecycleMixin:
    """Lifecycle methods used by SessionService."""

    def start(
        self,
        program: Optional[str] = None,
        args: Optional[list[str]] = None,
        init_commands: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        gdb_path: Optional[str] = None,
        working_dir: Optional[str] = None,
        core: Optional[str] = None,
    ) -> OperationSuccess[SessionStartInfo] | OperationError:
        """Start a new GDB session."""
        if self.controller:
            return OperationError(message="Session already running. Stop it first.")

        if gdb_path is None:
            gdb_path = self._os.environ.get("GDB_PATH", "gdb")

        self.config = SessionConfig.from_inputs(
            program=program,
            args=args,
            init_commands=init_commands,
            env=env,
            gdb_path=gdb_path,
            working_dir=working_dir,
            core=core,
        )
        self.state = SessionState.STARTING

        try:
            if working_dir and not self._os.path.isdir(working_dir):
                self.state = SessionState.FAILED
                return OperationError(message=f"Working directory does not exist: {working_dir}")

            gdb_command = [gdb_path, "--quiet", "--interpreter=mi"]

            if program:
                if args:
                    gdb_command.extend(["--args", program])
                    gdb_command.extend(args)
                else:
                    gdb_command.append(program)

            if core:
                gdb_command.extend(["--core", core])
                logger.info("Loading core dump: %s", core)

            self._transport.start(
                command=gdb_command,
                time_to_check_for_additional_output_sec=1.0,
                cwd=working_dir,
            )

            logger.debug("Waiting for GDB initialization to complete...")
            ready_check = self._send_command_and_wait_for_prompt(
                "-gdb-version", timeout_sec=DEFAULT_TIMEOUT_SEC
            )

            if "error" in ready_check or ready_check.get("timed_out"):
                error_msg = ready_check.get("error", "Timeout waiting for GDB to initialize")
                logger.error("GDB failed to initialize: %s", error_msg)
                if self.controller:
                    try:
                        self._transport.exit()
                    except Exception:
                        pass
                    self.controller = None
                error_response: dict[str, object] = {
                    "message": f"GDB failed to initialize: {error_msg}",
                }
                self.state = SessionState.FAILED
                return OperationError(
                    message=str(error_response["message"]),
                    fatal=bool(ready_check.get("fatal", False)),
                )

            logger.info("GDB initialized and ready")

            startup_result = parse_mi_responses(ready_check.get("command_responses", []))
            startup_console = "".join(startup_result.console)

            warnings = []
            if "no debugging symbols found" in startup_console.lower():
                warnings.append("No debugging symbols found - program was not compiled with -g")
            if "not in executable format" in startup_console.lower():
                warnings.append("File is not an executable")
            if "no such file" in startup_console.lower():
                warnings.append("Program file not found")

            init_output = []
            if init_commands:
                for cmd in init_commands:
                    try:
                        logger.info("Executing init command: %s", cmd)

                        if "core-file" in cmd.lower() or cmd.lower().startswith("file "):
                            timeout = FILE_LOAD_TIMEOUT_SEC
                            logger.info(
                                "Using extended timeout (%ss) for file loading command", timeout
                            )
                        else:
                            timeout = DEFAULT_TIMEOUT_SEC

                        result = self._execute_command_result(cmd, timeout_sec=timeout)
                        init_output.append(result_to_mapping(result))

                        if "core-file" in cmd.lower():
                            self._time.sleep(INIT_COMMAND_DELAY_SEC)
                            logger.debug("Waiting for GDB to stabilize after core-file command")

                        if isinstance(result, OperationError):
                            error_msg = result.message
                            logger.error("Init command '%s' failed: %s", cmd, error_msg)

                            if (
                                result.fatal
                                or "GDB process" in error_msg
                                or not self._is_gdb_alive()
                            ):
                                logger.error("GDB process died during init commands")
                                self.state = SessionState.FAILED
                                return OperationError(
                                    message=f"GDB crashed during init command '{cmd}': {error_msg}",
                                    fatal=result.fatal,
                                    details={"init_output": init_output},
                                )

                        if "file" in cmd.lower():
                            logger.debug(
                                "Setting target_loaded=True after file-related command: %s", cmd
                            )
                            self.target_loaded = True
                    except Exception as exc:
                        logger.error("Exception during init command '%s': %s", cmd, exc, exc_info=True)
                        init_output.append({"status": "error", "command": cmd, "message": str(exc)})

                        if not self._is_gdb_alive():
                            logger.error("GDB process died during init command execution")
                            self.state = SessionState.FAILED
                            return OperationError(
                                message=f"GDB crashed during init command '{cmd}': {str(exc)}",
                                details={"init_output": init_output},
                            )

            env_output = []
            if env:
                for var_name, var_value in env.items():
                    escaped_value = var_value.replace("\\", "\\\\").replace('"', '\\"')
                    env_cmd = f"set environment {var_name} {escaped_value}"
                    result = self._execute_command_result(env_cmd)
                    env_output.append(result_to_mapping(result))

            if program or core:
                self.target_loaded = True

            self.is_running = True
            self.state = SessionState.READY

            return OperationSuccess(
                SessionStartInfo(
                    message="GDB session started",
                    program=program,
                    core=core,
                    startup_output=startup_console.strip() or None,
                    warnings=warnings or None,
                    env_output=env_output or None,
                    init_output=init_output or None,
                )
            )

        except Exception as exc:
            logger.error("Failed to start GDB session: %s", exc)
            if self.controller:
                try:
                    self.controller.exit()
                except Exception:
                    pass
                self.controller = None
            self.state = SessionState.FAILED
            return OperationError(message=f"Failed to start GDB: {str(exc)}")

    def _is_gdb_alive(self) -> bool:
        """Check if the GDB process is still running."""
        return self._transport.is_alive()

    def _send_command_and_wait_for_prompt(
        self, command: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC
    ) -> dict[str, object]:
        """Send a command through the transport and normalize fatal cleanup."""
        result = self._transport.send_command_and_wait_for_prompt(command, timeout_sec=timeout_sec)

        if result.fatal:
            self.is_running = False
            self.target_loaded = False
            self.state = SessionState.FAILED

        return result.to_dict()

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        """Stop the GDB session."""
        if not self.controller:
            return OperationError(message="No active session")

        try:
            self.controller.exit()
            self.controller = None
            self.is_running = False
            self.target_loaded = False
            self.state = SessionState.STOPPED

            return OperationSuccess(SessionMessage(message="GDB session stopped"))

        except Exception as exc:
            logger.error("Failed to stop GDB session: %s", exc)
            return OperationError(message=str(exc))

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        """Get the current status of the GDB session."""
        snapshot = SessionStatusSnapshot(
            is_running=self.is_running,
            target_loaded=self.target_loaded,
            has_controller=self.controller is not None,
        )
        return OperationSuccess(snapshot)
