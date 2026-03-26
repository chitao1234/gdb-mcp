"""Inspection and navigation operations for a composed SessionService."""

from __future__ import annotations

import logging
from typing import Optional, cast

from ..domain import (
    BacktraceInfo,
    ExpressionValueInfo,
    FrameRecord,
    FrameInfo,
    FrameSelectionInfo,
    OperationError,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    ThreadRecord,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariableRecord,
    VariablesInfo,
)
from ..transport import build_evaluate_expression_command, extract_mi_result_payload
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_MAX_BACKTRACE_FRAMES, DEFAULT_TIMEOUT_SEC
from .result_utils import command_result_payload
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)


class SessionInspectionService:
    """Inspection and navigation helpers."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Get information about all threads in the debugged process."""
        logger.debug("get_threads() called")
        result = self._command_runner.execute_command_result("-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC)
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
        raw_threads = thread_info.get("threads", [])
        threads = raw_threads if isinstance(raw_threads, list) else []
        raw_current_thread = thread_info.get("current-thread-id")
        current_thread = raw_current_thread if isinstance(raw_current_thread, str) else None
        logger.debug(
            "get_threads: found %s threads, current_thread_id=%s", len(threads), current_thread
        )
        logger.debug("get_threads: threads data: %s", threads)

        return OperationSuccess(
            ThreadListInfo(
                threads=cast(list[ThreadRecord], threads),
                current_thread_id=current_thread,
                count=len(threads),
            )
        )

    def select_thread(self, thread_id: int) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Select a specific thread to make it the current thread."""
        result = self._command_runner.execute_command_result(
            f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        self._runtime.mark_thread_selected(thread_id)

        return OperationSuccess(
            ThreadSelectionInfo(
                thread_id=thread_id,
                new_thread_id=mi_result.get("new-thread-id")
                if isinstance(mi_result.get("new-thread-id"), str)
                else None,
                frame=cast(FrameRecord | None, mi_result.get("frame")),
            )
        )

    def get_backtrace(
        self, thread_id: Optional[int] = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Get the stack backtrace for a specific thread or the current thread."""
        if thread_id is not None:
            switch_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(switch_result, OperationError):
                return switch_result
            self._runtime.mark_thread_selected(thread_id)

        result = self._command_runner.execute_command_result(
            f"-stack-list-frames 0 {max_frames}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_stack_data = extract_mi_result_payload(command_result_payload(result)) or {}
        stack_data = raw_stack_data if isinstance(raw_stack_data, dict) else {}
        raw_frames = stack_data.get("stack", [])
        frames = raw_frames if isinstance(raw_frames, list) else []

        return OperationSuccess(
            BacktraceInfo(thread_id=thread_id, frames=cast(list[FrameRecord], frames), count=len(frames))
        )

    def get_frame_info(self) -> OperationSuccess[FrameInfo] | OperationError:
        """Get information about the current stack frame."""
        result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        raw_frame = mi_result.get("frame", {})
        frame = raw_frame if isinstance(raw_frame, dict) else {}

        return OperationSuccess(FrameInfo(frame=cast(FrameRecord, frame)))

    def select_frame(self, frame_number: int) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Select a specific stack frame to make it the current frame."""
        result = self._command_runner.execute_command_result(
            f"-stack-select-frame {frame_number}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        self._runtime.mark_frame_selected(frame_number)

        frame_info_result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(frame_info_result, OperationError):
            return OperationSuccess(
                FrameSelectionInfo(
                    frame_number=frame_number,
                    message=f"Frame {frame_number} selected",
                )
            )

        raw_payload = extract_mi_result_payload(command_result_payload(frame_info_result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        raw_frame = mi_result.get("frame", {})
        frame_info = raw_frame if isinstance(raw_frame, dict) else {}

        return OperationSuccess(
            FrameSelectionInfo(frame_number=frame_number, frame=cast(FrameRecord, frame_info))
        )

    def evaluate_expression(self, expression: str) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate an expression in the current context."""
        result = self._command_runner.execute_command_result(
            build_evaluate_expression_command(expression), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        value = mi_result.get("value")

        return OperationSuccess(ExpressionValueInfo(expression=expression, value=value))

    def get_variables(
        self, thread_id: Optional[int] = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Get local variables for a specific frame."""
        if thread_id is not None:
            thread_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_result, OperationError):
                return thread_result
            self._runtime.mark_thread_selected(thread_id)

        frame_result = self._command_runner.execute_command_result(
            f"-stack-select-frame {frame}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(frame_result, OperationError):
            return frame_result
        self._runtime.mark_frame_selected(frame)

        result = self._command_runner.execute_command_result(
            "-stack-list-variables --simple-values", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        raw_variables = mi_result.get("variables", [])
        variables = raw_variables if isinstance(raw_variables, list) else []

        return OperationSuccess(
            VariablesInfo(thread_id=thread_id, frame=frame, variables=cast(list[VariableRecord], variables))
        )

    def get_registers(self) -> OperationSuccess[RegistersInfo] | OperationError:
        """Get register values for current frame."""
        result = self._command_runner.execute_command_result(
            "-data-list-register-values x", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result)) or {}
        mi_result = raw_payload if isinstance(raw_payload, dict) else {}
        raw_registers = mi_result.get("register-values", [])
        registers = raw_registers if isinstance(raw_registers, list) else []

        return OperationSuccess(RegistersInfo(registers=cast(list[RegisterRecord], registers)))
