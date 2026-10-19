"""Shared fixtures for MCP-layer tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdb_mcp.client.daemon import ResolvedServer

from .cli_support import MANAGED_TOKEN, SERVER_URL


@pytest.fixture
def managed_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ResolvedServer:
    """Point the CLI at a managed background server rooted at ``tmp_path``."""

    monkeypatch.setenv("GDB_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return ResolvedServer(
        url=SERVER_URL,
        token=MANAGED_TOKEN,
        spawned=False,
        managed=True,
    )
