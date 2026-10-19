"""Tests for connecting to externally started GDB/MI endpoints."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from gdb_mcp.transport.external import (
    EndpointError,
    ExternalMiController,
    parse_mi_endpoint,
)
from gdb_mcp.transport.mi_models import MiRecord

# Both waits are generous bounds used only to turn a broken test into a failure
# instead of a hang; the handshakes normally complete immediately.
_SERVER_HANDSHAKE_TIMEOUT_SEC = 5.0
_RECORD_WAIT_TIMEOUT_SEC = 5.0


@dataclass
class UnixMiServer:
    """A running unix-socket MI server and the client reading from it."""

    controller: ExternalMiController
    records: list[MiRecord] = field(default_factory=list)
    advance: threading.Event = field(default_factory=threading.Event)

    def collect_until_result(self) -> list[MiRecord]:
        """Poll the controller until a result record arrives."""

        deadline = time.monotonic() + _RECORD_WAIT_TIMEOUT_SEC
        while time.monotonic() < deadline and not any(
            record.get("type") == "result" for record in self.records
        ):
            self.records.extend(
                self.controller.get_gdb_response(timeout_sec=0.1, raise_error_on_timeout=False)
            )
            # Let the server send the next payload only after this batch has
            # been consumed, so the test exercises a genuinely streamed response.
            self.advance.set()
        return self.records


@pytest.fixture
def unix_mi_server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[UnixMiServer]:
    """Serve preloaded MI payloads over a unix socket and yield the client."""

    payloads = cast(list[bytes], request.param)
    socket_path = str(tmp_path / "gdb.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)
    advance = threading.Event()
    closing = threading.Event()

    def serve() -> None:
        connection, _ = server.accept()
        with connection:
            for payload in payloads:
                connection.sendall(payload)
                advance.wait(timeout=_SERVER_HANDSHAKE_TIMEOUT_SEC)
                advance.clear()
                if closing.is_set():
                    return
            # Keep the stream open until the test tears the fixture down instead
            # of sleeping to outlive the controller's final poll.
            closing.wait(timeout=_SERVER_HANDSHAKE_TIMEOUT_SEC)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    controller = ExternalMiController(
        f"unix://{socket_path}",
        time_to_check_for_additional_output_sec=0.05,
    )
    try:
        yield UnixMiServer(controller=controller, advance=advance)
    finally:
        closing.set()
        advance.set()
        controller.exit()
        server.close()
        thread.join(timeout=2)


class TestParseMiEndpoint:
    """Endpoint strings should parse into typed descriptions."""

    def test_tcp(self) -> None:
        """TCP endpoints should expose host and port."""

        endpoint = parse_mi_endpoint("tcp://127.0.0.1:4399")

        assert endpoint.kind == "tcp"
        assert endpoint.host == "127.0.0.1"
        assert endpoint.port == 4399
        assert endpoint.display == "tcp://127.0.0.1:4399"

    def test_unix(self) -> None:
        """Unix endpoints should expose the socket path."""

        endpoint = parse_mi_endpoint("unix:///tmp/gdb-mcp.sock")

        assert endpoint.kind == "unix"
        assert endpoint.path == "/tmp/gdb-mcp.sock"

    def test_pty(self) -> None:
        """PTY endpoints should expose the device path."""

        endpoint = parse_mi_endpoint("pty:///dev/pts/7")

        assert endpoint.kind == "pty"
        assert endpoint.path == "/dev/pts/7"

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "localhost:4399",
            "tcp://host",
            "tcp://host:notaport",
            "unix:relative",
            "http://x",
        ],
    )
    def test_invalid(self, value: str) -> None:
        """Malformed endpoint strings should be rejected."""

        with pytest.raises(EndpointError):
            parse_mi_endpoint(value)


class TestExternalMiController:
    """The controller should speak MI over an externally owned stream."""

    @pytest.mark.parametrize(
        "unix_mi_server",
        [
            [
                b'=thread-group-added,id="i1"\n',
                b'1^done,bkpt={number="1"}\n',
            ]
        ],
        indirect=True,
    )
    def test_reads_records_over_unix_socket(self, unix_mi_server: UnixMiServer) -> None:
        """Records written by an external process should reach the controller."""

        records = unix_mi_server.collect_until_result()

        assert any(record.get("type") == "result" for record in records)

    @pytest.mark.parametrize(
        "unix_mi_server",
        [
            [
                b'=thread-group-added,id="i1"\r\n',
                b'~"GNU gdb 15.1\\n"\r\n',
                b"1^done\r\n(gdb) \r\n",
            ]
        ],
        indirect=True,
    )
    def test_reads_records_with_crlf_line_endings(self, unix_mi_server: UnixMiServer) -> None:
        """A tty-style CRLF stream must still yield a result record.

        A cooked tty (for example ``socat ... ,pty`` without ``raw,echo=0``)
        turns gdb's ``\\n`` into ``\\r\\n``. pygdbmi cannot classify ``^done\\r``
        as a result record, so the controller has to normalize it away.
        """

        records = unix_mi_server.collect_until_result()
        results = [record for record in records if record.get("type") == "result"]
        assert results, records
        assert results[0].get("message") == "done"
        assert not [record for record in records if record.get("type") == "output"]

    def test_unreachable_endpoint_raises(self) -> None:
        """A missing socket path should surface as an endpoint error."""

        with pytest.raises(EndpointError):
            ExternalMiController("unix:///tmp/definitely-not-here-gdb-mcp.sock")

    @pytest.mark.parametrize("unix_mi_server", [[]], indirect=True)
    def test_exit_is_idempotent(self, unix_mi_server: UnixMiServer) -> None:
        """Detaching twice should be a no-op instead of raising."""

        unix_mi_server.controller.exit()
        unix_mi_server.controller.exit()
