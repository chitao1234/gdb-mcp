"""Lifecycle coordination for a composed SessionService."""

from __future__ import annotations

import logging
from typing import Optional, TypeAlias, cast

from ..domain import (
    OperationError,
    OperationSuccess,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
    StructuredPayload,
    payload_to_mapping,
    result_to_mapping,
)
from ..transport import ParsedMiResponse, parse_mi_responses
from .command_runner import SessionCommandRunner
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC, FILE_LOAD_TIMEOUT_SEC, INIT_COMMAND_DELAY_SEC
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)

SerializedResultMapping: TypeAlias = StructuredPayload


class SessionLifecycleService:
    """Lifecycle orchestration backed by explicit runtime state."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

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
        with self._runtime.lifecycle_lock:
            if self._runtime.has_controller:
                return OperationError(message="Session already running. Stop it first.")

            if gdb_path is None:
                gdb_path = self._runtime.os_module.environ.get("GDB_PATH", "gdb")

            config = SessionConfig.from_inputs(
                program=program,
                args=args,
                init_commands=init_commands,
                env=env,
                gdb_path=gdb_path,
                working_dir=working_dir,
                core=core,
            )
            self._runtime.begin_startup(config)

            try:
                if args and core:
                    self._runtime.mark_failed(
                        "Cannot combine program arguments with core dump analysis in one startup request."
                    )
                    return OperationError(
                        message=(
                            "Cannot combine 'args' with 'core'. "
                            "Use either a live program launch with args or a core-dump session."
                        )
                    )

                if working_dir and not self._runtime.os_module.path.isdir(working_dir):
                    self._runtime.mark_failed(f"Working directory does not exist: {working_dir}")
                    return OperationError(
                        message=f"Working directory does not exist: {working_dir}"
                    )

                gdb_command = self._build_start_command(
                    gdb_path=gdb_path,
                    program=program,
                    args=args,
                    core=core,
                )
                if core:
                    logger.info("Loading core dump: %s", core)

                self._runtime.transport.start(
                    command=gdb_command,
                    time_to_check_for_additional_output_sec=1.0,
                    cwd=working_dir,
                )

                initial_startup_responses = self._runtime.transport.read_initial_output(
                    timeout_sec=1.0
                )

                logger.debug("Waiting for GDB initialization to complete...")
                ready_check = self._command_runner.send_command_and_wait_for_prompt(
                    "-gdb-version", timeout_sec=DEFAULT_TIMEOUT_SEC
                )

                if "error" in ready_check or ready_check.get("timed_out"):
                    error_msg = ready_check.get("error", "Timeout waiting for GDB to initialize")
                    logger.error("GDB failed to initialize: %s", error_msg)
                    if self._runtime.has_controller:
                        try:
                            self._runtime.transport.exit()
                        except Exception:
                            pass
                    message = f"GDB failed to initialize: {error_msg}"
                    self._runtime.mark_failed(message)
                    return OperationError(
                        message=message,
                        fatal=bool(ready_check.get("fatal", False)),
                    )

                logger.info("GDB initialized and ready")

                raw_startup_responses = ready_check.get("command_responses", [])
                startup_responses = list(initial_startup_responses)
                if isinstance(raw_startup_responses, list):
                    startup_responses.extend(raw_startup_responses)
                startup_result = parse_mi_responses(startup_responses)
                startup_output_text = self._startup_output_text(startup_result)

                warnings: list[str] = []
                startup_output_lower = startup_output_text.lower()
                if "no debugging symbols found" in startup_output_lower:
                    warnings.append("No debugging symbols found - program was not compiled with -g")
                if "not in executable format" in startup_output_lower:
                    warnings.append("File is not an executable")
                if "no such file" in startup_output_lower:
                    warnings.append("Program file not found")

                # Keep CLI commands non-interactive by default for automation clients.
                confirm_result = self._command_runner.execute_command_result(
                    "set confirm off",
                    timeout_sec=DEFAULT_TIMEOUT_SEC,
                )
                if isinstance(confirm_result, OperationError):
                    warnings.append(
                        "Failed to disable interactive confirmations by default: "
                        f"{confirm_result.message}"
                    )

                self._runtime.target_loaded = self._startup_target_loaded(
                    program=program,
                    core=core,
                    startup_output=startup_output_lower,
                )

                env_output = self._apply_environment(env)
                if isinstance(env_output, OperationError):
                    return env_output

                init_output: list[SerializedResultMapping] = []
                if init_commands:
                    for cmd in init_commands:
                        try:
                            logger.info("Executing init command: %s", cmd)
                            cmd_lower = cmd.lower().strip()
                            if self._loads_target(cmd_lower):
                                timeout = FILE_LOAD_TIMEOUT_SEC
                                logger.info(
                                    "Using extended timeout (%ss) for file loading command", timeout
                                )
                            else:
                                timeout = DEFAULT_TIMEOUT_SEC

                            result = self._command_runner.execute_command_result(
                                cmd, timeout_sec=timeout
                            )
                            init_output.append(
                                cast(SerializedResultMapping, result_to_mapping(result))
                            )

                            if cmd_lower.startswith("core-file "):
                                self._runtime.time_module.sleep(INIT_COMMAND_DELAY_SEC)
                                logger.debug("Waiting for GDB to stabilize after core-file command")

                            if isinstance(result, OperationError):
                                error_msg = result.message
                                logger.error("Init command '%s' failed: %s", cmd, error_msg)

                                if (
                                    result.fatal
                                    or "GDB process" in error_msg
                                    or not self._command_runner.is_gdb_alive()
                                ):
                                    logger.error("GDB process died during init commands")
                                self._cleanup_failed_start(
                                    f"Init command '{cmd}' failed: {error_msg}"
                                )
                                return OperationError(
                                    message=f"Init command '{cmd}' failed: {error_msg}",
                                    fatal=result.fatal,
                                    details={"init_output": payload_to_mapping(init_output)},
                                )

                        except Exception as exc:
                            logger.error(
                                "Exception during init command '%s': %s", cmd, exc, exc_info=True
                            )
                            init_output.append(
                                {"status": "error", "command": cmd, "message": str(exc)}
                            )

                            if not self._command_runner.is_gdb_alive():
                                logger.error("GDB process died during init command execution")
                            self._cleanup_failed_start(
                                f"Init command '{cmd}' raised an exception: {str(exc)}"
                            )
                            return OperationError(
                                message=f"Init command '{cmd}' raised an exception: {str(exc)}",
                                details={"init_output": payload_to_mapping(init_output)},
                            )

                target_loaded_probe = self._probe_target_loaded()
                if isinstance(target_loaded_probe, OperationError):
                    warnings.append(
                        "Could not refresh target_loaded from GDB after startup commands: "
                        f"{target_loaded_probe.message}"
                    )
                else:
                    self._runtime.target_loaded = target_loaded_probe
                if self._runtime.execution_state == "unknown":
                    if core or any(
                        command.lower().strip().startswith("core-file ")
                        for command in (init_commands or [])
                    ):
                        self._runtime.mark_inferior_paused("core-file")
                    elif self._runtime.target_loaded:
                        self._runtime.mark_inferior_not_started()
                self._runtime.mark_ready()

                return OperationSuccess(
                    SessionStartInfo(
                        message="GDB session started",
                        program=program,
                        core=core,
                        target_loaded=self._runtime.target_loaded,
                        execution_state=self._runtime.execution_state,
                        stop_reason=self._runtime.stop_reason,
                        exit_code=self._runtime.exit_code,
                        startup_output=startup_output_text.strip() or None,
                        warnings=warnings or None,
                        env_output=env_output or None,
                        init_output=init_output or None,
                    )
                )

            except Exception as exc:
                logger.error("Failed to start GDB session: %s", exc)
                self._cleanup_failed_start(f"Failed to start GDB: {str(exc)}")
                return OperationError(message=f"Failed to start GDB: {str(exc)}")

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        """Stop the GDB session."""
        with self._runtime.lifecycle_lock:
            if not self._runtime.has_controller:
                return OperationError(message="No active session")

            try:
                self._runtime.transport.exit()
                self._runtime.mark_stopped()

                return OperationSuccess(SessionMessage(message="GDB session stopped"))

            except Exception as exc:
                logger.error("Failed to stop GDB session: %s", exc)
                self._runtime.mark_failed(f"Failed to stop GDB session: {exc}")
                return OperationError(message=str(exc))

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        """Get the current status of the GDB session."""
        if self._runtime.has_controller and not self._command_runner.is_gdb_alive():
            self._runtime.mark_transport_terminated(
                "GDB process has exited - session is no longer active"
            )

        snapshot = SessionStatusSnapshot(
            is_running=self._runtime.is_running,
            target_loaded=self._runtime.target_loaded,
            has_controller=self._runtime.has_controller,
            execution_state=self._runtime.execution_state,
            stop_reason=self._runtime.stop_reason,
            exit_code=self._runtime.exit_code,
            current_inferior_id=self._runtime.current_inferior_id,
            inferior_count=self._runtime.inferior_count,
            inferior_states=self._runtime.inferiors_state_summary() or None,
            follow_fork_mode=self._runtime.follow_fork_mode,
            detach_on_fork=self._runtime.detach_on_fork,
        )
        return OperationSuccess(snapshot)

    def _cleanup_failed_start(self, message: str) -> None:
        """Best-effort cleanup after a startup failure."""

        if self._runtime.has_controller:
            try:
                self._runtime.transport.exit()
            except Exception:
                pass
        self._runtime.mark_failed(message)

    @staticmethod
    def _build_start_command(
        *,
        gdb_path: str,
        program: str | None,
        args: list[str] | None,
        core: str | None,
    ) -> list[str]:
        """Build a GDB startup argv for the supported launch modes."""

        command = [gdb_path, "--quiet", "--interpreter=mi"]
        if core:
            if program:
                # `--se` preserves symbol resolution for core analysis more reliably
                # than `--exec` with some GDB builds and target/core combinations.
                command.append(f"--se={program}")
            command.append(f"--core={core}")
            return command

        if program:
            if args:
                command.extend(["--args", program, *args])
            else:
                command.append(program)

        return command

    def _apply_environment(
        self, env: dict[str, str] | None
    ) -> list[SerializedResultMapping] | OperationError:
        """Apply inferior environment variables before startup commands run."""

        env_output: list[SerializedResultMapping] = []
        if not env:
            return env_output

        for var_name, var_value in env.items():
            escaped_value = var_value.replace("\\", "\\\\").replace('"', '\\"')
            env_cmd = f"set environment {var_name} {escaped_value}"
            result = self._command_runner.execute_command_result(
                env_cmd, timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            env_output.append(cast(SerializedResultMapping, result_to_mapping(result)))

            if isinstance(result, OperationError):
                self._cleanup_failed_start(
                    f"Failed to set environment variable {var_name}: {result.message}"
                )
                return OperationError(
                    message=f"Failed to set environment variable {var_name}: {result.message}",
                    fatal=result.fatal,
                    details={"env_output": payload_to_mapping(env_output)},
                )

        return env_output

    @staticmethod
    def _loads_target(command: str) -> bool:
        """Return whether a startup command explicitly loads an executable or core."""

        return command.startswith("file ") or command.startswith("core-file ")

    @staticmethod
    def _startup_output_text(startup_result: ParsedMiResponse) -> str:
        """Join all textual startup streams into one searchable string."""

        streams: list[str] = []
        streams.extend(item for item in startup_result.console if isinstance(item, str))
        streams.extend(item for item in startup_result.log if isinstance(item, str))
        streams.extend(item for item in startup_result.output if isinstance(item, str))
        return "".join(streams)

    @staticmethod
    def _startup_target_loaded(
        *,
        program: str | None,
        core: str | None,
        startup_output: str,
    ) -> bool:
        """Infer whether startup actually loaded the requested target."""

        if not program and not core:
            return False

        failure_markers = (
            "no such file or directory",
            "not in executable format",
            "file format not recognized",
            "is not a core dump",
            "is not a core file",
            "no executable file now",
        )
        return not any(marker in startup_output for marker in failure_markers)

    def _probe_target_loaded(self) -> bool | OperationError:
        """Probe whether GDB currently has an executable or core file loaded."""

        result = self._command_runner.execute_command_result(
            "info files", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            logger.debug("Unable to refresh target_loaded from GDB: %s", result.message)
            return result

        output = (result.value.output or "").strip()
        if not output or output == "(no output)":
            return False

        output_lower = output.lower()
        if "no executable file now" in output_lower:
            return False

        return True
