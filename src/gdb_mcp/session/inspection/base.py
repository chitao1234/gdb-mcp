"""Shared context, collaborator, and value objects for the inspection services."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ...domain import (
    OperationError,
    SourceLineRecord,
)
from ...domain.parsing import as_int, as_text_or_number
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
    r"(?P<start>0x[0-9a-fA-F]+)(?: <[^>]+>)?\s+and ends at (?P<end>0x[0-9a-fA-F]+)",
    re.MULTILINE,
)

# A line that has a line-table entry but no instructions -- an empty source
# line, for instance -- is reported without an end address.
_INFO_LINE_NO_CODE_RE = re.compile(
    r'^Line (?P<line>\d+) of "(?P<file>.+)" is at address '
    r"(?P<start>0x[0-9a-fA-F]+)(?: <[^>]+>)? but contains no code\.",
    re.MULTILINE,
)


logger = logging.getLogger(__name__)


class InspectionContext:
    """Shared runtime state and thread/frame selection helpers for inspection."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self.runtime = runtime
        self.command_runner = command_runner

    def capture_selection(self) -> _SelectionSnapshot | OperationError:
        """Capture the currently selected thread and frame for later restoration."""

        thread_result = self.command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(thread_result, OperationError):
            return thread_result

        thread_payload = extract_mi_result_payload(command_result_payload(thread_result))
        current_thread = None
        if isinstance(thread_payload, dict):
            current_thread = as_int(thread_payload.get("current-thread-id"))

        frame_result = self.command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(frame_result, OperationError):
            return frame_result

        frame_payload = extract_mi_result_payload(command_result_payload(frame_result))
        current_frame = None
        if isinstance(frame_payload, dict):
            current_frame_payload = frame_payload.get("frame")
            if isinstance(current_frame_payload, dict):
                current_frame = as_int(current_frame_payload.get("level"))

        self.runtime.mark_thread_selected(current_thread)
        self.runtime.mark_frame_selected(current_frame)

        return _SelectionSnapshot(thread_id=current_thread, frame_number=current_frame)

    def restore_selection(self, selection: _SelectionSnapshot) -> OperationError | None:
        """Restore a previously captured debugger selection."""

        if selection.thread_id is not None:
            thread_restore = self.command_runner.execute_command_result(
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
            frame_restore = self.command_runner.execute_command_result(
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

        self.runtime.mark_thread_selected(selection.thread_id)
        self.runtime.mark_frame_selected(selection.frame_number)
        return None

    def select_for_inspection(
        self,
        selection: _SelectionSnapshot | None,
        *,
        thread_id: int | None,
        frame: int | None,
    ) -> tuple[bool, OperationError | None]:
        """Temporarily switch thread/frame for one inspection call.

        Returns whether the selection actually changed together with an
        optional error, so callers can skip restoring an untouched selection.
        """

        changed = False

        if selection is not None and thread_id is not None and selection.thread_id != thread_id:
            thread_result = self.command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_result, OperationError):
                return changed, thread_result
            changed = True

        if selection is not None and frame is not None and selection.frame_number != frame:
            frame_result = self.command_runner.execute_command_result(
                f"-stack-select-frame {frame}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(frame_result, OperationError):
                return changed, frame_result
            changed = True

        return changed, None

    def restore_selection_if_changed(
        self,
        selection: _SelectionSnapshot | None,
        changed: bool,
    ) -> OperationError | None:
        """Restore a captured selection only when it was actually changed."""

        if not changed or selection is None:
            return None
        return self.restore_selection(selection)

    def selection_error_with_restore(
        self,
        selection: _SelectionSnapshot | None,
        selection_error: OperationError,
    ) -> OperationError:
        """Return a selection error, restoring the original context when possible."""

        if selection is None:
            return selection_error

        restore_error = self.restore_selection(selection)
        if restore_error is None:
            return selection_error

        return OperationError(
            message=(
                f"{selection_error.message}. "
                "Also failed to restore the original thread/frame selection: "
                f"{restore_error.message}"
            )
        )


class CodeLocationResolver:
    """Resolve `info frame`/`info line` output into normalized code locations."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

    def current_frame_location(
        self,
        *,
        thread_id: int | None,
        frame: int | None,
        selection: _SelectionSnapshot | None,
    ) -> _ResolvedCodeLocation | OperationError:
        """Resolve the current frame into a normalized code location."""

        result = self._command_runner.execute_command_result(
            "-stack-info-frame",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        if not isinstance(raw_payload, dict):
            return OperationError(message="GDB returned malformed frame data")

        frame_payload = raw_payload.get("frame")
        if not isinstance(frame_payload, dict):
            return OperationError(message="GDB did not return frame data")

        resolved_thread_id = (
            thread_id
            if thread_id is not None
            else selection.thread_id if selection is not None else self._runtime.current_thread_id
        )
        resolved_frame = frame if frame is not None else as_int(frame_payload.get("level"))
        return _ResolvedCodeLocation(
            scope="current_context",
            thread_id=resolved_thread_id,
            frame=resolved_frame,
            function=as_text_or_number(frame_payload.get("func")),
            address=as_text_or_number(frame_payload.get("addr")),
            file=as_text_or_number(frame_payload.get("file")),
            fullname=as_text_or_number(frame_payload.get("fullname")),
            line=as_int(frame_payload.get("line")),
        )

    def resolve_info_line_location(
        self,
        *,
        command: str,
        scope: str,
        function: str | None,
        address: str | None,
    ) -> _ResolvedCodeLocation | OperationError:
        """Resolve `info line ...` output into a normalized code location."""

        result = self._command_runner.execute_command_result(
            command,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        output = result.value.output or ""
        match = _INFO_LINE_RE.search(output)
        if match is not None:
            start_address: str | None = match.group("start")
            end_address: str | None = match.group("end")
        else:
            no_code_match = _INFO_LINE_NO_CODE_RE.search(output)
            if no_code_match is None:
                return OperationError(
                    message=f"GDB did not return source information for {command!r}"
                )
            match = no_code_match
            start_address = match.group("start")
            end_address = None

        line = int(match.group("line"))
        file = match.group("file")
        return _ResolvedCodeLocation(
            scope=scope,
            function=function,
            address=address,
            file=file,
            fullname=file,
            line=line,
            start_address=start_address,
            end_address=end_address,
        )
