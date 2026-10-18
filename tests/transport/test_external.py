"""Tests for connecting to externally started GDB/MI endpoints."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from gdb_mcp.transport.external import (
    EndpointError,
    ExternalMiController,
    parse_mi_endpoint,
)


class TestParseMiEndpoint:
    """Endpoint strings should parse into typed descriptions."""

    def test_tcp(self) -> None:
        endpoint = parse_mi_endpoint("tcp://127.0.0.1:4399")

        assert endpoint.kind == "tcp"
        assert endpoint.host == "127.0.0.1"
        assert endpoint.port == 4399
        assert endpoint.display == "tcp://127.0.0.1:4399"

    def test_unix(self) -> None:
        endpoint = parse_mi_endpoint("unix:///tmp/gdb-mcp.sock")

        assert endpoint.kind == "unix"
        assert endpoint.path == "/tmp/gdb-mcp.sock"

    def test_pty(self) -> None:
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
        with pytest.raises(EndpointError):
            parse_mi_endpoint(value)


class TestExternalMiController:
    """The controller should speak MI over an externally owned stream."""

    def test_reads_records_over_unix_socket(self, tmp_path: Path) -> None:
        socket_path = str(tmp_path / "gdb.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        server.listen(1)

        def serve() -> None:
            connection, _ = server.accept()
            with connection:
                connection.sendall(b'=thread-group-added,id="i1"\n')
                time.sleep(0.05)
                connection.sendall(b'1^done,bkpt={number="1"}\n')
                time.sleep(0.2)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        controller = ExternalMiController(
            f"unix://{socket_path}",
            time_to_check_for_additional_output_sec=0.05,
        )
        try:
            records: list[dict[str, object]] = []
            deadline = time.time() + 5
            while time.time() < deadline and not any(
                record.get("type") == "result" for record in records
            ):
                records.extend(
                    controller.get_gdb_response(timeout_sec=0.1, raise_error_on_timeout=False)
                )
        finally:
            controller.exit()
            server.close()
            thread.join(timeout=2)

        assert any(record.get("type") == "result" for record in records)

    def test_reads_records_with_crlf_line_endings(self, tmp_path: Path) -> None:
        """A tty-style CRLF stream must still yield a result record.

        A cooked tty (for example ``socat ... ,pty`` without ``raw,echo=0``)
        turns gdb's ``\\n`` into ``\\r\\n``. pygdbmi cannot classify ``^done\\r``
        as a result record, so the controller has to normalize it away.
        """

        socket_path = str(tmp_path / "gdb.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        server.listen(1)

        def serve() -> None:
            connection, _ = server.accept()
            with connection:
                connection.sendall(b'=thread-group-added,id="i1"\r\n')
                time.sleep(0.05)
                connection.sendall(b'~"GNU gdb 15.1\\n"\r\n')
                time.sleep(0.05)
                connection.sendall(b"1^done\r\n(gdb) \r\n")
                time.sleep(0.2)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        controller = ExternalMiController(
            f"unix://{socket_path}",
            time_to_check_for_additional_output_sec=0.05,
        )
        try:
            records: list[dict[str, object]] = []
            deadline = time.time() + 5
            while time.time() < deadline and not any(
                record.get("type") == "result" for record in records
            ):
                records.extend(
                    controller.get_gdb_response(timeout_sec=0.1, raise_error_on_timeout=False)
                )
        finally:
            controller.exit()
            server.close()
            thread.join(timeout=2)

        results = [record for record in records if record.get("type") == "result"]
        assert results, records
        assert results[0].get("message") == "done"
        assert not [record for record in records if record.get("type") == "output"]

    def test_unreachable_endpoint_raises(self) -> None:
        with pytest.raises(EndpointError):
            ExternalMiController("unix:///tmp/definitely-not-here-gdb-mcp.sock")

    def test_exit_is_idempotent(self, tmp_path: Path) -> None:
        socket_path = str(tmp_path / "gdb.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        server.listen(1)
        accepted: list[socket.socket] = []

        def serve() -> None:
            connection, _ = server.accept()
            accepted.append(connection)
            time.sleep(0.2)
            connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        controller = ExternalMiController(f"unix://{socket_path}")
        controller.exit()
        controller.exit()

        server.close()
        thread.join(timeout=2)
        for connection in accepted:
            connection.close()
