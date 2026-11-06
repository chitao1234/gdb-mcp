"""Bound retained MI traffic while continuing to drain the debugger."""

from .mi_models import MiRecord

_MAX_STREAM_CHARS = 256 * 1024
_MAX_RECORDS = 1024
_TRUNCATION_MESSAGE = "\n[gdb-mcp: debugger output truncated]\n"


class MiRecordBuffer:
    """Retain command results and recent notifications within an output budget."""

    def __init__(self) -> None:
        self._records: list[MiRecord] = []
        self._stream_chars = 0
        self._truncated = False
        self._result: MiRecord | None = None
        self._stop: MiRecord | None = None

    def append(self, record: MiRecord) -> None:
        """Limit streams independently of result and stop records."""

        if record.get("type") == "result":
            self._records = [item for item in self._records if item is not self._result]
            self._result = record
        if (
            self._stop is None
            and record.get("type") == "notify"
            and record.get("message") == "stopped"
        ):
            self._stop = record
        if record.get("type") in {"console", "log", "output", "target"}:
            payload = record.get("payload")
            if isinstance(payload, str):
                available = _MAX_STREAM_CHARS - self._stream_chars
                self._stream_chars += min(len(payload), available)
                if len(payload) > available:
                    self._truncated = True
                    if not available:
                        return
                    record = {**record, "payload": payload[:available]}

        if len(self._records) >= _MAX_RECORDS:
            # The result and first observed stop must survive later traffic.
            index = next(
                (i for i, item in enumerate(self._records) if not self._is_control(item)),
                None,
            )
            if index is not None:
                self._records.pop(index)
                self._truncated = True
        self._records.append(record)

    def _is_control(self, record: MiRecord) -> bool:
        return record is self._result or record is self._stop

    @property
    def records(self) -> list[MiRecord]:
        """Return retained records with an explicit truncation marker."""

        if self._truncated:
            return [*self._records, {"type": "console", "payload": _TRUNCATION_MESSAGE}]
        return list(self._records)
