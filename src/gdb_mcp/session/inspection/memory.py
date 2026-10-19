"""Expression evaluation and memory read methods."""

from __future__ import annotations

from collections.abc import Mapping

from ...domain import (
    ExpressionValueInfo,
    MemoryBlockRecord,
    MemoryReadInfo,
    OperationError,
    OperationSuccess,
    memory_block_records,
)
from ...domain.parsing import as_record_list, as_text
from ...transport import (
    build_evaluate_expression_command,
    build_read_memory_command,
    extract_mi_result_payload,
    quote_mi_string,
)
from ..constants import DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from .base import (
    InspectionContext,
)

_FEATURE_READ_MEMORY_BYTES = "data-read-memory-bytes"


def _legacy_memory_contents(payload: Mapping[str, object] | None) -> str:
    """Flatten a `-data-read-memory` table into one hex string.

    That command performs a single read and marks every byte behind the first
    unreadable one `N/A`, so the readable prefix is all the data there is.
    """

    if payload is None:
        return ""
    rows = as_record_list(payload.get("memory"))

    parts: list[str] = []
    for row in rows:
        values = row.get("data")
        if not isinstance(values, list):
            continue
        for value in values:
            text = as_text(value)
            if text is None or text == "N/A":
                return "".join(parts)
            parts.append(text[2:].zfill(2) if text.lower().startswith("0x") else text)
    return "".join(parts)


def _legacy_memory_block(
    payload: Mapping[str, object] | None,
    contents: str,
) -> MemoryBlockRecord:
    """Build one bytes-style block from a `-data-read-memory` payload."""

    block: dict[str, object] = {"offset": "0x0", "contents": contents}
    start = as_text(payload.get("addr")) if payload is not None else None
    if start is not None:
        block["begin"] = start
        try:
            block["end"] = hex(int(start, 16) + len(contents) // 2)
        except ValueError:
            pass
    return MemoryBlockRecord.from_payload(block)


def _read_memory_warnings(info: MemoryReadInfo) -> tuple[str, ...]:
    """Warn when a read did not return every requested byte in one contiguous run."""

    warnings: list[str] = []
    if info.block_count > 1:
        warnings.append(f"Discontinuous read: {info.block_count} blocks returned")
    if info.captured_bytes < info.count:
        warnings.append(f"Partial read: captured {info.captured_bytes} of {info.count} bytes")
    return tuple(warnings)


class InspectionMemory:
    """Expression evaluation and memory read methods."""

    def __init__(self, context: InspectionContext):
        self._context = context
        self._runtime = context.runtime
        self._command_runner = context.command_runner

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate an expression in the current context."""
        selection = (
            self._context.capture_selection()
            if thread_id is not None or frame is not None
            else None
        )
        if isinstance(selection, OperationError):
            return selection

        selection_changed, selection_error = self._context.select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._context.selection_error_with_restore(selection, selection_error)

        result = self._command_runner.execute_command_result(
            build_evaluate_expression_command(expression), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None:
                restore_error = self._context.restore_selection_if_changed(
                    selection, selection_changed
                )
                if restore_error is not None:
                    return restore_error
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        value = raw_payload.get("value") if isinstance(raw_payload, dict) else None
        if selection is not None:
            restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(ExpressionValueInfo(expression=expression, value=value))

    def read_memory(
        self,
        address: str,
        count: int,
        *,
        offset: int = 0,
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Read raw target memory bytes from one address expression."""

        if self._supports_read_memory_bytes():
            result = self._read_memory_bytes(address, count, offset=offset)
        else:
            result = self._read_memory_legacy(address, count, offset=offset)
        if isinstance(result, OperationError):
            return result

        warnings = _read_memory_warnings(result.value)
        if warnings:
            return OperationSuccess(result.value, warnings=warnings)
        return result

    def _read_memory_bytes(
        self,
        address: str,
        count: int,
        *,
        offset: int,
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Read memory through `-data-read-memory-bytes` (GDB >= 7.3)."""

        result = self._command_runner.execute_command_result(
            build_read_memory_command(address, count, offset=offset),
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )

        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        blocks = memory_block_records(payload)
        captured_bytes = sum(len(block.contents or "") // 2 for block in blocks)
        return OperationSuccess(
            MemoryReadInfo(
                address=address,
                count=count,
                offset=offset,
                blocks=blocks,
                block_count=len(blocks),
                captured_bytes=captured_bytes,
            )
        )

    def _read_memory_legacy(
        self,
        address: str,
        count: int,
        *,
        offset: int,
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Read memory through `-data-read-memory` on GDB older than 7.3.

        The older command returns one formatted table rather than a block list,
        so only the readable prefix of the request survives; bytes behind an
        unreadable region come back as `N/A` and are dropped.
        """

        offset_args = f"-o {offset} " if offset else ""
        command = f"-data-read-memory {offset_args}{quote_mi_string(address)} x 1 1 {count}"
        result = self._command_runner.execute_command_result(
            command, timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        contents = _legacy_memory_contents(payload)
        blocks = [_legacy_memory_block(payload, contents)] if contents else []
        return OperationSuccess(
            MemoryReadInfo(
                address=address,
                count=count,
                offset=offset,
                blocks=blocks,
                block_count=len(blocks),
                captured_bytes=len(contents) // 2,
            )
        )

    def _supports_read_memory_bytes(self) -> bool:
        """Return whether this GDB supports `-data-read-memory-bytes` (GDB >= 7.3)."""

        cached = self._runtime.read_memory_bytes_supported
        if cached is not None:
            return cached

        result = self._command_runner.execute_command_result(
            "-list-features", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        supported = False
        if isinstance(result, OperationSuccess):
            payload = extract_mi_result_payload(command_result_payload(result))
            if payload is not None:
                features = payload.get("features")
                if isinstance(features, list):
                    supported = _FEATURE_READ_MEMORY_BYTES in features
        self._runtime.read_memory_bytes_supported = supported
        return supported
