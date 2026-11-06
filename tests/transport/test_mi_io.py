"""Regressions for finite GDB reads under continuous or oversized output."""

from __future__ import annotations

from typing import BinaryIO, cast
from unittest.mock import Mock, patch

import pytest

from gdb_mcp.transport.mi_io import BoundedMiIoManager


def _manager(stdout: Mock, stderr: Mock | None = None) -> BoundedMiIoManager:
    return BoundedMiIoManager(
        cast(BinaryIO, Mock()),
        cast(BinaryIO, stdout),
        cast(BinaryIO, stderr) if stderr is not None else None,
        configure_nonblocking=False,
    )


def test_continuous_records_cannot_extend_a_poll_deadline() -> None:
    stdout = Mock()
    stdout.read.return_value = b'~"still printing\\n"\n'
    manager = _manager(stdout)
    with (
        patch("gdb_mcp.transport.mi_io.os.name", "posix"),
        patch("gdb_mcp.transport.mi_io.select.select", return_value=([stdout], [], [])),
        patch("gdb_mcp.transport.mi_io.time.monotonic", side_effect=[i / 100 for i in range(30)]),
    ):
        records = manager.get_gdb_response(timeout_sec=0.1)

    assert records
    assert stdout.read.call_count < 10
    assert all(call.args == (64 * 1024,) for call in stdout.read.call_args_list)


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_each_batch_has_a_byte_budget_even_with_a_long_timeout(platform: str) -> None:
    stdout = Mock()
    stdout.read.return_value = b"x" * (64 * 1024 - 1) + b"\n"
    manager = _manager(stdout)
    with (
        patch("gdb_mcp.transport.mi_io.os.name", platform),
        patch("gdb_mcp.transport.mi_io.select.select", return_value=([stdout], [], [])),
        patch("gdb_mcp.transport.mi_io.time.monotonic", return_value=0.0),
    ):
        records = manager.get_gdb_response(timeout_sec=60)

    assert len(records) == 4
    assert stdout.read.call_count == 4


def test_fragmented_records_and_stderr_are_preserved() -> None:
    stdout, stderr = Mock(), Mock()
    stdout.read.side_effect = [b"12^do", b"ne\n", None]
    stderr.read.side_effect = [b"diagnostic\n", None, None]
    manager = _manager(stdout, stderr)
    with (
        patch("gdb_mcp.transport.mi_io.os.name", "posix"),
        patch("gdb_mcp.transport.mi_io.select.select", return_value=([stdout, stderr], [], [])),
        patch("gdb_mcp.transport.mi_io.time.monotonic", side_effect=[i / 100 for i in range(30)]),
    ):
        records = manager.get_gdb_response(timeout_sec=0.05)

    assert records == [
        {"type": "output", "message": None, "payload": "diagnostic", "stream": "stderr"},
        {"type": "result", "message": "done", "payload": None, "token": 12, "stream": "stdout"},
    ]


def test_unterminated_records_have_a_size_limit() -> None:
    stdout = Mock()
    stdout.read.return_value = b"x" * 64
    manager = _manager(stdout)
    with (
        patch("gdb_mcp.transport.mi_io.os.name", "posix"),
        patch("gdb_mcp.transport.mi_io.select.select", return_value=([stdout], [], [])),
        patch("gdb_mcp.transport.mi_io._MAX_RECORD_BYTES", 100),
        pytest.raises(OSError, match="record exceeds"),
    ):
        manager.get_gdb_response(timeout_sec=1)
