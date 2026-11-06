"""Keep campaign responses small and preserve complete iteration evidence."""

from __future__ import annotations

import json
import tempfile
from collections import deque
from dataclasses import replace
from pathlib import Path

from ..domain import RunUntilFailureIterationInfo, StructuredPayload, payload_to_mapping

_MAX_INLINE_ITERATIONS = 1000
_MAX_INLINE_RESULT_CHARS = 256 * 1024
_MAX_INLINE_WIRE_BYTES = 8 * 1024 * 1024


class CampaignHistory:
    """Spill complete iterations to JSONL when inline history exceeds its budget."""

    def __init__(self) -> None:
        self.iterations: deque[RunUntilFailureIterationInfo] = deque()
        self._inline_sizes: deque[int] = deque()
        self._inline_bytes = 0
        self.completed = 0
        self.path: Path | None = None

    def append(self, iteration: RunUntilFailureIterationInfo) -> None:
        """Store the full result before replacing an oversized inline envelope."""

        oversized = len(json.dumps(iteration.result, indent=2)) > _MAX_INLINE_RESULT_CHARS
        inline_iteration = (
            replace(iteration, result=self._summary(iteration.result), result_truncated=True)
            if oversized
            else iteration
        )
        inline_size = self._inline_size(inline_iteration)
        over_budget = self._inline_bytes + inline_size > _MAX_INLINE_WIRE_BYTES
        if self.path is None and (
            oversized or len(self.iterations) == _MAX_INLINE_ITERATIONS or over_budget
        ):
            directory = Path(tempfile.mkdtemp(prefix="gdb-mcp-campaign-"))
            self.path = directory / "iterations.jsonl"
            with self.path.open("w", encoding="utf-8") as output:
                for previous in self.iterations:
                    output.write(json.dumps(payload_to_mapping(previous)) + "\n")
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(payload_to_mapping(iteration)) + "\n")
        while self.iterations and (
            len(self.iterations) >= _MAX_INLINE_ITERATIONS
            or self._inline_bytes + inline_size > _MAX_INLINE_WIRE_BYTES
        ):
            self.iterations.popleft()
            self._inline_bytes -= self._inline_sizes.popleft()
        self.iterations.append(inline_iteration)
        self._inline_sizes.append(inline_size)
        self._inline_bytes += inline_size
        self.completed += 1

    @staticmethod
    def _inline_size(iteration: RunUntilFailureIterationInfo) -> int:
        """Budget both MCP copies, including JSON escaping and nested indentation."""

        payload = payload_to_mapping(iteration)
        # The text envelope nests each iteration six spaces inside result.iterations.
        # ASCII escaping conservatively bounds both ASCII and UTF-8 serialization.
        text = json.dumps(payload, indent=2).replace("\n", "\n      ")
        structured = json.dumps(payload, separators=(",", ":"))
        return len(json.dumps(text)) + len(structured) + 4

    @staticmethod
    def _summary(envelope: StructuredPayload) -> StructuredPayload:
        """Preserve the outcome while moving bulky operation details to disk."""

        if envelope.get("status") == "error":
            summary: StructuredPayload = {"status": "error"}
            for key, limit in (("code", 128), ("message", 2048)):
                value = envelope.get(key)
                if isinstance(value, str):
                    summary[key] = value[:limit]
            if envelope.get("fatal") is True:
                summary["fatal"] = True
            return summary
        return {
            "status": "success",
            "result": {"message": "Large result saved in iterations_path."},
        }
