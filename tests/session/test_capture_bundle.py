"""Tests for file-oriented capture bundles."""

from __future__ import annotations

import json

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
from gdb_mcp.session.capture import DEFAULT_CAPTURE_MEMORY_MAX_RANGE_BYTES


class TestCaptureBundle:
    """Test capture bundle generation from one live session service."""

    def test_capture_bundle_writes_manifest_and_artifacts(self, session_service, tmp_path):
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

        session_service._lifecycle.get_status = lambda: OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="paused",
                stop_reason="breakpoint-hit",
            )
        )
        session_service._inspection.get_threads = lambda: OperationSuccess(
            ThreadListInfo(
                threads=[{"id": "1", "state": "stopped"}],
                current_thread_id="1",
                count=1,
            )
        )
        session_service._inspection.get_backtrace = (
            lambda thread_id=None, max_frames=100: OperationSuccess(
                BacktraceInfo(
                    thread_id=thread_id,
                    frames=[{"level": "0", "func": "main", "file": "app.c", "line": "10"}],
                    count=1,
                )
            )
        )
        session_service._inspection.get_frame_info = lambda: OperationSuccess(
            FrameInfo(frame={"level": "0", "func": "main", "file": "app.c", "line": "10"})
        )
        session_service._inspection.get_variables = (
            lambda thread_id=None, frame=0: OperationSuccess(
                VariablesInfo(
                    thread_id=thread_id,
                    frame=frame,
                    variables=[{"name": "value", "value": "5"}],
                )
            )
        )
        session_service._inspection.get_registers = (
            lambda thread_id=None, frame=None: OperationSuccess(
                RegistersInfo(registers=[{"number": "0", "value": "0x1"}])
            )
        )
        session_service._inspection.evaluate_expression = (
            lambda expression, thread_id=None, frame=None: OperationSuccess(
                ExpressionValueInfo(expression=expression, value="5")
            )
        )
        session_service._inspection.read_memory = (
            lambda address, count, offset=0: OperationSuccess(
                MemoryReadInfo(
                    address=address,
                    count=count,
                    offset=offset,
                    blocks=[
                        {
                            "begin": "0x1000",
                            "offset": "0x0",
                            "end": "0x1004",
                            "contents": "01020304",
                        }
                    ],
                    block_count=1,
                    captured_bytes=4,
                )
            )
        )

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
        assert bundle.artifact_count >= 8

        manifest_path = tmp_path / "bundle" / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["execution_state"] == "paused"
        assert manifest["last_stop_event"]["reason"] == "breakpoint-hit"
        artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
        assert "session-status" in artifact_names
        assert "threads" in artifact_names
        assert "thread-backtraces" in artifact_names
        assert "command-transcript" in artifact_names
        assert "expressions" in artifact_names
        assert "memory-ranges" in artifact_names

        memory_ranges = json.loads((tmp_path / "bundle" / "memory-ranges.json").read_text())
        assert memory_ranges[0]["requested_range"]["name"] == "value-bytes"
        assert memory_ranges[0]["captured_bytes"] == 4

    def test_capture_bundle_returns_success_with_warnings_for_partial_failures(
        self, session_service, tmp_path
    ):
        """Capture should still produce a bundle when some sections fail."""

        session_service._lifecycle.get_status = lambda: OperationSuccess(
            SessionStatusSnapshot(is_running=True, target_loaded=True, has_controller=True)
        )
        session_service._inspection.get_threads = lambda: OperationError(message="thread failure")
        session_service._inspection.get_frame_info = lambda: OperationError(message="frame failure")
        session_service._inspection.get_variables = lambda thread_id=None, frame=0: OperationError(
            message="vars failure"
        )
        session_service._inspection.get_registers = (
            lambda thread_id=None, frame=None: OperationError(message="register failure")
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
        manifest = json.loads((tmp_path / "partial" / "manifest.json").read_text())
        assert "threads" in manifest["failed_sections"]

    def test_capture_bundle_rejects_memory_ranges_that_exceed_size_limits(
        self, session_service, tmp_path
    ):
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
