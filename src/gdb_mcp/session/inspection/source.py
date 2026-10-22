"""Source context methods."""

from __future__ import annotations

import re

from ...domain import (
    InspectionContextInfo,
    InspectionLocation,
    OperationError,
    OperationSuccess,
    SourceContextInfo,
    SourceLineRecord,
    SourceRange,
)
from ..requests import (
    AddressLocation,
    CurrentLocation,
    FileLineLocation,
    FileRangeLocation,
    FunctionLocation,
    SourceContextRequest,
    SourceLocation,
)
from .base import (
    CodeLocationResolver,
    InspectionContext,
    _ResolvedCodeLocation,
    _ResolvedSourceWindow,
    _SelectionSnapshot,
)

_LIST_LINE_RE = re.compile(r"^(?P<line>\d+)\t(?P<text>.*)$")

# `CommandExecutionInfo.console_output` preserves trailing whitespace, so the
# final blank source line still has its delimiter ("12\t").
_LIST_TRAILING_BLANK_RE = re.compile(r"^(?P<line>\d+)$")


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
            continue
        blank = _LIST_TRAILING_BLANK_RE.match(raw_line)
        if blank is not None:
            listed.append((int(blank.group("line")), ""))
    return listed


class InspectionSource:
    """Source context methods."""

    def __init__(self, context: InspectionContext, resolver: CodeLocationResolver):
        self._context = context
        self._resolver = resolver

    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        """Return structured source lines for one resolved code location."""

        selection: _SelectionSnapshot | None = None
        location_result: _ResolvedCodeLocation | OperationError
        thread_id, frame = request.thread_id, request.frame
        location = request.location

        if isinstance(location, CurrentLocation):
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
        elif isinstance(location, FunctionLocation):
            location_result = self._resolver.resolve_info_line_location(
                command=f"info line {location.function}",
                scope="function",
                function=location.function,
                address=None,
            )
        elif isinstance(location, AddressLocation):
            location_result = self._resolver.resolve_info_line_location(
                command=f"info line *{location.address}",
                scope="address",
                function=None,
                address=location.address,
            )
        elif isinstance(location, FileLineLocation):
            location_result = _ResolvedCodeLocation(
                scope="file_line",
                file=location.file,
                fullname=location.file,
                line=location.line,
            )
        elif isinstance(location, FileRangeLocation):
            location_result = _ResolvedCodeLocation(
                scope="file_range",
                file=location.file,
                fullname=location.file,
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
            requested_location=location,
            context_before=request.context_before,
            context_after=request.context_after,
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
                context=InspectionContextInfo(
                    thread_id=location_result.thread_id,
                    frame=location_result.frame,
                ),
                location=InspectionLocation(
                    kind=location_result.scope,
                    function=location_result.function,
                    address=location_result.address,
                    file=read_result.file,
                    fullname=read_result.fullname,
                    line=location_result.line,
                ),
                range=SourceRange(
                    start_line=read_result.start_line,
                    end_line=read_result.end_line,
                ),
                lines=read_result.lines,
            )
        )

    def _read_source_context(
        self,
        location: _ResolvedCodeLocation,
        *,
        requested_location: SourceLocation,
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

        if isinstance(requested_location, FileRangeLocation):
            start_line = requested_location.start_line
            end_line = requested_location.end_line
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

        listed = _parse_list_output(result.value.console_output)
        if not listed:
            return OperationError(message=f"Source file not found: {file_selector}")

        lines: list[SourceLineRecord] = []
        for line_number, text in listed:
            lines.append(
                SourceLineRecord(
                    line_number=line_number,
                    text=text,
                    is_current=current_line is not None and line_number == current_line,
                )
            )

        return _ResolvedSourceWindow(
            file=location.file or file_selector,
            fullname=location.fullname or file_selector,
            start_line=listed[0][0],
            end_line=listed[-1][0],
            lines=lines,
        )
