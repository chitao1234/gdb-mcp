"""Unit tests for the injected server runtime."""

import asyncio
import json
import logging
from unittest.mock import MagicMock, Mock

from gdb_mcp.domain import OperationSuccess, SessionMessage, SessionStatusSnapshot
from gdb_mcp.mcp.runtime import create_server_runtime


def _session_double() -> Mock:
    """Create a runtime test double that satisfies the workflow-lock contract."""

    session = Mock()
    workflow_lock = MagicMock()
    workflow_lock.__enter__.return_value = None
    workflow_lock.__exit__.return_value = None
    session.runtime = Mock(workflow_lock=workflow_lock)
    return session


class TestServerRuntime:
    """Test runtime-level dependency injection."""

    def test_runtime_dispatches_v2_status_query(self):
        """The runtime should dispatch v2 status queries using the injected registry provider."""

        mock_manager = Mock()
        mock_session = _session_double()
        mock_session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        mock_manager.resolve_session.return_value = mock_session

        runtime = create_server_runtime(
            session_manager_provider=lambda: mock_manager,
            logger=logging.getLogger("test-runtime"),
        )

        result = asyncio.run(
            runtime.call_tool(
                "gdb_session_query",
                {"session_id": 7, "action": "status", "query": {}},
            )
        )
        result_data = json.loads(result[0].text)

        assert result_data["status"] == "success"
        assert result_data["action"] == "status"
        assert result_data["result"]["is_running"] is False
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
