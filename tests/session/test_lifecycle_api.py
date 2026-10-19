"""Lifecycle-focused session API tests."""

from __future__ import annotations

import os
from typing import cast
from unittest.mock import MagicMock, Mock, patch

from gdb_mcp.domain import (
    CommandExecutionInfo,
    OperationError,
    OperationResult,
    result_to_mapping,
)
from gdb_mcp.session.service import SessionService
from tests.support import sequence, text

from .conftest import CommandResultBuilder, StartHarness


class TestLifecycleApi:
    """Test session lifecycle behavior through the public API."""

    def test_get_status_no_session(self, session_service: SessionService) -> None:
        """Status should reflect an idle session before startup."""

        status = result_to_mapping(session_service.get_status())

        assert status["is_running"] is False
        assert status["target_loaded"] is False
        assert status["has_controller"] is False
        assert status["execution_state"] == "unknown"
        assert status["stop_reason"] is None
        assert status["exit_code"] is None

    def test_get_status_marks_dead_gdb_process_inactive(
        self, running_session: SessionService
    ) -> None:
        """Status should reconcile against a dead GDB child process."""

        with patch.object(running_session._command_runner, "is_gdb_alive", return_value=False):
            status = result_to_mapping(running_session.get_status())

        assert status["status"] == "success"
        assert status["is_running"] is False
        assert status["target_loaded"] is False
        assert status["has_controller"] is False
        assert status["execution_state"] == "unknown"
        assert running_session.controller is None
        assert running_session.state.value == "failed"

    def test_stop_no_session(self, session_service: SessionService) -> None:
        """Stopping without a controller should return a user-facing error."""

        result = result_to_mapping(session_service.stop())

        assert result["status"] == "error"
        assert "No active session" in text(result["message"])

    def test_start_session_already_running(self, session_service: SessionService) -> None:
        """Starting twice should be rejected before touching transport."""

        session_service.runtime.controller = Mock()

        result = result_to_mapping(session_service.start(program="/bin/ls"))

        assert result["status"] == "error"
        assert "already running" in text(result["message"]).lower()

    def test_start_session_basic(self, start_session: StartHarness) -> None:
        """Basic startup should attach a controller and expose program info."""

        result = start_session(
            program="/bin/ls",
            console=["Reading symbols from /bin/ls...\n"],
        )

        assert result["status"] == "success"
        assert result["program"] == "/bin/ls"
        assert result["target_loaded"] is True
        assert result["execution_state"] == "not_started"
        assert start_session.service.is_running is True

    def test_start_session_with_custom_gdb_path(self, start_session: StartHarness) -> None:
        """Explicit gdb_path should be forwarded into process startup."""

        result = start_session(program="/bin/ls", gdb_path="/usr/local/bin/gdb-custom")
        command = start_session.command_of()

        assert command[0] == "/usr/local/bin/gdb-custom"
        assert "--interpreter=mi" in command
        assert result["status"] == "success"

    def test_start_session_with_program_and_core_uses_explicit_exec_and_core_flags(
        self, start_session: StartHarness
    ) -> None:
        """Core-dump startup should use explicit GDB options instead of argv passthrough."""

        result = start_session(program="/bin/ls", core="/tmp/core.123")
        command = start_session.command_of()

        assert result["status"] == "success"
        assert result["execution_state"] == "paused"
        assert "--se=/bin/ls" in command
        assert "--core=/tmp/core.123" in command
        assert "--args" not in command

    def test_start_session_with_rr_replay_spawns_rr(self, start_session: StartHarness) -> None:
        """An rr replay should start 'rr replay --interpreter=mi' on the trace."""

        result = start_session(rr_replay="/traces/app-0")
        command = start_session.command_of()

        assert result["status"] == "success"
        assert result["rr_replay"] == "/traces/app-0"
        assert command[:3] == ["rr", "replay", "--interpreter=mi"]
        assert command[-1] == "/traces/app-0"
        assert "-d" in command

    def test_start_session_passes_gdb_args_before_the_program(
        self, start_session: StartHarness
    ) -> None:
        """gdb_args must reach GDB's command line ahead of the program."""

        result = start_session(
            program="/bin/ls",
            gdb_args=["--directory=/tmp/pp", "-iex", "add-auto-load-safe-path /tmp/pp"],
        )

        assert result["status"] == "success"
        assert start_session.command_of() == [
            "gdb",
            "--quiet",
            "--interpreter=mi",
            "--directory=/tmp/pp",
            "-iex",
            "add-auto-load-safe-path /tmp/pp",
            "/bin/ls",
        ]

    def test_start_session_forwards_gdb_args_to_rr_replay(
        self, start_session: StartHarness
    ) -> None:
        """rr replay receives gdb_args after '--', so its gdb gets them."""

        result = start_session(
            rr_replay="/traces/app-0",
            gdb_args=["-iex", "set pagination off"],
        )
        command = start_session.command_of()

        assert result["status"] == "success"
        assert command[:5] == ["rr", "replay", "--interpreter=mi", "-d", "gdb"]
        assert command[5:7] == ["/traces/app-0", "--"]
        assert command[7:] == ["-iex", "set pagination off"]

    @patch.dict(os.environ, {"RR_PATH": "/opt/rr/bin/rr"})
    def test_start_session_with_rr_replay_honors_rr_path(self, start_session: StartHarness) -> None:
        """RR_PATH should choose the rr binary, like GDB_PATH chooses gdb."""

        start_session(rr_replay="/traces/app-0")

        assert start_session.command_of()[0] == "/opt/rr/bin/rr"

    def test_start_session_ignores_program_with_rr_replay(
        self, start_session: StartHarness
    ) -> None:
        """An rr trace owns the target, so a program is ignored with a warning."""

        result = start_session(rr_replay="/traces/app-0", program="/bin/ls")

        assert result["status"] == "success"
        assert result["program"] is None
        assert any("ignored" in text(warning).lower() for warning in sequence(result["warnings"]))
        assert start_session.service.config is not None
        assert start_session.service.config.program is None

    def test_start_session_rejects_rr_replay_with_args(
        self, session_service: SessionService
    ) -> None:
        """Inferior argv cannot be replayed, so it stays a hard rejection."""

        result = result_to_mapping(session_service.start(rr_replay="/traces/app-0", args=["-l"]))

        assert result["status"] == "error"
        assert "rr trace already owns the target" in text(result["message"]).lower()
        assert session_service.state.value == "failed"

    @patch.dict(os.environ, {"GDB_PATH": "/custom/path/to/gdb"})
    def test_start_session_with_gdb_path_env_var(self, start_session: StartHarness) -> None:
        """GDB_PATH should be used when gdb_path is not provided."""

        result = start_session(program="/bin/ls")
        command = start_session.command_of()

        assert command[0] == "/custom/path/to/gdb"
        assert "--interpreter=mi" in command
        assert result["status"] == "success"

    @patch.dict(os.environ, {"GDB_PATH": "/env/gdb"})
    def test_start_session_explicit_path_overrides_env_var(
        self, start_session: StartHarness
    ) -> None:
        """An explicit gdb_path should override GDB_PATH."""

        result = start_session(program="/bin/ls", gdb_path="/explicit/gdb")
        command = start_session.command_of()

        assert command[0] == "/explicit/gdb"
        assert command[0] != "/env/gdb"
        assert result["status"] == "success"

    def test_start_session_with_env_variables(self, start_session: StartHarness) -> None:
        """Environment variables should be translated into init commands."""

        result = start_session(
            program="/bin/ls",
            env={"DEBUG_MODE": "1", "LOG_LEVEL": "verbose"},
        )

        env_commands = [
            command for command in start_session.executed if "set environment" in command
        ]
        assert result["status"] == "success"
        assert len(env_commands) == 2
        assert any("DEBUG_MODE" in command for command in env_commands)
        assert any("LOG_LEVEL" in command for command in env_commands)

    def test_start_session_applies_environment_before_init_commands(
        self, start_session: StartHarness
    ) -> None:
        """Inferior environment should be set before init commands can run the target."""

        result = start_session(
            program="/bin/ls",
            env={"DEBUG_MODE": "1"},
            init_commands=["set pagination off", "run"],
        )

        assert result["status"] == "success"
        assert start_session.executed == [
            "set confirm off",
            "set width 0",
            "set pagination off",
            "set environment DEBUG_MODE 1",
            "set pagination off",
            "run",
        ]

    def test_start_session_passes_environment_values_verbatim(
        self, start_session: StartHarness
    ) -> None:
        """Environment values must reach GDB unchanged, with no extra escape layer."""

        result = start_session(program="/bin/ls", env={"MSG": 'a\\b"c'})

        assert result["status"] == "success"
        assert 'set environment MSG a\\b"c' in start_session.executed

    def test_start_session_rejects_environment_value_with_newline(
        self, start_session: StartHarness
    ) -> None:
        """A newline in an environment value would inject a command, so reject it."""

        result = start_session(program="/bin/ls", env={"MSG": "line1\nline2"})

        assert result["status"] == "error"
        assert "must not contain newlines" in text(result["message"])
        assert not any(command.startswith("set environment") for command in start_session.executed)

    def test_start_session_fails_when_env_setup_fails(
        self,
        start_session: StartHarness,
        command_result: CommandResultBuilder,
    ) -> None:
        """Environment setup failures should fail startup instead of reporting READY."""

        def fail_environment(
            command: str, **kwargs: object
        ) -> OperationResult[CommandExecutionInfo]:
            del kwargs
            if command.startswith("set environment "):
                return OperationError(message="Permission denied for environment setup")
            return command_result(command, output="")

        result = start_session(
            program="/bin/ls",
            env={"DEBUG_MODE": "1"},
            execute=fail_environment,
        )

        assert result["status"] == "error"
        assert "environment" in text(result["message"]).lower()

    def test_start_session_rejects_args_with_core(self, session_service: SessionService) -> None:
        """Core analysis and inferior argv are mutually exclusive at startup."""

        result = result_to_mapping(
            session_service.start(
                program="/bin/ls",
                args=["-l"],
                core="/tmp/core.123",
            )
        )

        assert result["status"] == "error"
        assert "cannot combine 'args' with 'core'" in text(result["message"]).lower()
        assert session_service.state.value == "failed"
        assert session_service.config is not None
        assert session_service.config.args == ("-l",)
        assert session_service.config.core == "/tmp/core.123"

    def test_start_session_detects_missing_debug_symbols(self, start_session: StartHarness) -> None:
        """Startup warnings should surface missing debug symbols."""

        result = start_session(
            program="/bin/ls",
            console=[
                "Reading symbols from /bin/ls...\n",
                "(no debugging symbols found)...done.\n",
            ],
        )

        assert result["status"] == "success"
        assert "warnings" in result
        assert any(
            "not compiled with -g" in text(warning) for warning in sequence(result["warnings"])
        )

    def test_start_session_warns_when_gdb_declines_auto_load(
        self, start_session: StartHarness
    ) -> None:
        """A refused auto-load script must reach warnings, not just startup output."""

        result = start_session(
            program="/tmp/go-app",
            console=[
                "Reading symbols from /tmp/go-app...\n",
                (
                    'warning: File "/usr/share/go-1.22/src/runtime/runtime-gdb.py"'
                    " auto-loading has been declined by your `auto-load safe-path'"
                    ' set to "$debugdir:$datadir/auto-load".\n'
                    "To enable execution of this file add\n"
                    "\tadd-auto-load-safe-path"
                    " /usr/share/go-1.22/src/runtime/runtime-gdb.py\n"
                ),
            ],
        )

        warnings = [text(warning) for warning in sequence(result["warnings"])]

        assert result["status"] == "success"
        assert any("runtime-gdb.py" in warning for warning in warnings)
        assert any("init_commands run afterwards" in warning for warning in warnings)

    def test_start_session_warns_when_auto_load_script_is_missing(
        self, start_session: StartHarness
    ) -> None:
        """A script GDB cannot locate is also reported, naming the object file."""

        result = start_session(
            program="/tmp/rust-app",
            console=[
                (
                    "warning: Missing auto-load script at offset 0 in section"
                    " .debug_gdb_scripts\nof file /tmp/rust-app.\n"
                ),
            ],
        )

        warnings = [text(warning) for warning in sequence(result["warnings"])]

        assert result["status"] == "success"
        assert any(
            "/tmp/rust-app" in warning and ".debug_gdb_scripts" in warning for warning in warnings
        )
        assert any("rust-gdb" in warning for warning in warnings)

    def test_start_session_missing_program_leaves_target_unloaded(
        self, start_session: StartHarness
    ) -> None:
        """Missing startup targets should not report target_loaded=true."""

        result = start_session(
            program="/missing/app",
            initial_output=[
                {"type": "log", "payload": "/missing/app: No such file or directory.\n"}
            ],
            probe_target_loaded=False,
        )

        status = result_to_mapping(start_session.service.get_status())

        assert result["status"] == "success"
        assert result["target_loaded"] is False
        assert "warnings" in result
        assert "Program file not found" in [text(item) for item in sequence(result["warnings"])]
        assert status["target_loaded"] is False

    def test_start_session_refreshes_target_loaded_after_init_commands(
        self, start_session: StartHarness
    ) -> None:
        """Final startup target state should be refreshed from GDB after init commands."""

        result = start_session(init_commands=["source setup.gdb"])

        assert result["status"] == "success"
        assert result["target_loaded"] is True
        assert start_session.executed == [
            "set confirm off",
            "set width 0",
            "set pagination off",
            "source setup.gdb",
        ]

    def test_start_session_warns_when_target_loaded_refresh_fails(
        self,
        start_session: StartHarness,
        command_result: CommandResultBuilder,
    ) -> None:
        """Startup should keep the last known target_loaded value but surface probe failure."""

        result = start_session(
            program="/bin/ls",
            initial_output=[{"type": "console", "payload": "Reading symbols from /bin/ls...\n"}],
            probe_target_loaded=None,
            execute=[
                command_result("set confirm off", output=""),
                command_result("set width 0", output=""),
                command_result("set pagination off", output=""),
                OperationError(message="info files failed"),
            ],
        )

        assert result["status"] == "success"
        assert result["target_loaded"] is True
        assert "warnings" in result
        assert any(
            warning
            == "Could not refresh target_loaded from GDB after startup commands: info files failed"
            for warning in sequence(result["warnings"])
        )

    def test_stop_active_session(self, running_session: SessionService) -> None:
        """Stopping an active session should clear the controller and running flag."""

        result = result_to_mapping(running_session.stop())

        assert result["status"] == "success"
        assert running_session.controller is None
        assert running_session.is_running is False

    def test_stop_failure_marks_session_failed_and_not_running(
        self, running_session: SessionService
    ) -> None:
        """Shutdown failures should not leave the session marked as running."""

        # running_session installs a MagicMock controller (see conftest).
        cast(MagicMock, running_session.controller).exit.side_effect = RuntimeError("exit failed")

        result = result_to_mapping(running_session.stop())

        assert result["status"] == "error"
        assert "exit failed" in text(result["message"])
        assert running_session.controller is None
        assert running_session.is_running is False
        assert running_session.state.value == "failed"
        assert running_session.runtime.last_failure_message is not None
