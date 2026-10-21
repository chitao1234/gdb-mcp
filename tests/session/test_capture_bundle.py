"""Tests for file-oriented capture bundles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from gdb_mcp.contracts import SessionState
from gdb_mcp.domain import (
    BacktraceInfo,
    CommandTranscriptEntry,
    ExpressionValueInfo,
    FrameInfo,
    MemoryCaptureRange,
    MemoryReadInfo,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    SessionStatusSnapshot,
    StopEvent,
    ThreadListInfo,
    VariablesInfo,
)
from gdb_mcp.domain.adapters import (
    frame_record,
    memory_block_records,
    register_records,
    thread_records,
    variable_records,
)
from gdb_mcp.session.capture import DEFAULT_CAPTURE_MEMORY_MAX_RANGE_BYTES
from gdb_mcp.session.service import SessionService
from tests.support import gdb_backend, json_array, json_object, mapping, sequence

# The fixtures are the MI-shaped payloads a session produces; the adapters turn
# them into the records a service hands back.
_FRAME = frame_record({"level": "0", "func": "main", "file": "app.c", "line": "10"})


def _stub_bundle_sections(
    monkeypatch: pytest.MonkeyPatch,
    session: SessionService,
    **overrides: Callable[..., object],
) -> None:
    """Point the collaborators ``capture_bundle`` reads at canned results.

    With no overrides this is the all-sections-succeed case; pass e.g.
    ``get_threads=lambda: OperationError(message="thread failure")`` to make one
    section fail.
    """

    defaults: dict[str, Callable[..., object]] = {
        "get_status": lambda: OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="paused",
                stop_reason="breakpoint-hit",
            )
        ),
        "get_threads": lambda: OperationSuccess(
            ThreadListInfo(
                threads=thread_records({"threads": [{"id": "1", "state": "stopped"}]}),
                current_thread_id="1",
            )
        ),
        "get_backtrace": lambda thread_id=None, max_frames=100: OperationSuccess(
            BacktraceInfo(thread_id=thread_id, frames=[_FRAME])
        ),
        "get_frame_info": lambda: OperationSuccess(FrameInfo(frame=_FRAME)),
        "get_variables": lambda thread_id=None, frame=0: OperationSuccess(
            VariablesInfo(
                thread_id=thread_id,
                frame=frame,
                variables=variable_records({"variables": [{"name": "value", "value": "5"}]}),
            )
        ),
        "get_registers": lambda thread_id=None, frame=None: OperationSuccess(
            RegistersInfo(
                registers=register_records({"register-values": [{"number": "0", "value": "0x1"}]})
            )
        ),
        "evaluate_expression": lambda expression, thread_id=None, frame=None: OperationSuccess(
            ExpressionValueInfo(expression=expression, value="5")
        ),
        "read_memory": lambda address, count, offset=0: OperationSuccess(
            MemoryReadInfo(
                address=address,
                requested_bytes=count,
                offset=offset,
                blocks=memory_block_records(
                    {
                        "memory": [
                            {
                                "begin": "0x1000",
                                "offset": "0x0",
                                "end": "0x1004",
                                "contents": "01020304",
                            }
                        ]
                    }
                ),
                captured_bytes=4,
            )
        ),
    }
    defaults.update(overrides)

    for name, result in defaults.items():
        monkeypatch.setattr(gdb_backend(session), name, result)


class TestCaptureBundle:
    """Test capture bundle generation from one live session service."""

    def test_capture_bundle_writes_manifest_and_artifacts(
        self, session_service: SessionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Successful captures should write a manifest plus structured JSON files."""

        session_service.runtime.mark_thread_selected(1)
        session_service.runtime.mark_frame_selected(0)
        session_service.runtime.record_stop_event(
            StopEvent(
                execution_state="paused",
                reason="breakpoint-hit",
                command="-exec-run",
                thread_id=1,
            )
        )
        session_service.runtime.record_command_transcript(
            CommandTranscriptEntry(command="-exec-run", status="success")
        )
        _stub_bundle_sections(monkeypatch, session_service)

        result = session_service.capture_bundle(
            output_dir=str(tmp_path),
            bundle_name="bundle",
            expressions=["value"],
            memory_ranges=[MemoryCaptureRange(address="&value", count=4, name="value-bytes")],
        )

        assert isinstance(result, OperationSuccess)
        bundle = result.value
        assert bundle.bundle_name == "bundle"
        assert bundle.bundle_dir == str((tmp_path / "bundle").resolve())
        assert len(bundle.artifacts) >= 8

        manifest_path = tmp_path / "bundle" / "manifest.json"
        assert manifest_path.exists()
        manifest = json_object(manifest_path.read_text())
        assert manifest["execution_state"] == "paused"
        assert mapping(manifest["last_stop_event"])["reason"] == "breakpoint-hit"
        artifact_names = {mapping(artifact)["name"] for artifact in sequence(manifest["artifacts"])}
        assert {
            "session-status",
            "threads",
            "thread-backtraces",
            "command-transcript",
            "expressions",
            "memory-ranges",
        } <= artifact_names

        memory_ranges = json_array((tmp_path / "bundle" / "memory-ranges.json").read_text())
        first_range = mapping(memory_ranges[0])
        assert mapping(first_range["requested_range"])["name"] == "value-bytes"
        assert first_range["captured_bytes"] == 4

    def test_capture_bundle_returns_success_with_warnings_for_partial_failures(
        self, session_service: SessionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Capture should still produce a bundle when some sections fail."""

        _stub_bundle_sections(
            monkeypatch,
            session_service,
            get_threads=lambda: OperationError(message="thread failure"),
            get_frame_info=lambda: OperationError(message="frame failure"),
            get_variables=lambda thread_id=None, frame=0: OperationError(message="vars failure"),
            get_registers=lambda thread_id=None, frame=None: OperationError(
                message="register failure"
            ),
        )

        result = session_service.capture_bundle(
            output_dir=str(tmp_path),
            bundle_name="partial",
            include_transcript=False,
        )

        assert isinstance(result, OperationSuccess)
        assert result.warnings
        assert result.value.failed_sections is not None
        assert "threads" in result.value.failed_sections
        assert "thread-backtraces" in result.value.failed_sections
        manifest = json_object((tmp_path / "partial" / "manifest.json").read_text())
        assert "threads" in sequence(manifest["failed_sections"])

    def test_capture_bundle_rejects_memory_ranges_that_exceed_size_limits(
        self, session_service: SessionService, tmp_path: Path
    ) -> None:
        """Explicit memory captures should fail fast when a single range is too large."""

        result = session_service.capture_bundle(
            output_dir=str(tmp_path),
            bundle_name="too-large",
            memory_ranges=[
                MemoryCaptureRange(
                    address="&value",
                    count=DEFAULT_CAPTURE_MEMORY_MAX_RANGE_BYTES + 1,
                )
            ],
        )

        assert isinstance(result, OperationError)
        assert "per-range limit" in result.message
