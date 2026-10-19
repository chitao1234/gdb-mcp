"""Build LLDB ``source list`` commands and parse their output.

LLDB's DAP adapters do not serve source text: on lldb-dap 14 and 20 the ``source``
request answers ``success`` with empty content and ``supportsLoadedSourcesRequest``
is never advertised, so the text has to come from the command line. Its measured
grammar is::

       9   \\t    int nums[3] = {1, 2, 3};

a right-aligned line number, one tab, then the raw line (tabs and spaces
preserved, blank lines kept with empty text).

Three measured behaviours shape this module:

* ``-c N`` prints N+1 lines, because LLDB computes
  ``count = context_before + context_after + 1`` and ``-c`` feeds
  ``context_after`` (lldb/source/Core/SourceManager.cpp). Callers therefore ask
  for the window they want and :func:`select_window` slices by returned line
  number instead of trusting the count.
* "no source" is silent. A line past the end of the file, or a binary whose
  source is embedded and queried by file, returns zero data lines and no error.
  A missing file is different and does report
  ``error: Could not find source file "x".``
* The ``-f/-l/-c`` form never prints a ``File:`` header; only the symbol form
  does, and with embedded source that header names a synthetic path such as
  ``/tmp/-ac5777.probe.c``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# The DAP escape hatch echoes the command as the first line of its result.
_ECHO_RE = re.compile(r"^\(lldb\)\s")
_FILE_HEADER_RE = re.compile(r"^File:\s*(?P<path>.+?)\s*$")
# The address form (`-a`) marks the current line with a leading `->`; keeping the
# marker optional means that line is not silently dropped.
_LINE_RE = re.compile(r"^(?:->\s+)?\s*(?P<number>\d+)\s*\t(?P<text>.*)$")
_ERROR_RE = re.compile(r"^error:\s*(?P<message>.+?)\s*$")

# lldb/source/Utility/Args.cpp treats these as the characters that end a plain
# argument or start a quote scope.
_LLDB_SPECIAL_CHARS = " \t\r\"'`\\"


def quote_lldb_argument(value: str) -> str:
    """Quote one argument for LLDB's command parser.

    The parser only honours a backslash when it precedes one of its special
    characters, so both ``\\`` and ``"`` are escaped here and the value is always
    wrapped in double quotes. Newlines are rejected because they would start a
    new command instead of staying part of the argument.
    """

    if "\n" in value or "\r" in value:
        raise ValueError("LLDB command arguments must not contain newlines")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def needs_quoting(value: str) -> bool:
    """Whether an argument has to be quoted for LLDB to keep it in one piece."""

    return any(char in _LLDB_SPECIAL_CHARS for char in value)


def _argument(value: str) -> str:
    # A newline is not one of the parser's special characters, so it would slip
    # through unquoted and start a second command; reject it before that choice.
    if "\n" in value:
        raise ValueError("LLDB command arguments must not contain newlines")
    return quote_lldb_argument(value) if needs_quoting(value) else value


def build_source_list_command(
    *,
    file: str | None = None,
    line: int | None = None,
    function: str | None = None,
    address: str | None = None,
    linespec: str | None = None,
    count: int | None = None,
) -> str:
    """Build one ``source list`` command for exactly one selector.

    ``count`` is the LLDB ``-c`` value, which yields ``count + 1`` lines; use
    :func:`count_for_window` to ask for a specific number of lines. ``None``
    leaves LLDB's own default window in place.
    """

    selectors = [
        file is not None or line is not None,
        function is not None,
        address is not None,
        linespec is not None,
    ]
    if sum(1 for selected in selectors if selected) != 1:
        raise ValueError("exactly one of file/line, function, address, or linespec is required")
    if line is not None and file is None:
        raise ValueError("a line requires a file")
    if line is not None and line < 1:
        raise ValueError("line must be >= 1")
    if count is not None and count < 1:
        raise ValueError("count must be >= 1")

    parts = ["source list"]
    if file is not None:
        parts.extend(["-f", _argument(file)])
        if line is not None:
            parts.extend(["-l", str(line)])
    elif function is not None:
        parts.extend(["-n", _argument(function)])
    elif address is not None:
        parts.extend(["-a", _argument(address)])
    else:
        assert linespec is not None  # narrowed by the selector check above
        parts.extend(["-y", _argument(linespec)])
    if count is not None:
        parts.extend(["-c", str(count)])
    return " ".join(parts)


def count_for_window(window_lines: int) -> int:
    """Return the ``-c`` value that makes LLDB print ``window_lines`` lines."""

    if window_lines < 1:
        raise ValueError("window_lines must be >= 1")
    return window_lines - 1 if window_lines > 1 else 1


@dataclass(frozen=True, slots=True)
class SourceListLine:
    """One parsed source line."""

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class SourceListResult:
    """Parsed ``source list`` output."""

    file: str | None
    lines: tuple[SourceListLine, ...]
    errors: tuple[str, ...]

    @property
    def has_lines(self) -> bool:
        """Whether the adapter actually produced source text."""

        return bool(self.lines)


def parse_source_list_output(output: str) -> SourceListResult:
    """Parse ``source list`` output into lines, an optional file, and errors.

    Empty ``lines`` means "no source": LLDB reports a missing file as an error
    line, but a line past the end of the file, or embedded source queried by
    file, simply produce nothing.
    """

    file: str | None = None
    lines: list[SourceListLine] = []
    errors: list[str] = []
    for raw_line in output.splitlines():
        if _ECHO_RE.match(raw_line):
            continue
        header = _FILE_HEADER_RE.match(raw_line)
        if header is not None:
            file = header.group("path")
            continue
        error = _ERROR_RE.match(raw_line)
        if error is not None:
            errors.append(error.group("message"))
            continue
        match = _LINE_RE.match(raw_line)
        if match is not None:
            lines.append(
                SourceListLine(number=int(match.group("number")), text=match.group("text"))
            )
    return SourceListResult(file=file, lines=tuple(lines), errors=tuple(errors))


def select_window(
    lines: Iterable[SourceListLine], *, start_line: int, end_line: int
) -> tuple[SourceListLine, ...]:
    """Keep only the lines inside ``[start_line, end_line]``.

    ``-c`` overshoots by one line and LLDB clamps at the end of the file, so the
    window is always re-applied here instead of trusting the request.
    """

    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    return tuple(line for line in lines if start_line <= line.number <= end_line)


def first_line_number(lines: Sequence[SourceListLine]) -> int | None:
    """Return the lowest line number in ``lines``, if any."""

    return min((line.number for line in lines), default=None)
