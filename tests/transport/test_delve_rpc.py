"""Streaming, concurrency, and late replies in Delve's JSON-RPC transport."""

from __future__ import annotations

import json
import socket

import pytest

from gdb_mcp.transport.delve_rpc import DelveRpcClient, DelveRpcError


def test_requests_are_routed_out_of_order_across_fragmented_stream() -> None:
    local, remote = socket.socketpair()
    client = DelveRpcClient(local)
    try:
        run = client.submit("Command", {"name": "continue"})
        halt = client.submit("Command", {"name": "halt"})
        first = json.loads(remote.makefile("rb").readline())
        assert first["method"] == "RPCServer.Command"
        assert first["params"] == [{"name": "continue"}]
        packet = (
            json.dumps(
                {"id": halt.request_id, "result": {"State": {"message": "暂停"}}, "error": None},
                ensure_ascii=False,
            ).encode()
            + json.dumps(
                {"id": run.request_id, "result": {"State": {"exited": True}}, "error": None}
            ).encode()
        )
        for byte in packet:
            remote.sendall(bytes([byte]))
        assert halt.wait(1)["State"] == {"message": "暂停"}
        assert run.wait(1)["State"] == {"exited": True}
    finally:
        client.close()
        remote.close()


def test_timeout_keeps_execution_response_available() -> None:
    local, remote = socket.socketpair()
    client = DelveRpcClient(local)
    try:
        request = client.submit("Command", {"name": "continue"})
        with pytest.raises(DelveRpcError, match="Timed out") as caught:
            request.wait(0)
        assert caught.value.code == "command_timeout"
        remote.sendall(
            json.dumps(
                {"id": request.request_id, "result": {"State": {"exited": True}}, "error": None}
            ).encode()
        )
        assert request.wait(1) == {"State": {"exited": True}}
    finally:
        client.close()
        remote.close()


def test_disconnect_wakes_all_waiters() -> None:
    local, remote = socket.socketpair()
    client = DelveRpcClient(local)
    first, second = client.submit("State"), client.submit("Command")
    remote.close()
    try:
        for request in (first, second):
            with pytest.raises(DelveRpcError) as caught:
                request.wait(1)
            assert caught.value.code == "protocol_error"
    finally:
        client.close()


def test_remote_error_preserves_message_and_unknown_method_code() -> None:
    local, remote = socket.socketpair()
    client = DelveRpcClient(local)
    try:
        request = client.submit("SetExecutionPoint")
        remote.sendall(
            json.dumps(
                {
                    "id": request.request_id,
                    "result": None,
                    "error": "unknown method: RPCServer.SetExecutionPoint",
                }
            ).encode()
        )
        with pytest.raises(DelveRpcError, match="unknown method") as caught:
            request.wait(1)
        assert caught.value.code == "unsupported_operation"
    finally:
        client.close()
        remote.close()
