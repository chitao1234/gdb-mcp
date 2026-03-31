"""Thin smoke tests for the compatibility server entrypoint."""

from __future__ import annotations

import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, Mock, patch


class TestServerEntrypoint:
    """Keep minimal coverage for the thin compatibility entrypoint."""

    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_delegates_to_a_fresh_runtime(self, mock_create_default_runtime):
        """main() should delegate to a newly constructed runtime."""

        from gdb_mcp.server import main

        runtime = Mock()
        runtime.main = AsyncMock(return_value=None)
        mock_create_default_runtime.return_value = runtime

        asyncio.run(main())

        mock_create_default_runtime.assert_called_once_with()
        runtime.main.assert_awaited_once_with()

    def test_import_does_not_configure_logging(self):
        """Importing the compatibility module should not mutate host logging setup."""

        import gdb_mcp.server as server

        with patch.object(logging, "basicConfig") as mock_basic_config:
            importlib.reload(server)

        assert mock_basic_config.call_count == 0

    @patch("gdb_mcp.server.create_default_runtime")
    @patch("gdb_mcp.server.configure_logging")
    def test_run_server_builds_a_fresh_runtime_for_each_invocation(
        self,
        mock_configure_logging,
        mock_create_default_runtime,
    ):
        """The CLI entrypoint should not keep a module-global runtime cache."""

        from gdb_mcp.server import run_server

        runtime_one = Mock()
        runtime_two = Mock()
        mock_create_default_runtime.side_effect = [runtime_one, runtime_two]

        run_server()
        run_server()

        assert mock_configure_logging.call_count == 2
        assert mock_create_default_runtime.call_count == 2
        runtime_one.run_server.assert_called_once_with()
        runtime_two.run_server.assert_called_once_with()

    @patch("gdb_mcp.server.create_default_runtime")
    @patch("gdb_mcp.server.configure_logging")
    @patch("gdb_mcp.server.logger.warning")
    def test_run_server_warns_when_module_is_loaded_from_build_lib(
        self,
        mock_warning,
        _mock_configure_logging,
        mock_create_default_runtime,
    ):
        """CLI startup should flag potentially stale build/lib shadow imports."""

        import gdb_mcp.server as server

        runtime = Mock()
        mock_create_default_runtime.return_value = runtime

        with patch.object(server, "__file__", "/tmp/repo/build/lib/gdb_mcp/server.py"):
            server.run_server()

        mock_warning.assert_called_once()
        runtime.run_server.assert_called_once_with()
