"""Thin smoke tests for the compatibility server entrypoint."""

from __future__ import annotations

import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestServerEntrypoint:
    """Keep minimal coverage for the thin compatibility entrypoint."""

    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_defaults_to_stdio_transport(self, mock_create_default_runtime):
        """main() should keep stdio as the zero-argument default transport."""

        from gdb_mcp.server import main

        runtime = Mock()
        runtime.run_stdio = AsyncMock(return_value=None)
        runtime.run_streamable_http = AsyncMock(return_value=None)
        mock_create_default_runtime.return_value = runtime

        asyncio.run(main([]))

        mock_create_default_runtime.assert_called_once_with()
        runtime.run_stdio.assert_awaited_once_with()
        runtime.run_streamable_http.assert_not_called()

    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_selects_streamable_http_transport(self, mock_create_default_runtime):
        """main() should route explicit HTTP transport flags to the HTTP runner."""

        from gdb_mcp.server import main

        runtime = Mock()
        runtime.run_stdio = AsyncMock(return_value=None)
        runtime.run_streamable_http = AsyncMock(return_value=None)
        mock_create_default_runtime.return_value = runtime

        asyncio.run(
            main(
                [
                    "--transport",
                    "streamable-http",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9000",
                    "--path",
                    "/debug",
                ]
            )
        )

        runtime.run_streamable_http.assert_awaited_once_with(
            host="0.0.0.0",
            port=9000,
            path="/debug",
        )
        runtime.run_stdio.assert_not_called()

    def test_parse_server_config_rejects_http_flags_for_stdio(self):
        """HTTP-only flags should be rejected when stdio remains selected."""

        from gdb_mcp.server import parse_server_config

        with pytest.raises(SystemExit) as exc_info:
            parse_server_config(["--port", "9000"])

        assert exc_info.value.code == 2

    def test_parse_server_config_rejects_non_rooted_http_path(self):
        """HTTP paths should be validated before the runtime starts."""

        from gdb_mcp.server import parse_server_config

        with pytest.raises(SystemExit) as exc_info:
            parse_server_config(["--transport", "streamable-http", "--path", "mcp"])

        assert exc_info.value.code == 2

    def test_parse_server_config_defaults_streamable_http_settings(self):
        """HTTP transport should normalize omitted host, port, and path values."""

        from gdb_mcp.server import parse_server_config

        config = parse_server_config(["--transport", "streamable-http"])

        assert config.transport == "streamable-http"
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.path == "/mcp"

    def test_parse_server_config_preserves_explicit_port_zero(self):
        """A caller should be able to request an ephemeral HTTP port with --port 0."""

        from gdb_mcp.server import parse_server_config

        config = parse_server_config(["--transport", "streamable-http", "--port", "0"])

        assert config.transport == "streamable-http"
        assert config.port == 0

    @pytest.mark.parametrize("port", ["-1", "70000"])
    def test_parse_server_config_rejects_invalid_http_ports(self, port):
        """HTTP port validation should reject values outside the valid TCP range."""

        from gdb_mcp.server import parse_server_config

        with pytest.raises(SystemExit) as exc_info:
            parse_server_config(["--transport", "streamable-http", "--port", port])

        assert exc_info.value.code == 2

    def test_import_does_not_configure_logging(self):
        """Importing the compatibility module should not mutate host logging setup."""

        import gdb_mcp.server as server

        with patch.object(logging, "basicConfig") as mock_basic_config:
            importlib.reload(server)

        assert mock_basic_config.call_count == 0

    @patch("gdb_mcp.server.create_default_runtime")
    def test_main_builds_a_fresh_runtime_for_each_invocation(self, mock_create_default_runtime):
        """Repeated main() calls should not keep a module-global runtime cache."""

        from gdb_mcp.server import main

        runtime_one = Mock()
        runtime_one.run_stdio = AsyncMock(return_value=None)
        runtime_one.run_streamable_http = AsyncMock(return_value=None)
        runtime_two = Mock()
        runtime_two.run_stdio = AsyncMock(return_value=None)
        runtime_two.run_streamable_http = AsyncMock(return_value=None)
        mock_create_default_runtime.side_effect = [runtime_one, runtime_two]

        asyncio.run(main([]))
        asyncio.run(main([]))

        assert mock_create_default_runtime.call_count == 2
        runtime_one.run_stdio.assert_awaited_once_with()
        runtime_two.run_stdio.assert_awaited_once_with()

    @patch("gdb_mcp.server.asyncio.run")
    @patch("gdb_mcp.server.main", new_callable=Mock)
    @patch("gdb_mcp.server.configure_logging")
    @patch("gdb_mcp.server.logger.warning")
    def test_run_server_warns_when_module_is_loaded_from_build_lib(
        self,
        mock_warning,
        _mock_configure_logging,
        mock_main,
        mock_asyncio_run,
    ):
        """CLI startup should flag potentially stale build/lib shadow imports."""

        import gdb_mcp.server as server

        mock_main.return_value = "main-coroutine"

        with patch.object(server, "__file__", "/tmp/repo/build/lib/gdb_mcp/server.py"):
            server.run_server()

        _mock_configure_logging.assert_called_once_with()
        mock_warning.assert_called_once()
        mock_main.assert_called_once_with(None)
        mock_asyncio_run.assert_called_once_with("main-coroutine")
