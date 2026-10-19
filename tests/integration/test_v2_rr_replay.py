"""Integration coverage for rr replay sessions.

Skipped when rr is missing or cannot record in this environment (rr needs
perf-event and ptrace access, which sandboxes and CI runners often deny).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.support import dig, integer, sequence, text

from .conftest import CallGdbTool, StopSession, _compile
from .program_sources import RR_RECORDED_C_PROGRAM

pytestmark = pytest.mark.integration

RR = shutil.which("rr")


def _rr_recording_blocker() -> str | None:
    """Return why rr cannot record here, or None when it can.

    rr needs perf counters and an unrestricted kptr, which sandboxes and some
    cloud CPUs deny; that is an environment limitation, so the tests skip.
    Anything else that fails once rr is known to work is a real failure.
    """

    rr = RR
    if rr is None:
        return "rr is not installed"

    # `rr record -o DIR` creates DIR, so the probe must name a path that does
    # not exist yet.
    with tempfile.TemporaryDirectory(prefix="rr-capability-") as probe_root:
        probe = subprocess.run(
            [rr, "record", "-o", str(Path(probe_root) / "trace"), "/bin/true"],
            capture_output=True,
            text=True,
            check=False,
        )
    if probe.returncode == 0:
        return None

    detail = (probe.stderr or probe.stdout).strip().splitlines()
    return f"rr cannot record here: {detail[-1][:160] if detail else 'unknown error'}"


@pytest.fixture(scope="module")
def rr_trace(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Record one short program with rr, skipping only when rr itself cannot run."""

    blocker = _rr_recording_blocker()
    if blocker is not None:
        pytest.skip(blocker)

    rr = RR
    assert rr is not None  # _rr_recording_blocker() already skipped when it is missing

    workdir = tmp_path_factory.mktemp("rr-replay")
    _, binary = _compile(
        RR_RECORDED_C_PROGRAM,
        "rr_probe.c",
        "gcc",
        directory=workdir,
    )

    trace_dir = workdir / "trace"
    recorded = subprocess.run(
        [rr, "record", "-o", str(trace_dir), str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    # rr is known to record here, so a failure is this test's failure.
    assert recorded.returncode == 0, recorded.stderr

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

        stepped = call_gdb_tool(
            "gdb_execution_manage",
            {
                "session_id": session_id,
                "action": "step",
                "execution": {"wait_until": "stop"},
            },
        )
        assert stepped["status"] == "success", stepped

        inside_loop = call_gdb_tool(
            "gdb_context_query", {"session_id": session_id, "action": "frame", "query": {}}
        )
        assert _frame_function(inside_loop) == "loop_three_times"

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
        assert _frame_function(moved_back) == "main"
    finally:
        stop_session(session_id, ignore_errors=True)


def test_rr_replay_ignores_a_program_alongside_the_trace(
    rr_trace: str,
    call_gdb_tool: CallGdbTool,
    stop_session: StopSession,
) -> None:
    """The trace owns the target, so a program is ignored and reported."""

    start = call_gdb_tool(
        "gdb_session_start",
        {"rr_replay": rr_trace, "program": "/bin/ls"},
    )

    assert start["status"] == "success", start
    session_id = integer(start["session_id"])
    try:
        assert start["program"] is None
        assert any("ignored" in text(warning).lower() for warning in sequence(start["warnings"]))
    finally:
        stop_session(session_id, ignore_errors=True)
