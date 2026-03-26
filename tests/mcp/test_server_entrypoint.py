"""Thin smoke tests for the compatibility server entrypoint."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


class TestServerEntrypoint:
    """Keep minimal coverage for the thin compatibility entrypoint."""

    @patch("gdb_mcp.server.runtime.call_tool", new_callable=AsyncMock)
    def test_call_tool_delegates_to_runtime(self, mock_call_tool):
        """The server entrypoint should delegate tool calls to the runtime."""

        from gdb_mcp.server import call_tool

        mock_call_tool.return_value = ["payload"]

        result = asyncio.run(call_tool("gdb_get_status", {"session_id": 1}))

        assert result == ["payload"]
        mock_call_tool.assert_awaited_once_with("gdb_get_status", {"session_id": 1})
