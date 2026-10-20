"""Contract coverage for the LLDB-specific session fields."""

from __future__ import annotations

from gdb_mcp.contracts import DEBUGGER_LLDB, DEBUGGER_NAMES
from gdb_mcp.mcp.schemas.dedicated import ConnectSessionArgs


def test_lldb_is_a_supported_debugger_name() -> None:
    assert DEBUGGER_LLDB == "lldb"
    assert "gdb" in DEBUGGER_NAMES
    assert "lldb" in DEBUGGER_NAMES


def test_connect_accepts_lldb_remote_target_fields() -> None:
    args = ConnectSessionArgs(
        endpoint="connect://127.0.0.1:12345",
        debugger="lldb",
        program="/tmp/program",
        gdb_path="/opt/lldb/19",
        init_commands=["settings set target.process.stop-on-exec false"],
    )

    assert args.endpoint == "connect://127.0.0.1:12345"
    assert args.debugger == "lldb"
    assert args.program == "/tmp/program"
    assert args.gdb_path == "/opt/lldb/19"
    assert args.init_commands == ["settings set target.process.stop-on-exec false"]


def test_connect_defaults_to_gdb_for_backward_compatibility() -> None:
    args = ConnectSessionArgs(endpoint="tcp://127.0.0.1:4399")

    assert args.debugger == "gdb"
    assert args.program is None
    assert args.gdb_path is None
