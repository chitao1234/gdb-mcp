"""Base class, shared selectors, and module constants for inspection."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from ...domain import (
    OperationError,
    SourceLineRecord,
)
from ...transport import (
    extract_mi_result_payload,
)
from ..command_runner import SessionCommandRunner
from ..constants import DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from ..runtime import SessionRuntime


@dataclass(frozen=True)
class _SelectionSnapshot:
    """Current thread/frame selection captured for temporary inspection changes."""

    thread_id: int | None
    frame_number: int | None


@dataclass(frozen=True)
class _ResolvedCodeLocation:
    """One normalized code location used by disassembly and source lookup helpers."""

    scope: str
    thread_id: int | None = None
    frame: int | None = None
    function: str | None = None
    address: str | None = None
    file: str | None = None
    fullname: str | None = None
    line: int | None = None
    start_address: str | None = None
    end_address: str | None = None


@dataclass(frozen=True)
class _ResolvedSourceWindow:
    """A concrete source-file line window ready for serialization."""

    file: str
    fullname: str | None
    start_line: int
    end_line: int
    lines: list[SourceLineRecord]


_INFO_LINE_RE = re.compile(
    r'^Line (?P<line>\d+) of "(?P<file>.+)" starts at address '
    r"(?P<start>0x[0-9a-fA-F]+)(?: <[^>]+>)? and ends at (?P<end>0x[0-9a-fA-F]+)",
    re.MULTILINE,
)


_VECTOR_REGISTER_NAME_RE = re.compile(
    r"^(?:xmm[0-9]+|ymm[0-9]+|zmm[0-9]+|mm[0-9]+|st(?:\([0-9]+\)|[0-9]+)?|k[0-9]+|v[0-9]+|q[0-9]+|d[0-9]+|s[0-9]+)$",
    re.IGNORECASE,
)


logger = logging.getLogger(__name__)


class InspectionBase:
    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

    def _capture_selection(self) -> _SelectionSnapshot | OperationError:
        """Capture the currently selected thread and frame for later restoration."""

        thread_result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(thread_result, OperationError):
            return thread_result

        thread_payload = extract_mi_result_payload(command_result_payload(thread_result))
        current_thread = None
        if isinstance(thread_payload, dict):
            current_thread = self._int_or_none(thread_payload.get("current-thread-id"))

        frame_result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(frame_result, OperationError):
            return frame_result

        frame_payload = extract_mi_result_payload(command_result_payload(frame_result))
        current_frame = None
        if isinstance(frame_payload, dict):
            current_frame_payload = frame_payload.get("frame")
            if isinstance(current_frame_payload, dict):
                current_frame = self._int_or_none(current_frame_payload.get("level"))

        self._runtime.mark_thread_selected(current_thread)
        self._runtime.mark_frame_selected(current_frame)

        return _SelectionSnapshot(thread_id=current_thread, frame_number=current_frame)

    def _restore_selection(self, selection: _SelectionSnapshot) -> OperationError | None:
        """Restore a previously captured debugger selection."""

        if selection.thread_id is not None:
            thread_restore = self._command_runner.execute_command_result(
                f"-thread-select {selection.thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_restore, OperationError):
                return OperationError(
                    message=(
                        "Inspection completed but failed to restore the original thread selection: "
                        f"{thread_restore.message}"
                    )
                )

        if selection.frame_number is not None:
            frame_restore = self._command_runner.execute_command_result(
                f"-stack-select-frame {selection.frame_number}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if isinstance(frame_restore, OperationError):
                return OperationError(
                    message=(
                        "Inspection completed but failed to restore the original frame selection: "
                        f"{frame_restore.message}"
                    )
                )

        self._runtime.mark_thread_selected(selection.thread_id)
        self._runtime.mark_frame_selected(selection.frame_number)
        return None

    def _select_for_inspection(
        self,
        selection: _SelectionSnapshot | None,
        *,
        thread_id: int | None,
        frame: int | None,
    ) -> OperationError | None:
        """Temporarily switch thread/frame for one inspection call."""

        if selection is not None and thread_id is not None and selection.thread_id != thread_id:
            thread_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_result, OperationError):
                return thread_result

        if selection is not None and frame is not None and selection.frame_number != frame:
            frame_result = self._command_runner.execute_command_result(
                f"-stack-select-frame {frame}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(frame_result, OperationError):
                return frame_result

        return None

    def _selection_error_with_restore(
        self,
        selection: _SelectionSnapshot | None,
        selection_error: OperationError,
    ) -> OperationError:
        """Return a selection error, restoring the original context when possible."""

        if selection is None:
            return selection_error

        restore_error = self._restore_selection(selection)
        if restore_error is None:
            return selection_error

        return OperationError(
            message=(
                f"{selection_error.message}. "
                "Also failed to restore the original thread/frame selection: "
                f"{restore_error.message}"
            )
        )

    @staticmethod
    def _str_or_none(value: object) -> str | None:
        """Return a string-compatible scalar as text when possible."""

        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        return None

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        """Parse a GDB string/integer field into an integer when possible."""

        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None
