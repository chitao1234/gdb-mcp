"""Thin smoke tests for the compatibility server entrypoint."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, Mock, patch


class TestServerEntrypoint:
    """Keep minimal coverage for the thin compatibility entrypoint."""

    @patch("gdb_mcp.server.get_runtime")
    def test_call_tool_delegates_to_runtime(self, mock_get_runtime):
        """The server entrypoint should delegate tool calls to the runtime."""

        from gdb_mcp.server import call_tool

        runtime = Mock()
        runtime.call_tool = AsyncMock(return_value=["payload"])
        mock_get_runtime.return_value = runtime

        result = asyncio.run(call_tool("gdb_get_status", {"session_id": 1}))

        assert result == ["payload"]
        runtime.call_tool.assert_awaited_once_with("gdb_get_status", {"session_id": 1})

    def test_get_runtime_initializes_once_under_concurrency(self):
        """Concurrent first access should not create multiple default runtimes."""

        import gdb_mcp.server as server

        original_runtime = server._runtime
        server._runtime = None
        created: list[object] = []

        def make_runtime():
            time.sleep(0.05)
            runtime = object()
            created.append(runtime)
            return runtime

        results: list[object] = []

        with patch.object(server, "create_default_runtime", side_effect=make_runtime):
            threads = [threading.Thread(target=lambda: results.append(server.get_runtime())) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        try:
            assert len(created) == 1
            assert results[0] is results[1]
        finally:
            server._runtime = original_runtime
