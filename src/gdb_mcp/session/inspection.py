"""Inspection and navigation operations for SessionService."""

from __future__ import annotations

import logging
from typing import Optional

from ..domain import (
    BacktraceInfo,
    ExpressionValueInfo,
    FrameInfo,
    FrameSelectionInfo,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
)
from ..transport import build_evaluate_expression_command, extract_mi_result_payload
from .constants import DEFAULT_MAX_BACKTRACE_FRAMES
from .result_utils import command_result_payload

logger = logging.getLogger(__name__)


class SessionInspectionMixin:
    """Inspection and navigation methods used by SessionService."""

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Get information about all threads in the debugged process."""
        logger.debug("get_threads() called")
        result = self._execute_command_result("-thread-info")
        logger.debug("get_threads: execute_command returned: %s", result)

        if isinstance(result, OperationError):
            logger.debug("get_threads: returning error from execute_command")
            return result

        thread_info = extract_mi_result_payload(command_result_payload(result))
        logger.debug("get_threads: thread_info type=%s, value=%s", type(thread_info), thread_info)

        if thread_info is None:
            logger.warning("get_threads: thread_info is None - GDB returned incomplete data")
            return OperationError(
                message="GDB returned incomplete data - may still be loading symbols"
            )

        if not isinstance(thread_info, dict):
            thread_info = {}
        threads = thread_info.get("threads", [])
        current_thread = thread_info.get("current-thread-id")
        logger.debug(
            "get_threads: found %s threads, current_thread_id=%s", len(threads), current_thread
        )
        logger.debug("get_threads: threads data: %s", threads)

        return OperationSuccess(
            ThreadListInfo(
                threads=threads,
                current_thread_id=current_thread,
                count=len(threads),
            )
        )

    def select_thread(self, thread_id: int) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Select a specific thread to make it the current thread."""
        result = self._execute_command_result(f"-thread-select {thread_id}")

        if isinstance(result, OperationError):
            return result

        mi_result = extract_mi_result_payload(command_result_payload(result)) or {}

        return OperationSuccess(
            ThreadSelectionInfo(
                thread_id=thread_id,
                new_thread_id=mi_result.get("new-thread-id"),
                frame=mi_result.get("frame"),
            )
        )

    def get_backtrace(
        self, thread_id: Optional[int] = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Get the stack backtrace for a specific thread or the current thread."""
        if thread_id is not None:
            switch_result = self._execute_command_result(f"-thread-select {thread_id}")
            if isinstance(switch_result, OperationError):
                return switch_result

        result = self._execute_command_result(f"-stack-list-frames 0 {max_frames}")

        if isinstance(result, OperationError):
            return result

        stack_data = extract_mi_result_payload(command_result_payload(result)) or {}
        frames = stack_data.get("stack", [])

        return OperationSuccess(BacktraceInfo(thread_id=thread_id, frames=frames, count=len(frames)))

    def get_frame_info(self) -> OperationSuccess[FrameInfo] | OperationError:
        """Get information about the current stack frame."""
        result = self._execute_command_result("-stack-info-frame")

        if isinstance(result, OperationError):
            return result

        mi_result = extract_mi_result_payload(command_result_payload(result)) or {}
        frame = mi_result.get("frame", {})

        return OperationSuccess(FrameInfo(frame=frame))

    def select_frame(self, frame_number: int) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Select a specific stack frame to make it the current frame."""
        result = self._execute_command_result(f"-stack-select-frame {frame_number}")

        if isinstance(result, OperationError):
            return result

        frame_info_result = self._execute_command_result("-stack-info-frame")

        if isinstance(frame_info_result, OperationError):
            return OperationSuccess(
                FrameSelectionInfo(
                    frame_number=frame_number,
                    message=f"Frame {frame_number} selected",
                )
            )

        mi_result = extract_mi_result_payload(command_result_payload(frame_info_result)) or {}
        frame_info = mi_result.get("frame", {})

        return OperationSuccess(FrameSelectionInfo(frame_number=frame_number, frame=frame_info))

    def evaluate_expression(self, expression: str) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate an expression in the current context."""
        result = self._execute_command_result(build_evaluate_expression_command(expression))

        if isinstance(result, OperationError):
            return result

        mi_result = extract_mi_result_payload(command_result_payload(result)) or {}
        value = mi_result.get("value")

        return OperationSuccess(ExpressionValueInfo(expression=expression, value=value))

    def get_variables(
        self, thread_id: Optional[int] = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Get local variables for a specific frame."""
        if thread_id is not None:
            thread_result = self._execute_command_result(f"-thread-select {thread_id}")
            if isinstance(thread_result, OperationError):
                return thread_result

        frame_result = self._execute_command_result(f"-stack-select-frame {frame}")
        if isinstance(frame_result, OperationError):
            return frame_result

        result = self._execute_command_result("-stack-list-variables --simple-values")

        if isinstance(result, OperationError):
            return result

        mi_result = extract_mi_result_payload(command_result_payload(result)) or {}
        variables = mi_result.get("variables", [])

        return OperationSuccess(VariablesInfo(thread_id=thread_id, frame=frame, variables=variables))

    def get_registers(self) -> OperationSuccess[RegistersInfo] | OperationError:
        """Get register values for current frame."""
        result = self._execute_command_result("-data-list-register-values x")

        if isinstance(result, OperationError):
            return result

        mi_result = extract_mi_result_payload(command_result_payload(result)) or {}
        registers = mi_result.get("register-values", [])

        return OperationSuccess(RegistersInfo(registers=registers))
