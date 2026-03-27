"""Unit tests for the injected server runtime."""

import asyncio
import json
import logging
from unittest.mock import Mock

from gdb_mcp.domain import OperationSuccess, SessionMessage, SessionStatusSnapshot
from gdb_mcp.mcp.runtime import create_server_runtime


class TestServerRuntime:
    """Test runtime-level dependency injection."""

    def test_runtime_routes_through_injected_session_manager(self):
        """The runtime should dispatch tool calls using the injected registry provider."""

        mock_manager = Mock()
        mock_session = Mock()
        mock_session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        mock_manager.resolve_session.return_value = mock_session

        runtime = create_server_runtime(
            session_manager_provider=lambda: mock_manager,
            logger=logging.getLogger("test-runtime"),
        )

        result = asyncio.run(runtime.call_tool("gdb_get_status", {"session_id": 7}))
        result_data = json.loads(result[0].text)

        assert result_data == {
            "status": "success",
            "is_running": False,
            "target_loaded": False,
            "has_controller": True,
            "execution_state": "unknown",
            "stop_reason": None,
            "exit_code": None,
        }
        mock_manager.resolve_session.assert_called_once_with(7)
        mock_session.get_status.assert_called_once()

    def test_runtime_shutdown_uses_injected_session_manager(self):
        """Shutdown should call through the injected registry provider."""

        mock_manager = Mock()
        mock_manager.shutdown_all.return_value = {
            1: OperationSuccess(SessionMessage(message="stopped"))
        }

        runtime = create_server_runtime(
            session_manager_provider=lambda: mock_manager,
            logger=logging.getLogger("test-runtime"),
        )

        runtime.shutdown_sessions()

        mock_manager.shutdown_all.assert_called_once()
