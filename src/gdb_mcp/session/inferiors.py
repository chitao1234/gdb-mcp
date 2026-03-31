"""Shared inferior-inventory parsing helpers."""

from __future__ import annotations

import re

from ..domain import InferiorListInfo, InferiorRecord

_INFERIOR_ROW_RE = re.compile(r"^(?P<current>\*)?\s*(?P<inferior_id>\d+)\s+(?P<columns>.*)$")
_INFERIOR_COLUMN_SPLIT_RE = re.compile(r"\s{2,}")


def parse_inferiors_output(output: str, *, current_inferior_id: int | None) -> InferiorListInfo:
    """Parse `info inferiors` CLI output into a structured inventory snapshot."""

    inferiors: list[InferiorRecord] = []
    resolved_current_inferior_id = current_inferior_id

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Num ") or stripped.startswith("Num\t"):
            continue

        match = _INFERIOR_ROW_RE.match(line)
        if match is None:
            continue

        inferior_id = int(match.group("inferior_id"))
        is_current = match.group("current") == "*"
        display = match.group("columns").strip()
        columns = [part.strip() for part in _INFERIOR_COLUMN_SPLIT_RE.split(display) if part.strip()]

        record: InferiorRecord = {
            "inferior_id": inferior_id,
            "is_current": is_current,
            "display": display,
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

        inferiors.append(record)
        if is_current:
            resolved_current_inferior_id = inferior_id

    return InferiorListInfo(
        inferiors=inferiors,
        count=len(inferiors),
        current_inferior_id=resolved_current_inferior_id,
    )


def inferior_ids(payload: InferiorListInfo) -> tuple[int, ...]:
    """Extract the numeric inferior identifiers from one inventory snapshot."""

    return tuple(
        record["inferior_id"]
        for record in payload.inferiors
        if isinstance(record.get("inferior_id"), int)
    )


def looks_like_connection(value: str) -> bool:
    """Heuristically identify a connection column from `info inferiors` output."""

    return value.startswith(("target:", "process ", "remote ", "extended-remote")) or value in {
        "native",
    }
