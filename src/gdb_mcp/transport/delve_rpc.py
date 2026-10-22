"""Delve's streaming JSON-RPC transport, with independently routed replies."""

from __future__ import annotations

import codecs
import json
import socket
import threading
from dataclasses import dataclass, field


class DelveRpcError(RuntimeError):
    """A transport or remote RPC failure, preserving Delve's error message."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class RpcRequest:
    """One request whose deadline does not cancel the remote operation."""

    request_id: int
    method: str
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, object] = field(default_factory=dict)
    error: DelveRpcError | None = None

    def wait(self, timeout_sec: float | None = None) -> dict[str, object]:
        if not self.done.wait(timeout_sec):
            raise DelveRpcError(f"Timed out waiting for {self.method}", "command_timeout")
        if self.error is not None:
            raise self.error
        return self.result


class DelveRpcClient:
    """One socket, one reader, and multiple outstanding JSON-RPC 1.0 calls."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._connection.settimeout(None)
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, RpcRequest] = {}
        self._next_id = 1
        self._closed = False
        self._reader = threading.Thread(target=self._read, name="delve-rpc-reader", daemon=True)
        self._reader.start()

    @property
    def alive(self) -> bool:
        with self._lock:
            return not self._closed

    def submit(self, method: str, arguments: dict[str, object] | None = None) -> RpcRequest:
        with self._write_lock:
            with self._lock:
                if self._closed:
                    raise DelveRpcError("Delve RPC connection is closed", "not_started")
                request = RpcRequest(self._next_id, method)
                self._next_id += 1
                self._pending[request.request_id] = request
            message = {
                "method": f"RPCServer.{method}",
                "params": [arguments or {}],
                "id": request.request_id,
            }
            try:
                self._connection.sendall((json.dumps(message) + "\n").encode("utf-8"))
            except OSError as exc:
                self._fail(
                    DelveRpcError(f"Could not send Delve RPC request: {exc}", "protocol_error")
                )
                raise request.error or DelveRpcError(str(exc), "protocol_error") from exc
            return request

    def request(
        self,
        method: str,
        arguments: dict[str, object] | None = None,
        *,
        timeout_sec: float = 30,
    ) -> dict[str, object]:
        request = self.submit(method, arguments)
        try:
            return request.wait(timeout_sec)
        finally:
            with self._lock:
                self._pending.pop(request.request_id, None)

    def _read(self) -> None:
        decoder = json.JSONDecoder()
        utf8 = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        try:
            while True:
                chunk = self._connection.recv(65536)
                if not chunk:
                    raise DelveRpcError("Delve RPC connection closed", "protocol_error")
                buffer += utf8.decode(chunk)
                while buffer.strip():
                    buffer = buffer.lstrip()
                    try:
                        response, end = decoder.raw_decode(buffer)
                    except json.JSONDecodeError:
                        break
                    buffer = buffer[end:]
                    self._receive(response)
        except (OSError, UnicodeError, DelveRpcError) as exc:
            error = (
                exc if isinstance(exc, DelveRpcError) else DelveRpcError(str(exc), "protocol_error")
            )
            self._fail(error)

    def _receive(self, response: object) -> None:
        if not isinstance(response, dict):
            raise DelveRpcError("Delve RPC response must be an object", "protocol_error")
        request_id = response.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise DelveRpcError("Delve RPC response has no integer request id", "protocol_error")
        with self._lock:
            request = self._pending.pop(request_id, None)
        if request is None:
            return
        error = response.get("error")
        result = response.get("result")
        if error is not None:
            message = error if isinstance(error, str) else json.dumps(error)
            code = "unsupported_operation" if message.startswith("unknown method:") else "error"
            request.error = DelveRpcError(message, code)
        elif not isinstance(result, dict):
            request.error = DelveRpcError("Delve RPC result must be an object", "protocol_error")
        else:
            request.result = result
        request.done.set()

    def _fail(self, error: DelveRpcError) -> None:
        with self._lock:
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
        for request in pending:
            request.error = error
            request.done.set()
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._connection.close()

    def close(self) -> None:
        self._fail(DelveRpcError("Delve RPC connection closed", "not_started"))
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=1)
