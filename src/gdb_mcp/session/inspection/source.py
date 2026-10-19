"""Source context methods."""

from __future__ import annotations

import re

from ...domain import (
    OperationError,
    OperationSuccess,
    SourceContextInfo,
    SourceLineRecord,
)
from .base import (
    CodeLocationResolver,
    InspectionContext,
    _ResolvedCodeLocation,
    _ResolvedSourceWindow,
    _SelectionSnapshot,
)

_LIST_LINE_RE = re.compile(r"^(?P<line>\d+)\t(?P<text>.*)$")


def _parse_list_output(output: str) -> list[tuple[int, str]]:
    """Extract the numbered source lines from GDB's ``list`` output.

    ``list`` prints one ``<line number>``-tab-``<text>`` record per source
    line and can interleave unrelated console chatter (a ``list *address``
    header, a missing-source warning, or a distro debuginfo prompt); records
    that do not match that shape are ignored.
    """

    listed: list[tuple[int, str]] = []
    for raw_line in output.split("\n"):
        match = _LIST_LINE_RE.match(raw_line)
        if match is not None:
            listed.append((int(match.group("line")), match.group("text")))
    return listed


class InspectionSource:
    """Source context methods."""

    def __init__(self, context: InspectionContext, resolver: CodeLocationResolver):
        self._context = context
        self._resolver = resolver

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
                captured_selection = self._context.capture_selection()
                if isinstance(captured_selection, OperationError):
                    return captured_selection
                selection = captured_selection

            selection_changed, selection_error = self._context.select_for_inspection(
                selection,
                thread_id=thread_id,
                frame=frame,
            )
            if selection_error is not None:
                return self._context.selection_error_with_restore(selection, selection_error)

            location_result = self._resolver.current_frame_location(
                thread_id=thread_id,
                frame=frame,
                selection=selection,
            )
        elif function is not None:
            location_result = self._resolver.resolve_info_line_location(
                command=f"info line {function}",
                scope="function",
                function=function,
                address=None,
            )
        elif address is not None:
            location_result = self._resolver.resolve_info_line_location(
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
            return self._context.selection_error_with_restore(selection, location_result)

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
            return self._context.selection_error_with_restore(selection, read_result)

        if selection is not None:
            restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
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
        """List one source window through GDB for the resolved location.

        The text comes from GDB's own ``list`` so that path resolution
        (``set substitute-path``, ``set directories``, the working directory,
        a remote target) and line numbering stay GDB's, instead of being
        re-implemented against the local filesystem.
        """

        file_selector = location.file or location.fullname
        if file_selector is None:
            return OperationError(message="Resolved source location has no file path")

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

        result = self._context.command_runner.execute_command_result(
            f"list {file_selector}:{start_line},{file_selector}:{end_line}",
        )
        if isinstance(result, OperationError):
            return result

        listed = _parse_list_output(result.value.output or "")
        if not listed:
            return OperationError(message=f"Source file not found: {file_selector}")

        lines: list[SourceLineRecord] = []
        for line_number, text in listed:
            record: SourceLineRecord = {"line_number": line_number, "text": text}
            if current_line is not None and line_number == current_line:
                record["is_current"] = True
            lines.append(record)

        return _ResolvedSourceWindow(
            file=location.file or file_selector,
            fullname=location.fullname or file_selector,
            start_line=lines[0]["line_number"],
            end_line=lines[-1]["line_number"],
            lines=lines,
        )
