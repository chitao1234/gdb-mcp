"""Unit tests for the extracted SessionService layer."""

from unittest.mock import MagicMock, patch

from gdb_mcp.session.service import SessionService, SessionState


class TestSessionService:
    """Basic tests for the new session service boundary."""

    def test_initialization_uses_injected_dependencies(self):
        """SessionService should start with no controller and CREATED state."""

        service = SessionService(
            controller_factory=MagicMock(),
            os_module=MagicMock(),
            time_module=MagicMock(),
        )

        assert service.controller is None
        assert service.state is SessionState.CREATED
        assert service.config is None

    def test_start_success_uses_injected_controller_factory(self):
        """Startup should use the injected controller factory rather than importing one directly."""

        controller_factory = MagicMock(return_value=MagicMock())
        fake_os = MagicMock()
        fake_os.environ.get.return_value = "gdb"

        service = SessionService(
            controller_factory=controller_factory,
            os_module=fake_os,
            time_module=MagicMock(),
        )

        with patch.object(
            service,
            "_send_command_and_wait_for_prompt",
            return_value={
                "command_responses": [{"type": "result", "message": "done", "token": 1000}],
                "async_notifications": [],
                "timed_out": False,
            },
        ):
            result = service.start(program="/bin/ls")

        assert result["status"] == "success"
        assert service.state is SessionState.READY
        controller_factory.assert_called_once_with(
            command=["gdb", "--quiet", "--interpreter=mi", "/bin/ls"],
            time_to_check_for_additional_output_sec=1.0,
        )
