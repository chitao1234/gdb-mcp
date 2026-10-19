"""Unit tests for the injected server runtime."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import pytest
from mcp.types import TextContent

from gdb_mcp.domain import OperationSuccess, SessionStatusSnapshot
from gdb_mcp.mcp.runtime import ServerRuntime, create_server_runtime


def _session_double() -> Mock:
    """Create a runtime test double that satisfies the workflow-lock contract."""

    session = Mock()
    workflow_lock = MagicMock()
    workflow_lock.__enter__.return_value = None
    workflow_lock.__exit__.return_value = None
    session.runtime = Mock(workflow_lock=workflow_lock)
    return session


@pytest.fixture
def runtime_with_manager() -> tuple[ServerRuntime, Mock]:
    """Build a runtime wired to a fresh mock session manager."""

    mock_manager = Mock()
    mock_manager.shutdown_all.return_value = {}
    runtime = create_server_runtime(
        session_manager_provider=lambda: mock_manager,
        logger=logging.getLogger("test-runtime"),
    )
    return runtime, mock_manager


class TestServerRuntime:
    """Test runtime-level dependency injection."""

    def test_runtime_dispatches_v2_status_query(
        self, runtime_with_manager: tuple[ServerRuntime, Mock]
    ) -> None:
        """The runtime should dispatch v2 status queries using the injected registry provider."""

        runtime, mock_manager = runtime_with_manager
        mock_session = _session_double()
        mock_session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=True)
        )
        mock_manager.resolve_session.return_value = mock_session

        result = asyncio.run(
            runtime.call_tool(
                "gdb_session_query",
                {"session_id": 7, "action": "status", "query": {}},
            )
        )
        content = result.content[0]
        assert isinstance(content, TextContent)
        result_data = json.loads(content.text)

        assert result_data["status"] == "success"
        assert result_data["action"] == "status"
        assert result_data["result"]["is_running"] is False
        mock_manager.resolve_session.assert_called_once_with(7)
        mock_session.get_status.assert_called_once()

    def test_runtime_shutdown_uses_injected_session_manager(
        self, runtime_with_manager: tuple[ServerRuntime, Mock]
    ) -> None:
        """Shutdown should call through the injected registry provider."""

        runtime, mock_manager = runtime_with_manager

        runtime.shutdown_sessions()

        mock_manager.shutdown_all.assert_called_once()

    @patch("gdb_mcp.mcp.runtime.run_stdio_app", new_callable=AsyncMock)
    def test_run_stdio_delegates_to_stdio_helper(
        self, mock_run_stdio_app: AsyncMock, runtime_with_manager: tuple[ServerRuntime, Mock]
    ) -> None:
        """The explicit stdio runner should delegate through the stdio helper."""

        runtime, _mock_manager = runtime_with_manager

        asyncio.run(runtime.run_stdio())

        mock_run_stdio_app.assert_awaited_once_with(
            runtime.app,
            startup_message="GDB MCP Server starting...",
            on_shutdown=runtime.shutdown_sessions,
        )

    @patch("gdb_mcp.mcp.runtime.run_streamable_http_app", new_callable=AsyncMock)
    def test_run_streamable_http_delegates_to_http_helper(
        self,
        mock_run_streamable_http_app: AsyncMock,
        runtime_with_manager: tuple[ServerRuntime, Mock],
    ) -> None:
        """The explicit HTTP runner should delegate through the HTTP helper."""

        runtime, _mock_manager = runtime_with_manager

        asyncio.run(
            runtime.run_streamable_http(
                host="127.0.0.1",
                port=8000,
                path="/mcp",
            )
        )

        mock_run_streamable_http_app.assert_awaited_once_with(
            runtime.app,
            host="127.0.0.1",
            port=8000,
            path="/mcp",
            startup_message="GDB MCP Server starting...",
            on_shutdown=runtime.shutdown_sessions,
            auth_token=None,
            ready_file=None,
            idle_timeout_sec=0.0,
            has_active_sessions=ANY,
        )

    @patch("gdb_mcp.mcp.runtime.asyncio.run")
    @patch.object(ServerRuntime, "main", return_value="main-coroutine")
    def test_run_server_delegates_through_main(
        self,
        mock_main: Mock,
        mock_asyncio_run: Mock,
        runtime_with_manager: tuple[ServerRuntime, Mock],
    ) -> None:
        """The synchronous entrypoint should keep main() as the compatibility alias."""

        runtime, _mock_manager = runtime_with_manager

        runtime.run_server()

        mock_main.assert_called_once_with()
        mock_asyncio_run.assert_called_once()
        coroutine = mock_asyncio_run.call_args.args[0]
        assert asyncio.iscoroutine(coroutine)
        coroutine.close()
