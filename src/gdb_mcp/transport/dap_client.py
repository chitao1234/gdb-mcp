"""Debug Adapter Protocol client for LLDB's DAP adapters and Delve's DAP server.

DAP frames are ``Content-Length: N\\r\\n\\r\\n`` followed by one JSON message. The
adapters we care about differ in how that byte stream is reached: ``lldb-dap``
defaults to stdio (and also accepts ``--port``), while ``dlv dap`` only listens
on TCP. Both shapes live here; everything above this module works with requests,
responses, and events only.

Two rules are encoded because measuring real adapters forced them:

* A request the adapter does not advertise can end the session. ``lldb-dap``
  14-19 never answer ``readMemory`` and then abort the process, so callers must
  gate on :attr:`DapClient.capabilities` instead of probing, and
  :meth:`DapClient.supports` only reports a capability the adapter explicitly set
  to ``true``.
* A malformed frame means the stream can no longer be trusted. The client marks
  itself dead instead of resynchronising on garbage.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import time
from collections import deque
from collections.abc import Mapping, Sequence
from typing import IO, Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 15.0
_CONNECT_TIMEOUT_SEC = 20.0


class DapTransportError(RuntimeError):
    """Raised when the adapter stream is closed, unreadable, or out of sync."""


class DapTimeoutError(DapTransportError):
    """Raised when the adapter does not answer within the allotted time."""


@runtime_checkable
class DapStream(Protocol):
    """Byte stream carrying framed DAP messages."""

    def send(self, message: Mapping[str, Any]) -> None:
        """Write one framed message to the adapter."""

    def receive(self) -> Mapping[str, Any]:
        """Read one framed message, raising :class:`DapTransportError` on failure."""

    def close(self) -> None:
        """Release the stream and any process behind it."""


def _write_frame(writer: IO[bytes], message: Mapping[str, Any]) -> None:
    body = json.dumps(message).encode("utf-8")
    writer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    writer.flush()


def _read_frame(reader: IO[bytes]) -> Mapping[str, Any]:
    length: int | None = None
    while True:
        line = reader.readline()
        if not line:
            raise DapTransportError("adapter closed the connection")
        header = line.decode("utf-8", errors="replace").strip()
        if not header:
            break
        if header.lower().startswith("content-length:"):
            raw_length = header.split(":", 1)[1].strip()
            if not raw_length:
                raise DapTransportError("adapter sent a frame without a length")
            length = int(raw_length)
    if length is None:
        raise DapTransportError("adapter sent a frame without a Content-Length header")
    payload = reader.read(length)
    try:
        message = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DapTransportError(f"adapter sent malformed JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise DapTransportError("adapter sent a non-object message")
    return message


class DapStdioStream:
    """DAP over a spawned adapter process' stdin/stdout."""

    def __init__(self, command: Sequence[str], *, cwd: str | None = None) -> None:
        self._process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self._process.stdout is None or self._process.stdin is None:
            raise DapTransportError("adapter process has no stdio pipes")

    @property
    def pid(self) -> int:
        """Adapter process id, for liveness checks and cleanup."""

        return self._process.pid

    def is_alive(self) -> bool:
        """Whether the adapter process is still running."""

        return self._process.poll() is None

    def send(self, message: Mapping[str, Any]) -> None:
        if self._process.stdin is None:
            raise DapTransportError("adapter stdin is closed")
        _write_frame(self._process.stdin, message)

    def receive(self) -> Mapping[str, Any]:
        if self._process.stdout is None:
            raise DapTransportError("adapter stdout is closed")
        return _read_frame(self._process.stdout)

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            if stream is not None:
                stream.close()


class DapTcpStream:
    """DAP over a TCP endpoint, which is the only shape ``dlv dap`` offers."""

    def __init__(self, host: str, port: int, *, timeout_sec: float = _CONNECT_TIMEOUT_SEC) -> None:
        self._connection = self._connect(host, port, timeout_sec)
        self._reader = self._connection.makefile("rb")
        self._writer = self._connection.makefile("wb")

    @staticmethod
    def _connect(host: str, port: int, timeout_sec: float) -> socket.socket:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                connection = socket.create_connection((host, port), timeout=2.0)
            except OSError:
                time.sleep(0.2)
                continue
            connection.settimeout(DEFAULT_TIMEOUT_SEC)
            return connection
        raise DapTransportError(f"no DAP adapter listening on {host}:{port}")

    def send(self, message: Mapping[str, Any]) -> None:
        _write_frame(self._writer, message)

    def receive(self) -> Mapping[str, Any]:
        try:
            return _read_frame(self._reader)
        except (OSError, TimeoutError) as exc:
            raise DapTransportError(f"adapter stream failed: {exc}") from exc

    def close(self) -> None:
        self._reader.close()
        self._writer.close()
        self._connection.close()


def dap_error_message(response: Mapping[str, Any]) -> str | None:
    """Return the adapter's own error text for a failed response, if any.

    The adapters disagree on where that text lives: Delve nests it under
    ``body.error.format`` while ``lldb-dap`` puts it in ``message``.
    """

    if response.get("success", False):
        return None
    body = response.get("body")
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            text = error.get("format")
            if isinstance(text, str) and text:
                return text
    message = response.get("message")
    if isinstance(message, str) and message:
        return message
    return "adapter reported an unspecified failure"


class DapClient:
    """Correlate DAP requests, responses, and events over one stream."""

    def __init__(
        self, stream: DapStream, *, default_timeout_sec: float = DEFAULT_TIMEOUT_SEC
    ) -> None:
        self._stream = stream
        self._default_timeout_sec = default_timeout_sec
        self._sequence = 0
        self._responses: dict[int, Mapping[str, Any]] = {}
        self._events: deque[Mapping[str, Any]] = deque()
        self._capabilities: dict[str, bool] = {}
        self._dead = False

    @property
    def capabilities(self) -> Mapping[str, bool]:
        """Capability flags the adapter reported during ``initialize``."""

        return dict(self._capabilities)

    def supports(self, capability: str) -> bool:
        """Whether the adapter explicitly advertised ``capability``.

        Absent or non-boolean flags count as unsupported: sending such a request
        anyway can terminate the adapter, so callers must not probe.
        """

        return self._capabilities.get(capability) is True

    @property
    def is_dead(self) -> bool:
        """Whether the stream has failed and the client can no longer be used."""

        return self._dead

    def initialize(
        self,
        *,
        client_id: str,
        adapter_id: str,
        lines_start_at1: bool = True,
        columns_start_at1: bool = True,
        path_format: str = "path",
        extra: Mapping[str, Any] | None = None,
    ) -> Mapping[str, bool]:
        """Run the DAP handshake and remember the advertised capabilities."""

        arguments: dict[str, Any] = {
            "clientID": client_id,
            "adapterID": adapter_id,
            "linesStartAt1": lines_start_at1,
            "columnsStartAt1": columns_start_at1,
            "pathFormat": path_format,
        }
        if extra:
            arguments.update(extra)
        response = self.request("initialize", arguments)
        body = response.get("body")
        if isinstance(body, Mapping):
            self._capabilities = {
                str(key): value for key, value in body.items() if isinstance(value, bool)
            }
        logger.debug("Adapter advertised capabilities: %s", sorted(self._capabilities))
        return self.capabilities

    def request(
        self,
        command: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout_sec: float | None = None,
    ) -> Mapping[str, Any]:
        """Send one request and return its response, draining events meanwhile."""

        sequence = self._send(command, arguments)
        return self._await_response(sequence, timeout_sec or self._default_timeout_sec)

    def next_event(self, *, timeout_sec: float | None = None) -> Mapping[str, Any] | None:
        """Return the next queued event of any kind, or ``None`` on timeout."""

        timeout = self._default_timeout_sec if timeout_sec is None else timeout_sec
        deadline = time.monotonic() + timeout
        while not self._events:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._receive_one(deadline)
        return self._events.popleft()

    def wait_for_event(
        self, name: str, *, timeout_sec: float | None = None
    ) -> Mapping[str, Any] | None:
        """Return the next event called ``name``, or ``None`` on timeout."""

        timeout = self._default_timeout_sec if timeout_sec is None else timeout_sec
        deadline = time.monotonic() + timeout
        while True:
            for event in list(self._events):
                if event.get("event") == name:
                    self._events.remove(event)
                    return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._receive_one(deadline)

    def close(self) -> None:
        """Close the underlying stream."""

        self._stream.close()

    def _send(self, command: str, arguments: Mapping[str, Any] | None) -> int:
        if self._dead:
            raise DapTransportError("adapter stream is already closed")
        self._sequence += 1
        self._stream.send(
            {
                "seq": self._sequence,
                "type": "request",
                "command": command,
                "arguments": dict(arguments or {}),
            }
        )
        return self._sequence

    def _await_response(self, sequence: int, timeout_sec: float) -> Mapping[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while sequence not in self._responses:
            self._receive_one(deadline)
        return self._responses.pop(sequence)

    def _receive_one(self, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise DapTimeoutError("adapter did not answer in time")
        try:
            message = self._stream.receive()
        except DapTransportError:
            self._dead = True
            raise
        if message.get("type") == "event":
            self._events.append(message)
            return
        request_seq = message.get("request_seq")
        if not isinstance(request_seq, int):
            raise DapTransportError("adapter sent a response without a request sequence")
        self._responses[request_seq] = message
