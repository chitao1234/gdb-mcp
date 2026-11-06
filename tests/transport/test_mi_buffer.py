"""Output budgets must preserve the result and the final debugger stop."""

from gdb_mcp.transport.mi_buffer import MiRecordBuffer
from gdb_mcp.transport.mi_models import MiRecord


def test_large_streams_are_truncated_without_losing_control_records() -> None:
    buffer = MiRecordBuffer()
    result: MiRecord = {"type": "result", "message": "running"}
    stop: MiRecord = {
        "type": "notify",
        "message": "stopped",
        "payload": {"reason": "signal-received"},
    }
    buffer.append(result)
    for _ in range(20):
        buffer.append({"type": "output", "payload": "x" * (64 * 1024)})
    buffer.append(stop)

    records = buffer.records
    assert records[0] == result
    assert stop in records
    assert sum(len(str(record.get("payload", ""))) for record in records) < 300 * 1024
    assert "output truncated" in str(records[-1]["payload"])


def test_tiny_records_and_notifications_are_also_bounded() -> None:
    buffer = MiRecordBuffer()
    result: MiRecord = {"type": "result", "message": "running"}
    buffer.append(result)
    for index in range(10000):
        buffer.append({"type": "notify", "message": "thread-created", "payload": {"id": index}})
    stop: MiRecord = {
        "type": "notify",
        "message": "stopped",
        "payload": {"reason": "breakpoint-hit"},
    }
    buffer.append(stop)
    for index in range(2000):
        buffer.append({"type": "notify", "message": "thread-exited", "payload": {"id": index}})

    assert len(buffer.records) <= 1025
    assert buffer.records[0] == result
    assert stop in buffer.records
