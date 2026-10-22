"""Drive one native LLDB SB API bridge subprocess over JSON lines."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from typing import cast

from .constants import DEFAULT_TIMEOUT_SEC
from .lldb_bridge_types import BridgeError, BridgeLaunch
from .process_cleanup import cleanup_process


class BridgeClient:
    """One bridge subprocess speaking JSON objects, one per line."""

    def __init__(self, launch: BridgeLaunch) -> None:
        self._lock = threading.RLock()
        self._next_id = 1
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._process = subprocess.Popen(
            [str(launch.executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=launch.environment,
        )
        self._reader = threading.Thread(target=self._read_responses, daemon=True)
        self._reader.start()
        self._closed = False
        self._closing = False
        self._close_lock = threading.Lock()
        self._responsive = True

    def _read_responses(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            self._responses.put(None)
            return
        try:
            for line in stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def request(
        self, operation: str, *, timeout_sec: float = DEFAULT_TIMEOUT_SEC, **payload: object
    ) -> dict[str, object]:
        with self._lock:
            if (
                self._closed
                or (self._closing and operation != "shutdown")
                or self._process.poll() is not None
            ):
                raise BridgeError("LLDB bridge is not running", "not_started")
            request_id = self._next_id
            self._next_id += 1
            message = {"request_id": request_id, "op": operation, **payload}
            stdin = self._process.stdin
            stdout = self._process.stdout
            if stdin is None or stdout is None:
                raise BridgeError("LLDB bridge pipes are unavailable", "not_started")
            try:
                stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._responsive = False
                raise BridgeError(f"LLDB bridge exited: {exc}", "not_started") from exc
            try:
                line = self._responses.get(timeout=timeout_sec if timeout_sec > 0 else None)
            except queue.Empty as exc:
                self._responsive = False
                raise BridgeError(
                    f"LLDB bridge did not answer {operation!r} within {timeout_sec}s",
                    "command_timeout",
                ) from exc
            if line is None:
                self._responsive = False
                raise BridgeError("LLDB bridge exited without a response", "not_started")
            if not line:
                self._responsive = False
                raise BridgeError("LLDB bridge exited without a response", "not_started")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                self._responsive = False
                raise BridgeError(
                    f"Invalid LLDB bridge response: {line.strip()}", "protocol_error"
                ) from exc
            if response.get("request_id") != request_id:
                self._responsive = False
                raise BridgeError(f"LLDB bridge response id mismatch: {response}", "protocol_error")
            if not response.get("ok"):
                raise BridgeError(
                    str(response.get("error") or "LLDB operation failed"),
                    str(response.get("code") or "error"),
                )
            response.pop("request_id", None)
            response.pop("ok", None)
            return cast(dict[str, object], response)

    def close(self, *, detach: bool = False) -> None:
        """Share the ten-second cleanup budget across shutdown and termination."""

        with self._close_lock:
            if self._closed:
                return
            self._closing = True

            def shutdown() -> None:
                self.request("shutdown", detach=detach)

            try:
                cleanup_process(
                    self._process,
                    graceful_shutdown=shutdown if self._responsive else None,
                )
            finally:
                self._closed = True
