"""Bounded reads for pygdbmi's non-blocking debugger streams."""

from __future__ import annotations

import os
import select
import time
from typing import BinaryIO, cast

from pygdbmi import gdbmiparser
from pygdbmi.constants import GdbTimeoutError
from pygdbmi.IoManager import IoManager

from .mi_models import MiRecord

_READ_SIZE = 64 * 1024
_BATCH_BYTES = 256 * 1024
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_POLL_INTERVAL_SEC = 0.001


class BoundedMiIoManager(IoManager):
    """Keep one poll finite even when the inferior writes without a pause.

    pygdbmi's reader only checks its deadline on iterations without records,
    and reads an unlimited amount of pipe data at once. Reuse its non-blocking
    setup, but bound each read and check a monotonic deadline on every pass.
    """

    def __init__(
        self,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO | None,
        time_to_check_for_additional_output_sec: float = 0.2,
        *,
        configure_nonblocking: bool = True,
    ) -> None:
        if configure_nonblocking:
            super().__init__(stdin, stdout, stderr, time_to_check_for_additional_output_sec)
        else:
            # External Windows sockets are already non-blocking; pygdbmi only
            # knows how to configure Windows pipe handles.
            self.stdin = stdin
            self.stdout = stdout
            self.stderr = stderr
            self.time_to_check_for_additional_output_sec = time_to_check_for_additional_output_sec
        self._streams = {"stdout": stdout}
        if stderr is not None:
            self._streams["stderr"] = stderr
        self._pending = {name: bytearray() for name in self._streams}

    def get_gdb_response(
        self, timeout_sec: float = 0.0, raise_error_on_timeout: bool = True
    ) -> list[MiRecord]:
        """Read a bounded batch, preserving partial lines for the next poll."""

        deadline = time.monotonic() + max(0.0, timeout_sec)
        responses: list[MiRecord] = []
        read_bytes = 0
        while self._streams:
            remaining = max(0.0, deadline - time.monotonic())
            if os.name == "nt":
                ready = list(self._streams.values())
            else:
                ready, _, _ = select.select(list(self._streams.values()), [], [], remaining)
            received = False
            for name, stream in list(self._streams.items()):
                if stream not in ready:
                    continue
                try:
                    chunk = stream.read(_READ_SIZE)
                except (BlockingIOError, InterruptedError):
                    continue
                if chunk is None:
                    continue
                if not chunk:
                    del self._streams[name]
                    chunk = b"\n" if self._pending[name] else b""
                received = received or bool(chunk)
                read_bytes += len(chunk)
                pending = self._pending[name]
                pending.extend(chunk)
                lines = pending.split(b"\n")
                self._pending[name] = lines.pop()
                for line in lines:
                    self._check_record_size(line)
                    text = line.decode(errors="replace")
                    if text and not gdbmiparser.response_is_finished(text):
                        record = gdbmiparser.parse_response(text)
                        record["stream"] = name
                        responses.append(cast(MiRecord, record))
                self._check_record_size(self._pending[name])

            now = time.monotonic()
            if responses and received and self.time_to_check_for_additional_output_sec > 0:
                deadline = min(deadline, now + self.time_to_check_for_additional_output_sec)
            if now >= deadline or read_bytes >= _BATCH_BYTES:
                break
            if os.name == "nt" and not received:
                time.sleep(min(_POLL_INTERVAL_SEC, deadline - now))

        if not responses and raise_error_on_timeout:
            raise GdbTimeoutError(f"Did not get response from gdb after {timeout_sec} seconds")
        return responses

    @staticmethod
    def _check_record_size(record: bytes | bytearray) -> None:
        if len(record) > _MAX_RECORD_BYTES:
            raise OSError(f"GDB/MI record exceeds the {_MAX_RECORD_BYTES}-byte limit")
