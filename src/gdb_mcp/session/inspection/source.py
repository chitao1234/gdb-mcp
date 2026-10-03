"""Source context methods."""

from __future__ import annotations

from pathlib import Path

from ...domain import (
    OperationError,
    OperationSuccess,
    SourceContextInfo,
    SourceLineRecord,
)
from .base import (
    _ResolvedCodeLocation,
    _ResolvedSourceWindow,
    _SelectionSnapshot,
)
from .disassembly import (
    InspectionDisassemblyMixin,
)


class InspectionSourceMixin(InspectionDisassemblyMixin):
    """Source context methods."""

    def get_source_context(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        context_before: int = 5,
        context_after: int = 5,
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        """Return structured source lines for one resolved code location."""

        selection: _SelectionSnapshot | None = None
        location_result: _ResolvedCodeLocation | OperationError

        if all(value is None for value in (function, address, file, line, start_line, end_line)):
            if thread_id is not None or frame is not None:
                captured_selection = self._capture_selection()
                if isinstance(captured_selection, OperationError):
                    return captured_selection
                selection = captured_selection

            selection_error = self._select_for_inspection(
                selection,
                thread_id=thread_id,
                frame=frame,
            )
            if selection_error is not None:
                return self._selection_error_with_restore(selection, selection_error)

            location_result = self._current_frame_location(
                thread_id=thread_id,
                frame=frame,
                selection=selection,
            )
        elif function is not None:
            location_result = self._resolve_info_line_location(
                command=f"info line {function}",
                scope="function",
                function=function,
                address=None,
            )
        elif address is not None:
            location_result = self._resolve_info_line_location(
                command=f"info line *{address}",
                scope="address",
                function=None,
                address=address,
            )
        elif file is not None and line is not None:
            location_result = _ResolvedCodeLocation(
                scope="file_line",
                file=file,
                fullname=file,
                line=line,
            )
        elif file is not None and start_line is not None and end_line is not None:
            location_result = _ResolvedCodeLocation(
                scope="file_range",
                file=file,
                fullname=file,
                line=None,
            )
        else:
            return OperationError(message="Invalid source context selector combination")

        if isinstance(location_result, OperationError):
            if selection is None:
                return location_result
            return self._selection_error_with_restore(selection, location_result)

        read_result = self._read_source_context(
            location_result,
            requested_start_line=start_line,
            requested_end_line=end_line,
            context_before=context_before,
            context_after=context_after,
        )
        if isinstance(read_result, OperationError):
            if selection is None:
                return read_result
            return self._selection_error_with_restore(selection, read_result)

        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(
            SourceContextInfo(
                scope=location_result.scope,
                thread_id=location_result.thread_id,
                frame=location_result.frame,
                function=location_result.function,
                address=location_result.address,
                file=read_result.file,
                fullname=read_result.fullname,
                line=location_result.line,
                start_line=read_result.start_line,
                end_line=read_result.end_line,
                lines=read_result.lines,
                count=len(read_result.lines),
            )
        )

    def _read_source_context(
        self,
        location: _ResolvedCodeLocation,
        *,
        requested_start_line: int | None,
        requested_end_line: int | None,
        context_before: int,
        context_after: int,
    ) -> _ResolvedSourceWindow | OperationError:
        """Read one source window from disk for the resolved location."""

        file_selector = location.fullname or location.file
        if file_selector is None:
            return OperationError(message="Resolved source location has no file path")

        path = Path(file_selector).expanduser()
        if not path.exists():
            return OperationError(message=f"Source file not found: {file_selector}")

        source_lines = path.read_text().splitlines()
        if requested_start_line is not None and requested_end_line is not None:
            start_line = requested_start_line
            end_line = requested_end_line
            current_line = None
        else:
            current_line = location.line
            if current_line is None:
                return OperationError(message="Resolved source location has no source line")
            start_line = max(1, current_line - context_before)
            end_line = current_line + context_after

        if start_line > len(source_lines):
            return OperationError(message=f"Source line {start_line} is outside {file_selector}")

        end_line = min(end_line, len(source_lines))
        lines: list[SourceLineRecord] = []
        for line_number in range(start_line, end_line + 1):
            record: SourceLineRecord = {
                "line_number": line_number,
                "text": source_lines[line_number - 1],
            }
            if current_line is not None and line_number == current_line:
                record["is_current"] = True
            lines.append(record)

        resolved_fullname = location.fullname or str(path.resolve())
        return _ResolvedSourceWindow(
            file=location.file or str(path),
            fullname=resolved_fullname,
            start_line=start_line,
            end_line=end_line,
            lines=lines,
        )
