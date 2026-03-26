"""Session service for orchestrating debugger operations."""

from __future__ import annotations

import logging
import signal
from typing import Any, Optional

from ..domain.models import SessionStatusSnapshot
from ..transport import (
    MiClient,
    extract_mi_result_payload,
    is_cli_command,
    parse_mi_responses,
    wrap_cli_command,
)
from .config import SessionConfig
from .state import SessionState

logger = logging.getLogger(__name__)

# Timeout constants (in seconds)
DEFAULT_TIMEOUT_SEC = 30
FILE_LOAD_TIMEOUT_SEC = 300  # 5 minutes for loading core/executable files
INTERRUPT_RESPONSE_TIMEOUT_SEC = 2
POLL_TIMEOUT_SEC = 0.1
INIT_COMMAND_DELAY_SEC = 0.5

# Other constants
INITIAL_COMMAND_TOKEN = 1000
DEFAULT_MAX_BACKTRACE_FRAMES = 100


class SessionService:
    """
    Orchestrates one debugger session on top of the transport layer.

    This service owns session lifecycle state, configuration, and debugger-facing
    operations, while delegating low-level command I/O to `MiClient`.
    """

    def __init__(
        self,
        *,
        controller_factory: Any,
        os_module: Any,
        time_module: Any,
        initial_command_token: int = INITIAL_COMMAND_TOKEN,
        poll_timeout_sec: float = POLL_TIMEOUT_SEC,
    ):
        self._transport = MiClient(
            controller_factory=controller_factory,
            initial_command_token=initial_command_token,
            poll_timeout_sec=poll_timeout_sec,
        )
        self._os = os_module
        self._time = time_module
        self.is_running = False
        self.target_loaded = False
        self.original_cwd: Optional[str] = None
        self.config: Optional[SessionConfig] = None
        self.state = SessionState.CREATED

    @property
    def controller(self) -> Any:
        """Expose the underlying controller for compatibility and testing."""

        return self._transport.controller

    @controller.setter
    def controller(self, value: Any) -> None:
        """Allow tests and wrappers to replace the underlying controller."""

        self._transport.controller = value

    def start(
        self,
        program: Optional[str] = None,
        args: Optional[list[str]] = None,
        init_commands: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        gdb_path: Optional[str] = None,
        working_dir: Optional[str] = None,
        core: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a new GDB session."""
        if self.controller:
            return {"status": "error", "message": "Session already running. Stop it first."}

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

        if working_dir:
            self.original_cwd = self._os.getcwd()

        try:
            if working_dir:
                if not self._os.path.isdir(working_dir):
                    self.state = SessionState.FAILED
                    return {
                        "status": "error",
                        "message": f"Working directory does not exist: {working_dir}",
                    }
                self._os.chdir(working_dir)
                logger.info("Changed working directory to: %s", working_dir)

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
                error_response: dict[str, Any] = {
                    "status": "error",
                    "message": f"GDB failed to initialize: {error_msg}",
                }
                if ready_check.get("fatal"):
                    error_response["fatal"] = True
                self.state = SessionState.FAILED
                return error_response

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

                        result = self.execute_command(cmd, timeout_sec=timeout)
                        init_output.append(result)

                        if "core-file" in cmd.lower():
                            self._time.sleep(INIT_COMMAND_DELAY_SEC)
                            logger.debug("Waiting for GDB to stabilize after core-file command")

                        if result.get("status") == "error":
                            error_msg = result.get("message", "Unknown error")
                            logger.error("Init command '%s' failed: %s", cmd, error_msg)

                            if (
                                result.get("fatal")
                                or "GDB process" in error_msg
                                or not self._is_gdb_alive()
                            ):
                                logger.error("GDB process died during init commands")
                                error_response = {
                                    "status": "error",
                                    "message": f"GDB crashed during init command '{cmd}': {error_msg}",
                                    "init_output": init_output,
                                }
                                if result.get("fatal"):
                                    error_response["fatal"] = True
                                self.state = SessionState.FAILED
                                return error_response

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
                            return {
                                "status": "error",
                                "message": f"GDB crashed during init command '{cmd}': {str(exc)}",
                                "init_output": init_output,
                            }

            env_output = []
            if env:
                for var_name, var_value in env.items():
                    escaped_value = var_value.replace("\\", "\\\\").replace('"', '\\"')
                    env_cmd = f"set environment {var_name} {escaped_value}"
                    result = self.execute_command(env_cmd)
                    env_output.append(result)

            if program or core:
                self.target_loaded = True

            self.is_running = True
            self.state = SessionState.READY

            final_result: dict[str, Any] = {"status": "success", "message": "GDB session started"}
            if program:
                final_result["program"] = program
            if core:
                final_result["core"] = core
            if startup_console.strip():
                final_result["startup_output"] = startup_console.strip()
            if warnings:
                final_result["warnings"] = warnings
            if env_output:
                final_result["env_output"] = env_output
            if init_output:
                final_result["init_output"] = init_output

            return final_result

        except Exception as exc:
            logger.error("Failed to start GDB session: %s", exc)
            if self.controller:
                try:
                    self.controller.exit()
                except Exception:
                    pass
                self.controller = None
            if self.original_cwd:
                self._os.chdir(self.original_cwd)
                logger.info("Restored working directory after failed start: %s", self.original_cwd)
                self.original_cwd = None
            self.state = SessionState.FAILED
            return {"status": "error", "message": f"Failed to start GDB: {str(exc)}"}

    def _is_gdb_alive(self) -> bool:
        """Check if the GDB process is still running."""
        return self._transport.is_alive()

    def _send_command_and_wait_for_prompt(
        self, command: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC
    ) -> dict[str, Any]:
        """Send a command through the transport and normalize fatal cleanup."""
        result = self._transport.send_command_and_wait_for_prompt(command, timeout_sec=timeout_sec)

        if result.fatal:
            self.is_running = False
            self.target_loaded = False
            self.state = SessionState.FAILED

            if self.original_cwd:
                try:
                    self._os.chdir(self.original_cwd)
                    logger.info(
                        "Restored working directory after fatal error: %s", self.original_cwd
                    )
                except Exception as exc:
                    logger.warning("Failed to restore working directory: %s", exc)
                self.original_cwd = None

        return result.to_dict()

    def execute_command(
        self, command: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> dict[str, Any]:
        """Execute a GDB command and return the parsed response."""
        if not self.controller:
            return {"status": "error", "message": "No active GDB session"}

        if not self._is_gdb_alive():
            logger.error("GDB process is not running when trying to execute: %s", command)
            return {
                "status": "error",
                "message": "GDB process has exited - cannot execute command",
                "command": command,
            }

        cli_command = is_cli_command(command)
        actual_command = command

        if cli_command:
            actual_command = wrap_cli_command(command)
            logger.debug("Wrapping CLI command: %s -> %s", command, actual_command)

        result = self._send_command_and_wait_for_prompt(actual_command, timeout_sec)

        if "error" in result:
            error_response = {
                "status": "error",
                "message": result["error"],
                "command": command,
            }
            if result.get("fatal"):
                error_response["fatal"] = True
            return error_response

        if result.get("timed_out"):
            return {
                "status": "error",
                "message": f"Timeout waiting for command response after {timeout_sec}s",
                "command": command,
            }

        command_responses = result.get("command_responses", [])
        parsed = parse_mi_responses(command_responses)

        if cli_command:
            console_output = "".join(parsed.console)
            return {
                "status": "success",
                "command": command,
                "output": console_output.strip() if console_output else "(no output)",
            }

        return {"status": "success", "command": command, "result": parsed.to_dict()}

    def get_threads(self) -> dict[str, Any]:
        """Get information about all threads in the debugged process."""
        logger.debug("get_threads() called")
        result = self.execute_command("-thread-info")
        logger.debug("get_threads: execute_command returned: %s", result)

        if result["status"] == "error":
            logger.debug("get_threads: returning error from execute_command")
            return result

        thread_info = extract_mi_result_payload(result)
        logger.debug("get_threads: thread_info type=%s, value=%s", type(thread_info), thread_info)

        if thread_info is None:
            logger.warning("get_threads: thread_info is None - GDB returned incomplete data")
            return {
                "status": "error",
                "message": "GDB returned incomplete data - may still be loading symbols",
            }

        if not isinstance(thread_info, dict):
            thread_info = {}
        threads = thread_info.get("threads", [])
        current_thread = thread_info.get("current-thread-id")
        logger.debug(
            "get_threads: found %s threads, current_thread_id=%s", len(threads), current_thread
        )
        logger.debug("get_threads: threads data: %s", threads)

        return {
            "status": "success",
            "threads": threads,
            "current_thread_id": current_thread,
            "count": len(threads),
        }

    def select_thread(self, thread_id: int) -> dict[str, Any]:
        """Select a specific thread to make it the current thread."""
        result = self.execute_command(f"-thread-select {thread_id}")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}

        return {
            "status": "success",
            "thread_id": thread_id,
            "new_thread_id": mi_result.get("new-thread-id"),
            "frame": mi_result.get("frame"),
        }

    def get_backtrace(
        self, thread_id: Optional[int] = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> dict[str, Any]:
        """Get the stack backtrace for a specific thread or the current thread."""
        if thread_id is not None:
            switch_result = self.execute_command(f"-thread-select {thread_id}")
            if switch_result["status"] == "error":
                return switch_result

        result = self.execute_command(f"-stack-list-frames 0 {max_frames}")

        if result["status"] == "error":
            return result

        stack_data = extract_mi_result_payload(result) or {}
        frames = stack_data.get("stack", [])

        return {"status": "success", "thread_id": thread_id, "frames": frames, "count": len(frames)}

    def get_frame_info(self) -> dict[str, Any]:
        """Get information about the current stack frame."""
        result = self.execute_command("-stack-info-frame")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        frame = mi_result.get("frame", {})

        return {"status": "success", "frame": frame}

    def select_frame(self, frame_number: int) -> dict[str, Any]:
        """Select a specific stack frame to make it the current frame."""
        result = self.execute_command(f"-stack-select-frame {frame_number}")

        if result["status"] == "error":
            return result

        frame_info_result = self.execute_command("-stack-info-frame")

        if frame_info_result["status"] == "error":
            return {
                "status": "success",
                "frame_number": frame_number,
                "message": f"Frame {frame_number} selected",
            }

        mi_result = extract_mi_result_payload(frame_info_result) or {}
        frame_info = mi_result.get("frame", {})

        return {
            "status": "success",
            "frame_number": frame_number,
            "frame": frame_info,
        }

    def set_breakpoint(
        self, location: str, condition: Optional[str] = None, temporary: bool = False
    ) -> dict[str, Any]:
        """Set a breakpoint at the specified location."""
        cmd_parts = ["-break-insert"]

        if temporary:
            cmd_parts.append("-t")

        if condition:
            escaped_condition = condition.replace("\\", "\\\\").replace('"', '\\"')
            cmd_parts.extend(["-c", f'"{escaped_condition}"'])

        cmd_parts.append(location)

        result = self.execute_command(" ".join(cmd_parts))

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result)
        logger.debug("Breakpoint MI result: %s", mi_result)

        if mi_result is None:
            logger.warning("No MI result for breakpoint at %s", location)
            return {
                "status": "error",
                "message": f"Failed to set breakpoint at {location}: no result from GDB",
                "raw_result": result,
            }

        bp_info = mi_result if isinstance(mi_result, dict) else {}
        breakpoint = bp_info.get("bkpt", bp_info)

        if not breakpoint:
            logger.warning("Empty breakpoint result for %s: %s", location, mi_result)
            return {
                "status": "error",
                "message": f"Breakpoint set but no info returned for {location}",
                "raw_result": result,
            }

        return {"status": "success", "breakpoint": breakpoint}

    def list_breakpoints(self) -> dict[str, Any]:
        """List all breakpoints with structured data."""
        result = self.execute_command("-break-list")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        bp_table = mi_result.get("BreakpointTable", {})
        breakpoints = bp_table.get("body", [])

        return {"status": "success", "breakpoints": breakpoints, "count": len(breakpoints)}

    def delete_breakpoint(self, number: int) -> dict[str, Any]:
        """Delete a breakpoint by its number."""
        result = self.execute_command(f"-break-delete {number}")

        if result["status"] == "error":
            return result

        return {"status": "success", "message": f"Breakpoint {number} deleted"}

    def enable_breakpoint(self, number: int) -> dict[str, Any]:
        """Enable a breakpoint by its number."""
        result = self.execute_command(f"-break-enable {number}")

        if result["status"] == "error":
            return result

        return {"status": "success", "message": f"Breakpoint {number} enabled"}

    def disable_breakpoint(self, number: int) -> dict[str, Any]:
        """Disable a breakpoint by its number."""
        result = self.execute_command(f"-break-disable {number}")

        if result["status"] == "error":
            return result

        return {"status": "success", "message": f"Breakpoint {number} disabled"}

    def run(self, args: Optional[list[str]] = None) -> dict[str, Any]:
        """Run the program."""
        if not self.controller:
            return {"status": "error", "message": "No active GDB session"}

        if args:
            arg_str = " ".join(args)
            result = self.execute_command(f"-exec-arguments {arg_str}")
            if result.get("status") == "error":
                return result

        return self.execute_command("-exec-run")

    def continue_execution(self) -> dict[str, Any]:
        """Continue execution of the program."""
        return self.execute_command("-exec-continue")

    def step(self) -> dict[str, Any]:
        """Step into the next source line."""
        return self.execute_command("-exec-step")

    def next(self) -> dict[str, Any]:
        """Step over the next source line."""
        return self.execute_command("-exec-next")

    def interrupt(self) -> dict[str, Any]:
        """Interrupt (pause) a running program."""
        if not self.controller:
            return {"status": "error", "message": "No active GDB session"}

        if not self.controller.gdb_process:
            return {"status": "error", "message": "No GDB process running"}

        try:
            self._os.kill(self.controller.gdb_process.pid, signal.SIGINT)

            start_time = self._time.time()
            all_responses: list[dict[str, Any]] = []
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
                return {
                    "status": "warning",
                    "message": "Interrupt sent but no stopped notification received",
                    "result": result,
                }

            return {
                "status": "success",
                "message": "Program interrupted (paused)",
                "result": result,
            }
        except Exception as exc:
            logger.error("Failed to interrupt program: %s", exc)
            return {"status": "error", "message": f"Failed to interrupt: {str(exc)}"}

    def evaluate_expression(self, expression: str) -> dict[str, Any]:
        """Evaluate an expression in the current context."""
        result = self.execute_command(f'-data-evaluate-expression "{expression}"')

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        value = mi_result.get("value")

        return {"status": "success", "expression": expression, "value": value}

    def get_variables(self, thread_id: Optional[int] = None, frame: int = 0) -> dict[str, Any]:
        """Get local variables for a specific frame."""
        if thread_id is not None:
            thread_result = self.execute_command(f"-thread-select {thread_id}")
            if thread_result.get("status") == "error":
                return thread_result

        frame_result = self.execute_command(f"-stack-select-frame {frame}")
        if frame_result.get("status") == "error":
            return frame_result

        result = self.execute_command("-stack-list-variables --simple-values")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        variables = mi_result.get("variables", [])

        return {"status": "success", "thread_id": thread_id, "frame": frame, "variables": variables}

    def get_registers(self) -> dict[str, Any]:
        """Get register values for current frame."""
        result = self.execute_command("-data-list-register-values x")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        registers = mi_result.get("register-values", [])

        return {"status": "success", "registers": registers}

    def stop(self) -> dict[str, Any]:
        """Stop the GDB session."""
        if not self.controller:
            return {"status": "error", "message": "No active session"}

        try:
            self.controller.exit()
            self.controller = None
            self.is_running = False
            self.target_loaded = False
            self.state = SessionState.STOPPED

            if self.original_cwd:
                self._os.chdir(self.original_cwd)
                logger.info("Restored working directory to: %s", self.original_cwd)
                self.original_cwd = None

            return {"status": "success", "message": "GDB session stopped"}

        except Exception as exc:
            logger.error("Failed to stop GDB session: %s", exc)
            if self.original_cwd:
                try:
                    self._os.chdir(self.original_cwd)
                    logger.info("Restored working directory after error: %s", self.original_cwd)
                    self.original_cwd = None
                except Exception as cwd_error:
                    logger.warning("Failed to restore working directory: %s", cwd_error)
            return {"status": "error", "message": str(exc)}

    def get_status(self) -> dict[str, Any]:
        """Get the current status of the GDB session."""
        snapshot = SessionStatusSnapshot(
            is_running=self.is_running,
            target_loaded=self.target_loaded,
            has_controller=self.controller is not None,
        )
        return {
            "is_running": snapshot.is_running,
            "target_loaded": snapshot.target_loaded,
            "has_controller": snapshot.has_controller,
        }

    def call_function(
        self, function_call: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ) -> dict[str, Any]:
        """Call a function in the target process."""
        if not self.controller:
            return {"status": "error", "message": "No active GDB session"}

        if not self._is_gdb_alive():
            return {
                "status": "error",
                "message": "GDB process has exited - cannot execute call",
            }

        command = f"call {function_call}"
        mi_command = wrap_cli_command(command)

        result = self._send_command_and_wait_for_prompt(mi_command, timeout_sec)

        if "error" in result:
            return {
                "status": "error",
                "message": result["error"],
                "function_call": function_call,
            }

        if result.get("timed_out"):
            return {
                "status": "error",
                "message": f"Timeout waiting for call to complete after {timeout_sec}s",
                "function_call": function_call,
            }

        parsed = parse_mi_responses(result.get("command_responses", []))
        console_output = "".join(parsed.console)

        return {
            "status": "success",
            "function_call": function_call,
            "result": console_output.strip() if console_output else "(no return value)",
        }
