"""File-oriented forensic capture helpers for one debugger session."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..domain import (
    CaptureArtifactInfo,
    CaptureBundleInfo,
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    ToolPayload,
    ToolResultStatus,
    payload_to_mapping,
    result_to_mapping,
)
from .backends import DebuggerBackend

DEFAULT_CAPTURE_MEMORY_MAX_RANGE_BYTES = 4 * 1024
DEFAULT_CAPTURE_MEMORY_MAX_TOTAL_BYTES = 64 * 1024


@dataclass(slots=True, frozen=True)
class CaptureBundleRequest:
    """Validated bundle-capture configuration."""

    output_dir: str | None = None
    bundle_name: str | None = None
    expressions: tuple[str, ...] = ()
    memory_ranges: tuple[MemoryCaptureRange, ...] = ()
    max_frames: int = 100
    include_threads: bool = True
    include_backtraces: bool = True
    include_frame: bool = True
    include_variables: bool = True
    include_registers: bool = True
    include_transcript: bool = True
    include_stop_history: bool = True


class SessionCaptureService:
    """Capture structured session state into deterministic on-disk artifacts."""

    def __init__(self, backend: DebuggerBackend):
        self._backend = backend
        self._runtime = backend.runtime

    def capture_bundle(
        self,
        *,
        output_dir: str | None = None,
        bundle_name: str | None = None,
        expressions: list[str] | None = None,
        memory_ranges: list[MemoryCaptureRange] | None = None,
        max_frames: int = 100,
        include_threads: bool = True,
        include_backtraces: bool = True,
        include_frame: bool = True,
        include_variables: bool = True,
        include_registers: bool = True,
        include_transcript: bool = True,
        include_stop_history: bool = True,
    ) -> OperationSuccess[CaptureBundleInfo] | OperationError:
        """Write a structured capture bundle to disk and return its manifest paths."""

        request = CaptureBundleRequest(
            output_dir=output_dir,
            bundle_name=bundle_name,
            expressions=tuple(expressions or ()),
            memory_ranges=tuple(memory_ranges or ()),
            max_frames=max_frames,
            include_threads=include_threads,
            include_backtraces=include_backtraces,
            include_frame=include_frame,
            include_variables=include_variables,
            include_registers=include_registers,
            include_transcript=include_transcript,
            include_stop_history=include_stop_history,
        )

        request_error = self._validate_request(request)
        if request_error is not None:
            return request_error

        try:
            bundle_dir = self._prepare_bundle_dir(request)
        except OSError as exc:
            return OperationError(message=f"Failed to create capture bundle directory: {exc}")
        except ValueError as exc:
            return OperationError(message=str(exc))

        artifacts: list[CaptureArtifactInfo] = []
        failed_sections: list[str] = []

        status_result = self._backend.get_status()
        snapshot = self._runtime.snapshot(
            debugger_alive=status_result.value.debugger_alive,
        )
        status = snapshot.status
        artifacts.append(
            self._write_result_artifact(bundle_dir, "session-status", OperationSuccess(status))
        )
        status_execution_state = status.execution_state
        status_stop_reason = status.stop_reason
        status_stop_kind = status.stop_kind

        stop_event_payload = self._write_payload_artifact(
            bundle_dir,
            "last-stop-event",
            payload_to_mapping(snapshot.last_stop_event),
        )
        artifacts.append(stop_event_payload)

        if request.include_stop_history:
            stop_history_payload = self._write_payload_artifact(
                bundle_dir,
                "stop-history",
                payload_to_mapping(snapshot.stop_history),
            )
            artifacts.append(stop_history_payload)

        if request.include_transcript:
            transcript_payload = self._write_payload_artifact(
                bundle_dir,
                "command-transcript",
                payload_to_mapping(snapshot.command_transcript),
            )
            artifacts.append(transcript_payload)

        thread_ids: list[int] = []
        if request.include_threads or request.include_backtraces:
            threads_result = self._backend.get_threads()
            threads_capture = self._write_result_artifact(bundle_dir, "threads", threads_result)
            artifacts.append(threads_capture)
            if isinstance(threads_result, OperationError):
                failed_sections.append("threads")
            else:
                for thread in threads_result.value.threads:
                    raw_id = thread.id
                    if raw_id is not None and raw_id.isdigit():
                        thread_ids.append(int(raw_id))

        if request.include_backtraces:
            backtrace_payloads: list[StructuredPayload] = []
            if not thread_ids:
                backtrace_payloads.append(
                    result_to_mapping(
                        OperationError(
                            message="Unable to capture thread backtraces because thread enumeration failed"
                        )
                    )
                )
                failed_sections.append("thread-backtraces")
            else:
                for thread_id in thread_ids:
                    backtrace_result = self._backend.get_backtrace(
                        thread_id=thread_id,
                        max_frames=request.max_frames,
                    )
                    payload = result_to_mapping(backtrace_result)
                    payload["thread_id"] = thread_id
                    backtrace_payloads.append(payload)
                    if isinstance(backtrace_result, OperationError):
                        failed_sections.append(f"thread-{thread_id}-backtrace")

            artifacts.append(
                self._write_payload_artifact(bundle_dir, "thread-backtraces", backtrace_payloads)
            )

        current_thread_id = snapshot.current_thread_id
        current_frame = snapshot.current_frame if snapshot.current_frame is not None else 0

        if request.include_frame:
            frame_result = self._backend.get_frame_info()
            frame_capture = self._write_result_artifact(bundle_dir, "current-frame", frame_result)
            artifacts.append(frame_capture)
            if isinstance(frame_result, OperationError):
                failed_sections.append("current-frame")

        if request.include_variables:
            variables_result = self._backend.get_variables(
                thread_id=current_thread_id,
                frame=current_frame,
            )
            variables_capture = self._write_result_artifact(
                bundle_dir,
                "current-variables",
                variables_result,
            )
            artifacts.append(variables_capture)
            if isinstance(variables_result, OperationError):
                failed_sections.append("current-variables")

        if request.include_registers:
            registers_result = self._backend.get_registers(
                thread_id=current_thread_id,
                frame=snapshot.current_frame,
            )
            registers_capture = self._write_result_artifact(
                bundle_dir,
                "current-registers",
                registers_result,
            )
            artifacts.append(registers_capture)
            if isinstance(registers_result, OperationError):
                failed_sections.append("current-registers")

        if request.expressions:
            expression_payloads: list[StructuredPayload] = []
            for expression in request.expressions:
                expression_result = self._backend.evaluate_expression(
                    expression,
                    thread_id=current_thread_id,
                    frame=snapshot.current_frame,
                )
                payload = result_to_mapping(expression_result)
                payload["expression"] = expression
                expression_payloads.append(payload)
                if isinstance(expression_result, OperationError):
                    failed_sections.append(f"expression:{expression}")

            artifacts.append(
                self._write_payload_artifact(bundle_dir, "expressions", expression_payloads)
            )

        if request.memory_ranges:
            memory_range_payloads: list[StructuredPayload] = []
            for memory_range in request.memory_ranges:
                memory_result = self._backend.read_memory(
                    memory_range.address,
                    memory_range.count,
                    offset=memory_range.offset,
                )
                payload = result_to_mapping(memory_result)
                payload["requested_range"] = payload_to_mapping(memory_range)
                if memory_range.name is not None:
                    payload["name"] = memory_range.name
                memory_range_payloads.append(payload)
                if isinstance(memory_result, OperationError):
                    failed_sections.append(self._memory_range_section_name(memory_range))

            artifacts.append(
                self._write_payload_artifact(bundle_dir, "memory-ranges", memory_range_payloads)
            )

        manifest_payload: StructuredPayload = {
            "bundle_name": bundle_dir.name,
            "bundle_dir": str(bundle_dir),
            "captured_at": self._runtime.time_module.time(),
            "execution_state": status_execution_state,
            "stop_reason": status_stop_reason,
            "stop_kind": status_stop_kind,
            "last_stop_event": payload_to_mapping(snapshot.last_stop_event),
            "requested_memory_ranges": payload_to_mapping(request.memory_ranges),
            "artifacts": payload_to_mapping(artifacts),
            "failed_sections": payload_to_mapping(sorted(set(failed_sections))),
            "session": {
                "program": (snapshot.config.program if snapshot.config is not None else None),
                "core": snapshot.config.core if snapshot.config is not None else None,
                "working_dir": (
                    snapshot.config.working_dir if snapshot.config is not None else None
                ),
                "attached_pid": snapshot.attached_pid,
                "current_thread_id": snapshot.current_thread_id,
                "current_frame": snapshot.current_frame,
            },
        }
        manifest_artifact = self._write_payload_artifact(bundle_dir, "manifest", manifest_payload)
        artifacts.append(manifest_artifact)

        warnings = tuple(
            f"Capture section failed: {section}" for section in sorted(set(failed_sections))
        )

        return OperationSuccess(
            CaptureBundleInfo(
                message="Capture bundle written",
                bundle_dir=str(bundle_dir),
                bundle_name=bundle_dir.name,
                manifest_path=manifest_artifact.path,
                artifacts=artifacts,
                failed_sections=sorted(set(failed_sections)) or None,
                execution_state=status_execution_state,
                stop_reason=status_stop_reason,
                stop_kind=status_stop_kind,
                last_stop_event=snapshot.last_stop_event,
            ),
            warnings=warnings,
        )

    def _prepare_bundle_dir(self, request: CaptureBundleRequest) -> Path:
        """Create and return the directory that will hold one bundle."""

        if request.output_dir is not None:
            base_dir = Path(request.output_dir).expanduser().resolve()
            base_dir.mkdir(parents=True, exist_ok=True)
        elif self._runtime.artifact_root is not None:
            base_dir = Path(self._runtime.artifact_root).expanduser().resolve()
            base_dir.mkdir(parents=True, exist_ok=True)
        else:
            base_dir = Path(tempfile.gettempdir()).resolve()

        if request.bundle_name is not None:
            bundle_dir = base_dir / request.bundle_name
            if bundle_dir.exists():
                raise ValueError(f"Capture bundle already exists: {bundle_dir}")
            bundle_dir.mkdir(parents=True, exist_ok=False)
            return bundle_dir.resolve()

        return Path(tempfile.mkdtemp(prefix="gdb-mcp-bundle-", dir=base_dir)).resolve()

    def _validate_request(self, request: CaptureBundleRequest) -> OperationError | None:
        """Validate bounded capture options before creating a bundle directory."""

        total_memory_bytes = 0
        memory_range_names: set[str] = set()
        for index, memory_range in enumerate(request.memory_ranges):
            if memory_range.count < 1:
                return OperationError(
                    message=f"Memory range {index} must request at least one byte",
                )
            if memory_range.offset < 0:
                return OperationError(
                    message=f"Memory range {index} must not use a negative offset",
                )
            if memory_range.count > DEFAULT_CAPTURE_MEMORY_MAX_RANGE_BYTES:
                return OperationError(
                    message=(
                        f"Memory range {index} requests {memory_range.count} bytes, "
                        f"which exceeds the per-range limit of {DEFAULT_CAPTURE_MEMORY_MAX_RANGE_BYTES}"
                    ),
                )

            total_memory_bytes += memory_range.count
            if total_memory_bytes > DEFAULT_CAPTURE_MEMORY_MAX_TOTAL_BYTES:
                return OperationError(
                    message=(
                        "Requested memory capture exceeds the total limit of "
                        f"{DEFAULT_CAPTURE_MEMORY_MAX_TOTAL_BYTES} bytes"
                    ),
                )

            if memory_range.name is None:
                continue
            if not memory_range.name.strip():
                return OperationError(message=f"Memory range {index} name must not be blank")
            if memory_range.name in memory_range_names:
                return OperationError(message=f"Duplicate memory range name: {memory_range.name}")
            memory_range_names.add(memory_range.name)

        return None

    def _write_result_artifact(
        self,
        bundle_dir: Path,
        name: str,
        result: OperationResult[ToolPayload],
    ) -> CaptureArtifactInfo:
        """Serialize one typed operation result into an artifact file."""

        payload = result_to_mapping(result)
        return self._write_payload_artifact(
            bundle_dir,
            name,
            payload,
            status="error" if isinstance(result, OperationError) else "success",
        )

    def _write_payload_artifact(
        self,
        bundle_dir: Path,
        name: str,
        payload: object,
        *,
        status: ToolResultStatus = "success",
    ) -> CaptureArtifactInfo:
        """Write one JSON artifact and return its manifest entry."""

        structured_payload = payload_to_mapping(payload)
        artifact_path = bundle_dir / f"{name}.json"
        artifact_path.write_text(
            json.dumps(structured_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return CaptureArtifactInfo(
            name=name,
            path=str(artifact_path.resolve()),
            status=status,
        )

    @staticmethod
    def _memory_range_section_name(memory_range: MemoryCaptureRange) -> str:
        """Build a stable failed-section name for one memory-range capture request."""

        if memory_range.name is not None:
            return f"memory-range:{memory_range.name}"
        return f"memory-range:{memory_range.address}"
