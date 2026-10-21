"""Shared inferior-inventory parsing helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..domain import InferiorListInfo, InferiorRecord

# `info inferiors` renders a ui_out table: a `current` column holds the `*` row
# marker, then `Num` and `Description`, then optionally `Connection` (added in
# GDB 10) and `Executable` (added in GDB 7.1). Parsing at the header's column
# starts keeps descriptions and paths correct even though they contain spaces
# and either the connection or executable cell can be empty.
_HEADER_COLUMNS = ("Num", "Description", "Connection", "Executable")
_ROW_RE = re.compile(r"^(?P<current>\*)?\s*(?P<inferior_id>\d+)\s+(?P<columns>.*)$")
_COLUMN_SPLIT_RE = re.compile(r"\s{2,}")


def parse_inferiors_output(output: str, *, current_inferior_id: int | None) -> InferiorListInfo:
    """Parse `info inferiors` CLI output into a structured inventory snapshot."""

    lines = output.splitlines()
    header = _header_offsets(lines)
    if header is None:
        records = _parse_rows_by_whitespace(lines)
    else:
        header_index, offsets = header
        records = _parse_rows_by_columns(lines[header_index + 1 :], offsets)

    resolved_current_inferior_id = current_inferior_id
    for record in records:
        if record.is_current and record.inferior_id is not None:
            resolved_current_inferior_id = record.inferior_id

    return InferiorListInfo(
        inferiors=records,
        current_inferior_id=resolved_current_inferior_id,
    )


def inferior_ids(payload: InferiorListInfo) -> tuple[int, ...]:
    """Extract the numeric inferior identifiers from one inventory snapshot."""

    return tuple(
        record.inferior_id for record in payload.inferiors if isinstance(record.inferior_id, int)
    )


def looks_like_connection(value: str) -> bool:
    """Heuristically identify a connection column from `info inferiors` output."""

    return value.startswith(("target:", "process ", "remote ", "extended-remote")) or value in {
        "native",
    }


def _header_offsets(lines: Iterable[str]) -> tuple[int, dict[str, int]] | None:
    """Return the header line index and where each known column label starts."""

    for index, line in enumerate(lines):
        if not line.lstrip().startswith("Num"):
            continue

        offsets: dict[str, int] = {}
        cursor = 0
        for label in _HEADER_COLUMNS:
            found = line.find(label, cursor)
            if found < 0:
                continue
            offsets[label] = found
            cursor = found + len(label)

        if "Num" in offsets and "Description" in offsets:
            return index, offsets
    return None


def _parse_rows_by_columns(lines: Iterable[str], offsets: dict[str, int]) -> list[InferiorRecord]:
    """Slice each row at the header's column offsets, measured from the number.

    The caller strips the whole CLI output, so the header loses its leading
    spaces while the data rows keep theirs. Only the offsets *relative* to the
    number column survive that shift, so each row is anchored on its first
    digit and the header supplies the gaps between columns.
    """

    num_offset = offsets["Num"]
    relative = {label: offset - num_offset for label, offset in offsets.items()}
    columns = [(label, relative[label]) for label in _HEADER_COLUMNS if label in relative]
    description_gap = relative["Description"]

    records: list[InferiorRecord] = []
    for line in lines:
        if not line.strip():
            continue

        number_start = next((index for index, char in enumerate(line) if char.isdigit()), None)
        if number_start is None:
            continue

        record: dict[str, object] = {
            "inferior_id": int(
                "".join(
                    char
                    for char in line[number_start : number_start + description_gap]
                    if char.isdigit()
                )
            ),
            "is_current": line[:number_start].strip() == "*",
            "display": line[number_start + description_gap :].strip(),
        }

        for position, (label, gap) in enumerate(columns):
            if label == "Num":
                continue
            start = number_start + gap
            end = (
                number_start + columns[position + 1][1]
                if position + 1 < len(columns)
                else len(line)
            )
            value = line[start:end].strip() if start < len(line) else ""
            if not value:
                continue
            if label == "Description":
                record["description"] = value
            elif label == "Connection":
                record["connection"] = value
            elif label == "Executable":
                record["executable"] = value

        records.append(InferiorRecord.from_payload(record))
    return records


def _parse_rows_by_whitespace(lines: Iterable[str]) -> list[InferiorRecord]:
    """Fallback parser for rows when no recognizable header is present."""

    records: list[InferiorRecord] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue

        match = _ROW_RE.match(line)
        if match is None:
            continue

        columns = [
            part.strip() for part in _COLUMN_SPLIT_RE.split(match.group("columns")) if part.strip()
        ]
        record: dict[str, object] = {
            "inferior_id": int(match.group("inferior_id")),
            "is_current": match.group("current") == "*",
            "display": match.group("columns").strip(),
        }
        if columns:
            record["description"] = columns[0]
        if len(columns) == 2:
            if looks_like_connection(columns[1]):
                record["connection"] = columns[1]
            else:
                record["executable"] = columns[1]
        elif len(columns) >= 3:
            record["connection"] = columns[1]
            record["executable"] = columns[2]
        records.append(InferiorRecord.from_payload(record))
    return records
