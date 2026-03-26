"""Thin smoke tests for the compatibility server entrypoint."""

from __future__ import annotations

import asyncio
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
