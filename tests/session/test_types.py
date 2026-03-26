"""Tests for the new session config/state types."""

from unittest.mock import MagicMock, patch

from gdb_mcp.gdb_interface import GDBSession
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.state import SessionState


class TestSessionConfig:
    """Test normalized session configuration helpers."""

    def test_from_inputs_normalizes_mutable_values(self):
        """Lists and dicts should be copied into immutable/canonical shapes."""

        args = ["--flag", "value"]
        init_commands = ["set pagination off"]
        env = {"DEBUG": "1"}

        config = SessionConfig.from_inputs(
            program="/bin/ls",
            args=args,
            init_commands=init_commands,
            env=env,
            gdb_path="/usr/bin/gdb",
            working_dir="/tmp",
            core="/tmp/core",
        )

        args.append("--mutated")
        init_commands.append("set confirm off")
        env["EXTRA"] = "2"

        assert config.program == "/bin/ls"
        assert config.args == ("--flag", "value")
        assert config.init_commands == ("set pagination off",)
        assert config.env == {"DEBUG": "1"}
        assert config.gdb_path == "/usr/bin/gdb"
        assert config.working_dir == "/tmp"
        assert config.core == "/tmp/core"


class TestGDBSessionState:
    """Test that GDBSession tracks the new explicit lifecycle state."""

    def test_initial_state_is_created(self):
        """Fresh sessions should start in CREATED state without config."""

        session = GDBSession()

        assert session.state is SessionState.CREATED
        assert session.config is None

    @patch("gdb_mcp.gdb_interface.GdbController")
    def test_start_success_sets_config_and_ready_state(self, mock_controller_class):
        """Successful startup should populate config and move to READY."""

        mock_controller_class.return_value = MagicMock()
        session = GDBSession()

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = session.start(program="/bin/ls", args=["-l"])

        assert result["status"] == "success"
        assert session.state is SessionState.READY
        assert session.config is not None
        assert session.config.program == "/bin/ls"
        assert session.config.args == ("-l",)

    @patch("gdb_mcp.gdb_interface.os.chdir")
    @patch("gdb_mcp.gdb_interface.os.path.isdir", return_value=True)
    @patch("gdb_mcp.gdb_interface.GdbController")
    def test_start_with_working_dir_does_not_change_process_cwd(
        self,
        mock_controller_class,
        mock_isdir,
        mock_chdir,
    ):
        """The GDBSession compatibility wrapper should forward cwd into process startup."""

        mock_controller_class.return_value = MagicMock()
        session = GDBSession()

        with patch.object(
            session,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = session.start(program="/bin/ls", working_dir="/tmp/work")

        assert result["status"] == "success"
        mock_controller_class.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi", "/bin/ls"],
            time_to_check_for_additional_output_sec=1.0,
            cwd="/tmp/work",
        )
        mock_isdir.assert_called_once_with("/tmp/work")
        mock_chdir.assert_not_called()

    def test_start_failure_sets_failed_state_and_config(self):
        """Validation failures during startup should still record attempted config."""

        session = GDBSession()

        result = session.start(program="/bin/ls", working_dir="/definitely/missing")

        assert result["status"] == "error"
        assert session.state is SessionState.FAILED
        assert session.config is not None
        assert session.config.working_dir == "/definitely/missing"

    def test_stop_success_sets_stopped_state(self):
        """Stopping an active session should move it to STOPPED."""

        session = GDBSession()
        session.controller = MagicMock()
        session.is_running = True
        session.state = SessionState.READY

        result = session.stop()

        assert result["status"] == "success"
        assert session.state is SessionState.STOPPED
