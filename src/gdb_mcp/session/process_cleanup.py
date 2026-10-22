"""Bound shutdown of owned debugger processes, including their pipe cleanup."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

PROCESS_CLEANUP_TIMEOUT_SEC = 10.0


def cleanup_process(
    process: subprocess.Popen[bytes] | subprocess.Popen[str],
    *,
    graceful_shutdown: Callable[[], None] | None = None,
    timeout_sec: float = PROCESS_CLEANUP_TIMEOUT_SEC,
) -> None:
    """Share one deadline across graceful shutdown, terminate, kill, and pipes.

    Pipe writes and closes can block, including when a descendant inherits a
    pipe. Keep that work in a daemon thread so it cannot block the caller beyond
    the budget. The caller escalates signals independently of that worker.
    """

    started = time.monotonic()
    deadline = started + timeout_sec
    finished = threading.Event()
    failures: list[Exception] = []

    def signal_process(*, kill: bool = False) -> None:
        try:
            if process.poll() is None:
                if kill:
                    process.kill()
                else:
                    process.terminate()
        except OSError as exc:
            logger.debug("Could not signal debugger process during cleanup: %s", exc)

    def cleanup() -> None:
        try:
            if process.poll() is None:
                if graceful_shutdown is None:
                    signal_process()
                else:
                    try:
                        graceful_shutdown()
                    except Exception as exc:
                        logger.debug("Debugger graceful shutdown failed: %s", exc)
                        signal_process()
            process.wait()
        except Exception as exc:
            failures.append(exc)
        finally:
            try:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError as exc:
                            logger.debug("Could not close debugger pipe: %s", exc)
            except Exception as exc:
                failures.append(exc)
            finally:
                finished.set()

    worker = threading.Thread(target=cleanup, name="debugger-process-cleanup", daemon=True)
    worker.start()
    if not finished.wait(max(0.0, started + timeout_sec / 2 - time.monotonic())):
        if graceful_shutdown is not None:
            signal_process()
            finished.wait(max(0.0, started + timeout_sec * 0.75 - time.monotonic()))
        if not finished.is_set():
            signal_process(kill=True)
            finished.wait(max(0.0, deadline - time.monotonic()))
    if not finished.is_set():
        raise TimeoutError(f"Debugger process cleanup did not finish within {timeout_sec:g}s")
    if failures:
        raise failures[0]
