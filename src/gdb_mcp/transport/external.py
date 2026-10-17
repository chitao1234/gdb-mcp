"""Controllers for GDB/MI streams owned by another process.

The default controller spawns its own ``gdb`` child. These controllers instead
connect to a GDB/MI byte stream that an external process already started and
exposed, so gdb-mcp never owns the debugger process.
"""

from __future__ import annotations

import logging
import os
import socket
import termios
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Literal, cast

from pygdbmi.IoManager import IoManager

from .mi_models import MiRecord
from .protocols import ControllerIoManagerProtocol, GdbProcessProtocol

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_SEC = 10.0

_ENDPOINT_HELP = (
    "Unsupported endpoint. Use 'tcp://host:port', 'unix:///path/to/socket', "
    "or 'pty:///dev/pts/N'."
)


class EndpointError(ValueError):
    """Raised when an MI endpoint is malformed or cannot be reached."""


@dataclass(frozen=True, slots=True)
class MiEndpoint:
    """Parsed description of an external GDB/MI endpoint."""

    kind: Literal["tcp", "unix", "pty"]
    display: str
    host: str | None = None
    port: int | None = None
    path: str | None = None


def parse_mi_endpoint(endpoint: str) -> MiEndpoint:
    """Parse an endpoint string into a typed endpoint description."""

    raw = endpoint.strip()
    if not raw:
        raise EndpointError("endpoint must be a non-empty string")

    scheme, _, remainder = raw.partition("://")
    scheme = scheme.lower()

    if scheme == "tcp":
        host, separator, port_text = remainder.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            raise EndpointError("tcp endpoint must look like 'tcp://host:port'")
        return MiEndpoint(
            kind="tcp",
            display=f"tcp://{host}:{int(port_text)}",
            host=host,
            port=int(port_text),
        )

    if scheme in {"unix", "pty"}:
        path = remainder
        if not path.startswith("/"):
            raise EndpointError(f"{scheme} endpoint must look like '{scheme}:///path/to/endpoint'")
        kind: Literal["unix", "pty"] = "unix" if scheme == "unix" else "pty"
        return MiEndpoint(kind=kind, display=f"{scheme}://{path}", path=path)

    raise EndpointError(_ENDPOINT_HELP)


@dataclass(slots=True)
class _EndpointConnection:
    """One opened external endpoint and how to release it."""

    stream: BinaryIO
    close: Callable[[], None]


class _CrLfNormalizingStream:
    """Binary stream wrapper that hides tty carriage returns from MI output.

    A tty left in cooked mode (for example ``socat ... ,pty`` without
    ``raw,echo=0``) translates every ``\\n`` gdb writes into ``\\r\\n``. pygdbmi
    splits records on ``\\n`` only, and its MI result-record pattern ends in
    ``$`` with a ``\\S+?`` result class, so a trailing ``\\r`` makes the whole
    line match no record pattern at all: ``^done\\r`` silently degrades into a
    generic ``output`` record instead of a ``result`` record, and commands never
    appear to complete.

    GDB escapes control characters inside MI payloads, so any raw ``\\r`` byte
    on the stream is a line terminator contributed by a tty and can be dropped.
    """

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner

    def read(self, size: int = -1) -> bytes | None:
        data = self._inner.read(size)
        if data and b"\r" in data:
            return data.replace(b"\r", b"")
        return data

    def write(self, data: bytes) -> int | None:
        return self._inner.write(data)

    def flush(self) -> None:
        self._inner.flush()

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()

    def isatty(self) -> bool:
        return False


def _open_tcp(endpoint: MiEndpoint, timeout_sec: float) -> _EndpointConnection:
    assert endpoint.host is not None and endpoint.port is not None
    sock = socket.create_connection((endpoint.host, endpoint.port), timeout=timeout_sec)
    return _adopt_socket(sock)


def _open_unix(endpoint: MiEndpoint, timeout_sec: float) -> _EndpointConnection:
    assert endpoint.path is not None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    sock.connect(endpoint.path)
    return _adopt_socket(sock)


def _adopt_socket(sock: socket.socket) -> _EndpointConnection:
    """Wrap a connected socket as an unbuffered read/write binary stream."""

    sock.setblocking(False)
    fd = sock.detach()
    stream = cast(BinaryIO, os.fdopen(fd, "r+b", buffering=0))
    return _EndpointConnection(stream=stream, close=stream.close)


def _open_pty(endpoint: MiEndpoint) -> _EndpointConnection:
    assert endpoint.path is not None
    fd = os.open(endpoint.path, os.O_RDWR | os.O_NOCTTY)
    _configure_raw_tty(fd)
    stream = cast(BinaryIO, os.fdopen(fd, "r+b", buffering=0))
    return _EndpointConnection(stream=stream, close=stream.close)


def _configure_raw_tty(fd: int) -> None:
    """Disable echo and line editing on a tty so MI bytes pass through untouched."""

    try:
        attrs = termios.tcgetattr(fd)
    except termios.error:
        return

    attrs[0] &= ~(termios.ICRNL | termios.IXON)
    attrs[1] &= ~termios.OPOST
    attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
    try:
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except termios.error as exc:  # pragma: no cover - defensive around exotic ttys
        logger.debug("Could not switch %s to raw mode: %s", fd, exc)


def open_endpoint(endpoint: MiEndpoint, *, timeout_sec: float) -> _EndpointConnection:
    """Open the byte stream for one parsed endpoint."""

    if endpoint.kind == "tcp":
        return _open_tcp(endpoint, timeout_sec)
    if endpoint.kind == "unix":
        return _open_unix(endpoint, timeout_sec)
    return _open_pty(endpoint)


class ExternalMiController:
    """`GdbControllerProtocol` backed by a GDB/MI stream started elsewhere.

    ``exit`` only detaches: it closes this side of the stream and never signals
    the external GDB process.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        time_to_check_for_additional_output_sec: float = 0.2,
        connect_timeout_sec: float = DEFAULT_CONNECT_TIMEOUT_SEC,
    ) -> None:
        self.endpoint = parse_mi_endpoint(endpoint)
        try:
            self._connection = open_endpoint(self.endpoint, timeout_sec=connect_timeout_sec)
        except OSError as exc:
            raise EndpointError(
                f"Could not reach MI endpoint {self.endpoint.display}: {exc}"
            ) from exc

        self._stream: BinaryIO = cast(BinaryIO, _CrLfNormalizingStream(self._connection.stream))
        self._io_manager = IoManager(
            self._stream,
            self._stream,
            None,
            time_to_check_for_additional_output_sec,
        )
        self.io_manager: ControllerIoManagerProtocol = cast(
            ControllerIoManagerProtocol, self._io_manager
        )
        # The external process owns gdb, so gdb-mcp has no child to poll.
        self.gdb_process: GdbProcessProtocol | None = None
        self._closed = False

    def get_gdb_response(
        self,
        *,
        timeout_sec: float,
        raise_error_on_timeout: bool,
    ) -> list[MiRecord]:
        """Read one batch of GDB/MI records from the external stream."""

        return cast(
            list[MiRecord],
            self._io_manager.get_gdb_response(
                timeout_sec=timeout_sec,
                raise_error_on_timeout=raise_error_on_timeout,
            ),
        )

    def exit(self) -> None:
        """Detach from the external stream without terminating GDB."""

        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        except OSError as exc:  # pragma: no cover - best effort
            logger.debug("Ignoring error while closing external MI endpoint: %s", exc)


def create_external_controller(
    endpoint: str,
    *,
    time_to_check_for_additional_output_sec: float = 0.2,
) -> ExternalMiController:
    """Build a controller that connects to an externally started GDB/MI stream."""

    return ExternalMiController(
        endpoint,
        time_to_check_for_additional_output_sec=time_to_check_for_additional_output_sec,
    )
