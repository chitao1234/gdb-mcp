"""Unit tests for the daemon-backed stdin line source."""

from __future__ import annotations

import asyncio
import os

import pytest

from gdb_mcp.mcp.stdio_input import DaemonStdinLines


def test_daemon_stdin_lines_decodes_lines_and_ends_on_eof() -> None:
    read_fd, write_fd = os.pipe()
    source = DaemonStdinLines(read_fd)

    async def collect() -> list[str]:
        return [line async for line in source]

    os.write(write_fd, b"one\ntwo")
    os.close(write_fd)

    assert asyncio.run(asyncio.wait_for(collect(), timeout=5)) == ["one\n", "two"]
    os.close(read_fd)


def test_blocked_read_is_cancelled_promptly() -> None:
    read_fd, write_fd = os.pipe()
    source = DaemonStdinLines(read_fd)

    async def scenario() -> None:
        pending = asyncio.create_task(source.__anext__())
        await asyncio.sleep(0)  # let the reader thread block on the pipe

        assert source._thread is not None
        assert source._thread.daemon is True

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=2)

    asyncio.run(scenario())
    os.close(write_fd)
    os.close(read_fd)
