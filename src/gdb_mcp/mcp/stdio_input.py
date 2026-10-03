"""Cancellable, shutdown-safe stdin line source for the stdio transport.

The MCP SDK reads stdin through anyio's worker-thread pool
(``wrap_file`` -> ``to_thread.run_sync(readline)``); those reads are not
cancellable, so a Ctrl-C during shutdown waits for a thread blocked in
``read(0)`` and the server hangs. Feeding the transport our own reader fixes
that; reading a raw file descriptor instead of a Python file object keeps the
daemon thread from ever holding an ``io`` lock, which would otherwise make
interpreter shutdown abort with ``_enter_buffered_busy``.
"""

from __future__ import annotations

import asyncio
import os
import threading


class DaemonStdinLines:
    """Async line iterator over a raw file descriptor using a daemon thread."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._queue: asyncio.Queue[str | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def __aiter__(self) -> "DaemonStdinLines":
        return self

    async def __anext__(self) -> str:
        if self._queue is None:
            self._queue = asyncio.Queue()
            self._loop = asyncio.get_running_loop()
            self._thread = threading.Thread(target=self._pump, name="mcp-stdin", daemon=True)
            self._thread.start()

        line = await self._queue.get()
        if line is None:
            raise StopAsyncIteration
        return line

    def _emit(self, line: str | None) -> bool:
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None:  # pragma: no cover - defensive
            return False
        try:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        except RuntimeError:
            # Event loop closed while the reader was blocked.
            return False
        return True

    def _pump(self) -> None:
        buffer = b""
        while True:
            try:
                chunk = os.read(self._fd, 65536)
            except OSError:
                chunk = b""

            if chunk:
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw_line in lines:
                    # Lines produced by the split keep readline() semantics,
                    # which include the trailing newline.
                    if not self._emit(raw_line.decode("utf-8", errors="replace") + "\n"):
                        return
                continue

            if buffer and not self._emit(buffer.decode("utf-8", errors="replace")):
                return
            self._emit(None)
            return
