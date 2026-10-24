"""Unit tests for the daemon-backed stdin line source."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from gdb_mcp.mcp.stdio_input import DaemonStdinLines


@pytest.fixture
def pipe_fds() -> Iterator[tuple[int, int]]:
    """Provide a connected ``(read, write)`` pipe closed after the test."""

    read_fd, write_fd = os.pipe()
    try:
        yield read_fd, write_fd
    finally:
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def test_daemon_stdin_lines_decodes_lines_and_ends_on_eof(
    pipe_fds: tuple[int, int],
) -> None:
    """Lines are decoded until EOF, preserving the trailing newline."""

    read_fd, write_fd = pipe_fds
    source = DaemonStdinLines(read_fd)

    async def collect() -> list[str]:
        return [line async for line in source]

    os.write(write_fd, b"one\ntwo")
    os.close(write_fd)

    assert asyncio.run(asyncio.wait_for(collect(), timeout=5)) == ["one\n", "two"]


def test_blocked_read_is_cancelled_promptly(pipe_fds: tuple[int, int]) -> None:
    """Cancelling a read blocked on the pipe must not leak the reader thread."""

    read_fd, write_fd = pipe_fds
    source = DaemonStdinLines(read_fd)

    async def scenario() -> None:
        pending = asyncio.create_task(source.__anext__())
        await asyncio.sleep(0)  # let the reader thread block on the pipe

        assert source._thread is not None
        assert source._thread.daemon is True

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=2)

    try:
        asyncio.run(scenario())
    finally:
        # Windows ``_close`` takes the descriptor's lock, which a read blocked in
        # another thread already holds; signal EOF so the reader exits first.
        os.close(write_fd)
        thread = source._thread
        if thread is not None:
            thread.join(timeout=5)
