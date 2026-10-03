"""Expression evaluation and memory read methods."""

from __future__ import annotations

from typing import Optional

from ...domain import (
    ExpressionValueInfo,
    MemoryReadInfo,
    OperationError,
    OperationSuccess,
    memory_block_records,
)
from ...transport import (
    build_evaluate_expression_command,
    build_read_memory_command,
    extract_mi_result_payload,
)
from ..constants import DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from .base import (
    InspectionBase,
)


class InspectionMemoryMixin(InspectionBase):
    """Expression evaluation and memory read methods."""

    def evaluate_expression(
        self,
        expression: str,
        thread_id: Optional[int] = None,
        frame: Optional[int] = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate an expression in the current context."""
        selection = (
            self._capture_selection() if thread_id is not None or frame is not None else None
        )
        if isinstance(selection, OperationError):
            return selection

        selection_changed, selection_error = self._select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._selection_error_with_restore(selection, selection_error)

        result = self._command_runner.execute_command_result(
            build_evaluate_expression_command(expression), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None:
                restore_error = self._restore_selection_if_changed(selection, selection_changed)
                if restore_error is not None:
                    return restore_error
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        value = raw_payload.get("value") if isinstance(raw_payload, dict) else None
        if selection is not None:
            restore_error = self._restore_selection_if_changed(selection, selection_changed)
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

        result = self._command_runner.execute_command_result(
            build_read_memory_command(address, count, offset=offset),
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )

        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        blocks = memory_block_records(payload)
        captured_bytes = sum(len(block.get("contents", "")) // 2 for block in blocks)
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
