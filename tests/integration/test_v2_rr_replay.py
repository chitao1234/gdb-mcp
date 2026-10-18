"""Integration coverage for rr replay sessions.

Skipped when rr is missing or cannot record in this environment (rr needs
perf-event and ptrace access, which sandboxes and CI runners often deny).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.support import dig, integer, text

from .conftest import CallGdbTool, StopSession

pytestmark = pytest.mark.integration

RR = shutil.which("rr")

_RECORDED_PROGRAM = """
int main(void) {
    int i = 0;
    for (i = 0; i < 3; i++) {
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def rr_trace(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Record one short program with rr, or skip when recording is unavailable."""

    if RR is None:
        pytest.skip("rr is not installed")

    workdir = tmp_path_factory.mktemp("rr-replay")
    source = workdir / "rr_probe.c"
    source.write_text(_RECORDED_PROGRAM)
    binary = workdir / "rr_probe"
    compiled = subprocess.run(
        ["gcc", "-g", "-O0", "-o", str(binary), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    trace_dir = workdir / "trace"
    recorded = subprocess.run(
        [RR, "record", "-o", str(trace_dir), str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    if recorded.returncode != 0:
        pytest.skip(f"rr cannot record here: {recorded.stderr.strip()[:160]}")

    return str(trace_dir)


def _frame_function(payload: dict[str, object]) -> str:
    return text(dig(payload, "result", "frame", "func"))


def test_rr_replay_session_supports_reverse_execution(
    rr_trace: str,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """A session started on an rr trace should step both forward and backward."""

    start = call_gdb_tool("gdb_session_start", {"rr_replay": rr_trace})
    assert start["status"] == "success", start
    session_id = integer(start["session_id"])
    try:
        assert start["rr_replay"] == rr_trace
        assert start["target_loaded"] is True

        created = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main"},
            },
        )
        assert created["status"] == "success", created

        continued = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "continue", "execution": {"wait_until": "stop"}},
        )
        assert continued["status"] == "success", continued

        at_main = call_gdb_tool(
            "gdb_context_query", {"session_id": session_id, "action": "frame", "query": {}}
        )
        assert _frame_function(at_main) == "main"

        backward = call_gdb_tool(
            "gdb_execution_manage",
            {
                "session_id": session_id,
                "action": "step",
                "execution": {"reverse": True, "wait_until": "stop"},
            },
        )
        assert backward["status"] == "success", backward

        moved_back = call_gdb_tool(
            "gdb_context_query", {"session_id": session_id, "action": "frame", "query": {}}
        )
        assert _frame_function(moved_back) != "main"
    finally:
        stop_session(session_id, ignore_errors=True)


def test_rr_replay_rejects_a_program_alongside_the_trace(
    rr_trace: str,
    call_gdb_tool: CallGdbTool,
) -> None:
    """The trace owns the target, so pairing it with a program is rejected."""

    result = call_gdb_tool(
        "gdb_session_start",
        {"rr_replay": rr_trace, "program": "/bin/ls"},
    )

    assert result["status"] == "error", result
    assert "rr trace already owns the target" in text(result["message"]).lower()
