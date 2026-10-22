"""Lifecycle coordination for a composed SessionService."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import TypeAlias

from ..contracts import DEBUGGER_GDB, DEBUGGER_NAMES
from ..domain import (
    BackendData,
    OperationError,
    OperationSuccess,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
    StartupDiagnostics,
    StructuredPayload,
    payload_to_mapping,
    result_to_envelope,
)
from ..domain.errors import DebuggerDecodeError
from ..transport import (
    EndpointError,
    MiClient,
    MiTransportError,
    ParsedMiResponse,
    create_external_controller,
    parse_mi_responses,
)
from .command_runner import SessionCommandRunner
from .config import SessionConfig
from .constants import DEFAULT_TIMEOUT_SEC, FILE_LOAD_TIMEOUT_SEC, INIT_COMMAND_DELAY_SEC
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)

SerializedResultMapping: TypeAlias = StructuredPayload

_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_VALUE_CONTROL_RE = re.compile(r"[\r\n]")

# GDB loads the program during its own startup, so a script it auto-loads from
# that program's debug info is refused or missing before this server can send a
# single command. Both messages therefore only ever appear in the startup text.
_AUTO_LOAD_DECLINED_RE = re.compile(
    r'warning: File "(?P<path>[^"]+)" auto-loading has been declined'
    r" by your `auto-load safe-path'",
    re.IGNORECASE,
)
_AUTO_LOAD_MISSING_RE = re.compile(
    r"missing auto-load script at offset 0 in section \.debug_gdb_scripts\s+"
    r"of file (?P<file>.+?)\.",
    re.IGNORECASE | re.DOTALL,
)


def _auto_load_warnings(startup_output: str) -> list[str]:
    """Return warnings for auto-load scripts GDB refused or could not find.

    Language support ships as GDB scripts (Rust pretty-printers, the Go runtime
    helpers, a kernel's ``vmlinux-gdb.py``), and GDB refuses to run them unless
    their directory is trusted. Silently losing them turns into confusing
    symptoms later, such as a ``Vec`` printed as raw ``RawVec`` internals, so
    surface the refusal where the caller actually looks.
    """

    warnings: list[str] = []
    for match in _AUTO_LOAD_DECLINED_RE.finditer(startup_output):
        script = match.group("path")
        warnings.append(
            f"GDB declined to auto-load {script}: its directory is not on auto-load "
            "safe-path. Auto-load runs while GDB loads the program, so pass the option on "
            "the GDB command line with debugger_args (for example ['-iex', "
            "'add-auto-load-safe-path <dir>']); init_commands run afterwards and are too "
            "late."
        )
    for match in _AUTO_LOAD_MISSING_RE.finditer(startup_output):
        warnings.append(
            f"GDB could not find the auto-load script referenced by {match.group('file')} "
            "(.debug_gdb_scripts), so its pretty-printers and extensions stay inactive. For "
            "a Rust binary, start the session with debugger_path='rust-gdb' (the rustup wrapper), "
            "which both points GDB at the script directory and trusts it; otherwise pass "
            "debugger_args=['--directory=<dir>', '-iex', 'add-auto-load-safe-path <dir>']."
        )
    return warnings


def _environment_validation_error(var_name: str, var_value: str) -> str | None:
    """Return a message when one environment variable cannot be set safely.

    Values are passed to ``set environment`` verbatim, so a newline in either
    field would let a caller append an extra GDB command. Names must also be a
    plain identifier because GDB splits the line on the first whitespace.
    """

    if _ENV_NAME_RE.fullmatch(var_name) is None:
        return (
            f"Invalid environment variable name {var_name!r}: "
            "names must match [A-Za-z_][A-Za-z0-9_]*"
        )
    if _ENV_VALUE_CONTROL_RE.search(var_value) is not None:
        return f"Environment variable {var_name} value must not contain newlines"
    return None


class SessionLifecycleService:
    """Lifecycle orchestration backed by explicit runtime state."""

    def __init__(
        self,
        runtime: SessionRuntime,
        command_runner: SessionCommandRunner,
        transport: MiClient,
    ):
        self._runtime = runtime
        self._command_runner = command_runner
        self._transport = transport

    def start(
        self,
        config: SessionConfig,
    ) -> OperationSuccess[SessionStartupInfo] | OperationError:
        """Start a new GDB session.

        When ``endpoint`` is given the session connects to a GDB/MI stream that
        another process started (``tcp://``, ``unix://`` or ``pty://``) instead
        of spawning its own ``gdb``. When ``rr_replay`` is given the session
        spawns ``rr replay --interpreter=mi`` on that trace instead of a plain
        ``gdb``.
        """
        with self._runtime.lifecycle_lock:
            if self._runtime.has_controller:
                return OperationError(message="Session already running. Stop it first.")

            program = config.program
            debugger = config.debugger
            args = config.args
            init_commands = config.init_commands
            env = config.env
            debugger_path = config.debugger_path
            if debugger_path is None:
                debugger_path = self._runtime.os_module.environ.get("GDB_PATH", "gdb")
                config = replace(config, debugger_path=debugger_path)
            debugger_args = config.debugger_args
            working_dir = config.working_dir
            core = config.core
            endpoint = config.endpoint
            replay = config.rr_replay

            startup_warnings: list[str] = []
            if replay and program:
                startup_warnings.append(
                    f"Program '{program}' was ignored: the rr trace already owns the target."
                )
                program = None
                config = replace(config, program=None)

            self._runtime.begin_startup(config)

            try:
                if debugger not in DEBUGGER_NAMES:
                    supported = ", ".join(DEBUGGER_NAMES)
                    message = f"Unsupported debugger {debugger!r}: supported values are {supported}"
                    self._runtime.mark_failed(message)
                    return OperationError(message=message, code="unsupported_combination")
                if replay and (args or core):
                    message = (
                        "An rr trace already owns the target; do not also pass args or core "
                        "when replaying a recording."
                    )
                    self._runtime.mark_failed(message)
                    return OperationError(message=message)

                if replay and endpoint:
                    message = (
                        "An rr replay starts its own debugger; do not also pass endpoint, "
                        "which expects an externally started GDB/MI stream."
                    )
                    self._runtime.mark_failed(message)
                    return OperationError(message=message)

                if endpoint and (program or args or core):
                    message = (
                        "An external endpoint already owns the target; do not also pass "
                        "program, args, or core when connecting."
                    )
                    self._runtime.mark_failed(message)
                    return OperationError(message=message)

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

                if core:
                    logger.info("Loading core dump: %s", core)

                if endpoint:
                    logger.info("Connecting to external GDB/MI endpoint: %s", endpoint)
                    external_controller = create_external_controller(
                        endpoint,
                        time_to_check_for_additional_output_sec=1.0,
                    )
                    self._transport.adopt_controller(external_controller)
                    self._mirror_controller()
                else:
                    gdb_command = self._build_start_command(
                        debugger_path=debugger_path,
                        program=program,
                        args=args,
                        core=core,
                        rr_replay=replay,
                        rr_path=self._runtime.os_module.environ.get("RR_PATH", "rr"),
                        debugger_args=debugger_args,
                    )
                    self._transport.start(
                        command=gdb_command,
                        time_to_check_for_additional_output_sec=1.0,
                        cwd=working_dir,
                    )
                    self._mirror_controller()

                initial_startup_responses = self._transport.read_initial_output(timeout_sec=1.0)

                logger.debug("Waiting for GDB initialization to complete...")
                ready_check = self._command_runner.send_command_and_wait_for_prompt(
                    "-gdb-version", timeout_sec=DEFAULT_TIMEOUT_SEC
                )

                if ready_check.error is not None or ready_check.timed_out:
                    error_msg = (
                        ready_check.error
                        if ready_check.error is not None
                        else "Timeout waiting for GDB to initialize"
                    )
                    logger.error("GDB failed to initialize: %s", error_msg)
                    if self._runtime.has_controller:
                        try:
                            self._transport.exit()
                        except Exception as exc:  # pragma: no cover - best effort
                            logger.debug("Ignoring failure while aborting GDB: %s", exc)
                        finally:
                            self._mirror_controller()
                    message = f"GDB failed to initialize: {error_msg}"
                    self._runtime.mark_failed(message)
                    return OperationError(
                        message=message,
                        fatal=ready_check.fatal,
                    )

                logger.info("GDB initialized and ready")

                startup_responses = list(initial_startup_responses)
                startup_responses.extend(ready_check.command_responses)
                startup_result = parse_mi_responses(startup_responses)
                startup_output_text = self._startup_output_text(startup_result)

                warnings: list[str] = list(startup_warnings)
                startup_output_lower = startup_output_text.lower()
                if "no debugging symbols found" in startup_output_lower:
                    warnings.append("No debugging symbols found - program was not compiled with -g")
                if "not in executable format" in startup_output_lower:
                    warnings.append("File is not an executable")
                if "no such file" in startup_output_lower:
                    warnings.append("Program file not found")
                warnings.extend(_auto_load_warnings(startup_output_text))

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

                # Older GDB wraps CLI output at the terminal width (80 columns),
                # which splits the messages this server parses; `set width 0`
                # removes the limit so one reply stays one line.
                width_result = self._command_runner.execute_command_result(
                    "set width 0",
                    timeout_sec=DEFAULT_TIMEOUT_SEC,
                )
                if isinstance(width_result, OperationError):
                    warnings.append(
                        "Failed to disable CLI line wrapping by default: " f"{width_result.message}"
                    )

                # An externally started GDB on a pty still pages long CLI output,
                # which stalls the `info files` probe until the pager prompt is
                # answered.
                pagination_result = self._command_runner.execute_command_result(
                    "set pagination off",
                    timeout_sec=DEFAULT_TIMEOUT_SEC,
                )
                if isinstance(pagination_result, OperationError):
                    warnings.append(
                        "Failed to disable pagination by default: " f"{pagination_result.message}"
                    )

                self._runtime.target_loaded = self._startup_target_loaded(
                    program=program,
                    core=core,
                    startup_output=startup_output_lower,
                )

                startup_backend_data = BackendData(
                    debugger=DEBUGGER_GDB,
                    console_output=self._startup_console_text(startup_result),
                    data=self._startup_raw_data(startup_result),
                )
                env_output = self._apply_environment(env, startup_backend_data)
                if isinstance(env_output, OperationError):
                    return env_output

                initialization_results: list[SerializedResultMapping] = []
                if init_commands:
                    for cmd in init_commands:
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
                        initialization_results.append(result_to_envelope(result))

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
                            self._cleanup_failed_start(f"Init command '{cmd}' failed: {error_msg}")
                            return OperationError(
                                message=f"Init command '{cmd}' failed: {error_msg}",
                                fatal=result.fatal,
                                details={
                                    "diagnostics": payload_to_mapping(
                                        StartupDiagnostics(
                                            environment_results=env_output,
                                            initialization_results=initialization_results,
                                            backend_data=startup_backend_data,
                                        )
                                    )
                                },
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
                        for command in init_commands
                    ):
                        self._runtime.mark_inferior_paused("core-file")
                    elif self._runtime.target_loaded:
                        self._runtime.mark_inferior_not_started()
                self._runtime.mark_ready()

                return OperationSuccess(
                    SessionStartupInfo(
                        message="GDB session started",
                        program=program,
                        core=core,
                        target_loaded=self._runtime.target_loaded,
                        execution_state=self._runtime.execution_state,
                        stop_reason=self._runtime.stop_reason,
                        stop_kind=self._runtime.stop_kind,
                        exit_code=self._runtime.exit_code,
                        diagnostics=StartupDiagnostics(
                            environment_results=env_output,
                            initialization_results=initialization_results,
                            backend_data=startup_backend_data,
                        ),
                        endpoint=endpoint,
                        rr_replay=replay,
                    ),
                    warnings=tuple(warnings),
                )

            except EndpointError as exc:
                logger.error("Failed to connect to GDB/MI endpoint: %s", exc)
                self._cleanup_failed_start(str(exc))
                return OperationError(message=str(exc))

            except MiTransportError as exc:
                logger.error("Failed to start GDB session: %s", exc)
                self._cleanup_failed_start(str(exc))
                return OperationError(message=str(exc), code="not_started")

            except DebuggerDecodeError:
                self._cleanup_failed_start("GDB returned malformed debugger data")
                raise

            except Exception as exc:
                logger.exception("Failed to start GDB session")
                self._cleanup_failed_start(f"Failed to start GDB: {exc!s}")
                raise

    def stop(self) -> OperationSuccess[SessionMessage] | OperationError:
        """Stop the GDB session."""
        with self._runtime.lifecycle_lock:
            if not self._runtime.has_controller:
                return OperationError(message="No active session")

            try:
                self._transport.exit()
            except Exception as exc:
                self._mirror_controller()
                logger.error("Failed to stop GDB session: %s", exc)
                self._runtime.mark_failed(f"Failed to stop GDB session: {exc}")
                return OperationError(message=str(exc))

            self._mirror_controller()
            self._runtime.mark_stopped()
            return OperationSuccess(SessionMessage(message="GDB session stopped"))

    def get_status(self) -> OperationSuccess[SessionStatusSnapshot]:
        """Get the current status of the GDB session."""
        if self._runtime.has_controller and not self._command_runner.is_gdb_alive():
            self._runtime.mark_transport_terminated(
                "GDB process has exited - session is no longer active"
            )

        snapshot = SessionStatusSnapshot(
            debugger_alive=self._runtime.is_running,
            target_loaded=self._runtime.target_loaded,
            session_state=self._runtime.state,
            execution_state=self._runtime.execution_state,
            state_detail=(
                self._runtime.last_stop_event.state_detail
                if self._runtime.execution_state == "paused"
                and self._runtime.last_stop_event is not None
                and self._runtime.last_stop_event.reason == self._runtime.stop_reason
                else None
            ),
            stop_reason=self._runtime.stop_reason,
            stop_kind=self._runtime.stop_kind,
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
                self._transport.exit()
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("Ignoring failure while cleaning up a failed start: %s", exc)
            finally:
                self._mirror_controller()
        self._runtime.mark_failed(message)

    def _mirror_controller(self) -> None:
        """Publish the transport's controller on the shared runtime state."""

        self._runtime.controller = self._transport.controller

    @staticmethod
    def _build_start_command(
        *,
        debugger_path: str,
        program: str | None,
        args: tuple[str, ...],
        core: str | None,
        rr_replay: str | None = None,
        rr_path: str = "rr",
        debugger_args: tuple[str, ...] = (),
    ) -> list[str]:
        """Build a GDB startup argv for the supported launch modes.

        ``debugger_args`` carries caller-supplied GDB options. They are inserted before
        the program, because GDB loads a target given on its command line during
        startup, so anything that has to apply before that (auto-load safe-path,
        search directories) can only arrive this way.
        """

        extra_args = list(debugger_args)

        if rr_replay:
            # rr replays the trace itself, so it owns the target and chooses the
            # gdb that speaks MI to us. Everything after `--` goes to that gdb.
            command = [rr_path, "replay", "--interpreter=mi", "-d", debugger_path, rr_replay]
            if extra_args:
                command.extend(["--", *extra_args])
            return command

        command = [debugger_path, "--quiet", "--interpreter=mi", *extra_args]
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
        self, env: dict[str, str], startup_backend_data: BackendData
    ) -> list[SerializedResultMapping] | OperationError:
        """Apply inferior environment variables before startup commands run."""

        env_output: list[SerializedResultMapping] = []
        if not env:
            return env_output

        for var_name, var_value in env.items():
            validation_error = _environment_validation_error(var_name, var_value)
            if validation_error is not None:
                self._cleanup_failed_start(validation_error)
                return OperationError(
                    message=validation_error,
                    details={
                        "diagnostics": payload_to_mapping(
                            StartupDiagnostics(
                                environment_results=env_output,
                                initialization_results=[],
                                backend_data=startup_backend_data,
                            )
                        )
                    },
                )

            env_cmd = f"set environment {var_name} {var_value}"
            result = self._command_runner.execute_command_result(
                env_cmd, timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            env_output.append(result_to_envelope(result))

            if isinstance(result, OperationError):
                self._cleanup_failed_start(
                    f"Failed to set environment variable {var_name}: {result.message}"
                )
                return OperationError(
                    message=f"Failed to set environment variable {var_name}: {result.message}",
                    fatal=result.fatal,
                    details={
                        "diagnostics": payload_to_mapping(
                            StartupDiagnostics(
                                environment_results=env_output,
                                initialization_results=[],
                                backend_data=startup_backend_data,
                            )
                        )
                    },
                )

        return env_output

    @staticmethod
    def _loads_target(command: str) -> bool:
        """Return whether a startup command explicitly loads an executable or core."""

        return command.startswith(("file ", "core-file "))

    @staticmethod
    def _startup_output_text(startup_result: ParsedMiResponse) -> str:
        """Join all textual startup streams into one searchable string."""

        streams: list[str] = []
        streams.extend(item for item in startup_result.console if isinstance(item, str))
        streams.extend(item for item in startup_result.log if isinstance(item, str))
        streams.extend(item for item in startup_result.output if isinstance(item, str))
        return "".join(streams)

    @staticmethod
    def _startup_console_text(startup_result: ParsedMiResponse) -> str:
        """Join only GDB's console stream for the public console output field."""

        return "".join(item for item in startup_result.console if isinstance(item, str)).strip()

    @staticmethod
    def _startup_raw_data(startup_result: ParsedMiResponse) -> StructuredPayload:
        """Keep non-console MI records in the tagged backend payload."""

        return payload_to_mapping(
            {
                "log": startup_result.log,
                "output": startup_result.output,
                "result": startup_result.result,
                "result_class": startup_result.result_class,
                "notify": startup_result.notify,
            }
        )

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

        output = result.value.backend_data.console_output.strip()
        if not output or output == "(no output)":
            return False

        output_lower = output.lower()
        return "no executable file now" not in output_lower
