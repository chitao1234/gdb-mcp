"""Tests for the Debug Adapter Protocol client."""

from __future__ import annotations

import io
import socket
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from gdb_mcp.transport.dap_client import (
    DapClient,
    DapStdioStream,
    DapTcpStream,
    DapTimeoutError,
    DapTransportError,
    _read_frame,
    _write_frame,
    dap_error_message,
)

# Generous bounds: they only turn a broken test into a failure instead of a hang.
_HANDSHAKE_TIMEOUT_SEC = 5.0


class ScriptedStream:
    """In-memory stream that replays a fixed script of adapter messages."""

    def __init__(self, messages: Sequence[Mapping[str, Any]]) -> None:
        self.sent: list[Mapping[str, Any]] = []
        self._messages = list(messages)
        self.closed = False

    def send(self, message: Mapping[str, Any]) -> None:
        self.sent.append(message)

    def receive(self) -> Mapping[str, Any]:
        if not self._messages:
            raise DapTransportError("adapter closed the connection")
        return self._messages.pop(0)

    def close(self) -> None:
        self.closed = True


class BlockingStream:
    """Stream that never answers, used to exercise timeouts."""

    def __init__(self) -> None:
        self.sent: list[Mapping[str, Any]] = []

    def send(self, message: Mapping[str, Any]) -> None:
        self.sent.append(message)

    def receive(self) -> Mapping[str, Any]:
        raise DapTransportError("no data")

    def close(self) -> None:
        return None


def _response(
    sequence: int, command: str, body: Mapping[str, Any] | None = None, success: bool = True
) -> dict[str, Any]:
    return {
        "seq": 0,
        "type": "response",
        "request_seq": sequence,
        "success": success,
        "command": command,
        "body": dict(body or {}),
    }


def _event(name: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"seq": 0, "type": "event", "event": name, "body": dict(body or {})}


class TestFrameFraming:
    """Content-Length framing and its failure modes."""

    def test_round_trip(self) -> None:
        buffer = io.BytesIO()
        _write_frame(buffer, {"type": "request", "seq": 1})
        buffer.seek(0)

        assert _read_frame(buffer) == {"type": "request", "seq": 1}

    def test_rejects_frame_without_length(self) -> None:
        with pytest.raises(DapTransportError):
            _read_frame(io.BytesIO(b"Content-Type: application/json\r\n\r\n{}"))

    def test_rejects_empty_length(self) -> None:
        with pytest.raises(DapTransportError):
            _read_frame(io.BytesIO(b"Content-Length:\r\n\r\n{}"))

    def test_rejects_malformed_json(self) -> None:
        body = b"{not json"
        with pytest.raises(DapTransportError):
            _read_frame(io.BytesIO(b"Content-Length: %d\r\n\r\n" % len(body) + body))

    def test_rejects_non_object_payload(self) -> None:
        body = b"[1, 2]"
        with pytest.raises(DapTransportError):
            _read_frame(io.BytesIO(b"Content-Length: %d\r\n\r\n" % len(body) + body))

    def test_closed_stream_raises(self) -> None:
        with pytest.raises(DapTransportError):
            _read_frame(io.BytesIO(b""))


class TestDapClient:
    """Request/response correlation, events, and capability gating."""

    def test_correlates_responses_and_drains_events(self) -> None:
        stream = ScriptedStream(
            [
                _event("initialized"),
                _response(1, "initialize", {"supportsConfigurationDoneRequest": True}),
            ]
        )
        client = DapClient(stream)

        response = client.request("initialize", {"adapterID": "lldb"})

        assert response["success"] is True
        assert stream.sent[0]["command"] == "initialize"
        assert stream.sent[0]["seq"] == 1
        assert client.wait_for_event("initialized", timeout_sec=_HANDSHAKE_TIMEOUT_SEC) is not None

    def test_initialize_remembers_boolean_capabilities_only(self) -> None:
        stream = ScriptedStream(
            [
                _response(
                    1,
                    "initialize",
                    {
                        "supportsConfigurationDoneRequest": True,
                        "supportsDataBreakpoints": False,
                        "adapterID": "lldb",
                    },
                )
            ]
        )
        client = DapClient(stream)

        capabilities = client.initialize(client_id="gdb-mcp", adapter_id="lldb")

        assert capabilities == {
            "supportsConfigurationDoneRequest": True,
            "supportsDataBreakpoints": False,
        }

    def test_supports_requires_an_explicit_true(self) -> None:
        stream = ScriptedStream(
            [
                _response(
                    1,
                    "initialize",
                    {
                        "supportsReadMemoryRequest": False,
                        "supportsDisassembleRequest": True,
                        "supportsSetVariable": "yes",
                    },
                )
            ]
        )
        client = DapClient(stream)
        client.initialize(client_id="gdb-mcp", adapter_id="lldb")

        assert client.supports("supportsDisassembleRequest") is True
        assert client.supports("supportsReadMemoryRequest") is False
        assert client.supports("supportsSetVariable") is False
        assert client.supports("never_mentioned") is False

    def test_timeout_raises(self) -> None:
        client = DapClient(ScriptedStream([_event("output")]), default_timeout_sec=0.0)

        with pytest.raises(DapTimeoutError):
            client.request("stackTrace", {"threadId": 1})

    def test_failed_stream_marks_client_dead(self) -> None:
        client = DapClient(BlockingStream(), default_timeout_sec=_HANDSHAKE_TIMEOUT_SEC)

        with pytest.raises(DapTransportError):
            client.request("threads")

        assert client.is_dead is True
        with pytest.raises(DapTransportError):
            client.request("threads")

    def test_close_closes_the_stream(self) -> None:
        stream = ScriptedStream([])
        DapClient(stream).close()

        assert stream.closed is True


class TestDapErrorMessage:
    """The adapters disagree about where their error text lives."""

    def test_success_has_no_error(self) -> None:
        assert dap_error_message(_response(1, "threads")) is None

    def test_delve_nested_error(self) -> None:
        response = _response(
            1, "launch", {"error": {"id": 3000, "format": "Go version too old"}}, success=False
        )

        assert dap_error_message(response) == "Go version too old"

    def test_lldb_dap_message(self) -> None:
        response = {
            "type": "response",
            "request_seq": 1,
            "success": False,
            "command": "launch",
            "message": "Failed to launch",
        }

        assert dap_error_message(response) == "Failed to launch"

    def test_unspecified_failure(self) -> None:
        assert dap_error_message(_response(1, "launch", success=False)) == (
            "adapter reported an unspecified failure"
        )


_FAKE_ADAPTER = """
import json
import sys


def read_message():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        header = line.decode().strip()
        if not header:
            break
        if header.lower().startswith("content-length:"):
            length = int(header.split(":", 1)[1])
    return json.loads(sys.stdin.buffer.read(length))


def write_message(message):
    body = json.dumps(message).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(body) + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    body = {"supportsConfigurationDoneRequest": True} if message["command"] == "initialize" else {}
    write_message(
        {
            "seq": 0,
            "type": "response",
            "request_seq": message["seq"],
            "success": True,
            "command": message["command"],
            "body": body,
        }
    )
"""


class TestDapStdioStream:
    """Framing over a real adapter process' pipes."""

    def test_initialize_over_stdio_and_close(self, tmp_path: Path) -> None:
        script = tmp_path / "fake_adapter.py"
        script.write_text(_FAKE_ADAPTER)
        stream = DapStdioStream([sys.executable, str(script)])
        client = DapClient(stream)
        try:
            capabilities = client.initialize(client_id="gdb-mcp", adapter_id="lldb")
            assert capabilities == {"supportsConfigurationDoneRequest": True}
            assert stream.is_alive() is True
        finally:
            client.close()

        assert stream.is_alive() is False


class TestDapTcpStream:
    """Connecting to an adapter that only listens on TCP."""

    def test_framing_over_tcp(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def serve() -> None:
            connection, _ = server.accept()
            reader = connection.makefile("rb")
            writer = connection.makefile("wb")
            message = _read_frame(reader)
            _write_frame(writer, _response(int(message["seq"]), str(message["command"])))
            connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        client = DapClient(DapTcpStream("127.0.0.1", port, timeout_sec=_HANDSHAKE_TIMEOUT_SEC))
        try:
            assert client.request("threads")["success"] is True
        finally:
            client.close()
            server.close()
        thread.join(timeout=_HANDSHAKE_TIMEOUT_SEC)

    def test_unreachable_endpoint_raises(self) -> None:
        with pytest.raises(DapTransportError):
            DapTcpStream("127.0.0.1", 1, timeout_sec=0.2)

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            DapStdioStream([str(tmp_path / "not-an-adapter")])


def test_stdio_stream_terminates_child_process(tmp_path: Path) -> None:
    """A closed stream must not leave the adapter process behind."""

    script = tmp_path / "fake_adapter.py"
    script.write_text(_FAKE_ADAPTER)
    stream = DapStdioStream([sys.executable, str(script)])
    pid = stream.pid

    stream.close()

    completed = subprocess.run(
        ["ps", "-p", str(pid)], check=False, capture_output=True, text=True, timeout=30
    )
    assert str(pid) not in completed.stdout
