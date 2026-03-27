"""Inspection and navigation operations for a composed SessionService."""

from __future__ import annotations

from dataclasses import dataclass
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
    backtrace_info_from_payload,
    frame_info_from_payload,
    frame_selection_info_from_payload,
    registers_info_from_payload,
    thread_list_info_from_payload,
    thread_selection_info_from_payload,
    variables_info_from_payload,
)
from ..transport import build_evaluate_expression_command, extract_mi_result_payload
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_MAX_BACKTRACE_FRAMES, DEFAULT_TIMEOUT_SEC
from .result_utils import command_result_payload
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SelectionSnapshot:
    """Current thread/frame selection captured for temporary inspection changes."""

    thread_id: int | None
    frame_number: int | None


class SessionInspectionService:
    """Inspection and navigation helpers."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Get information about all threads in the debugged process."""
        logger.debug("get_threads() called")
        result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
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
        payload = thread_list_info_from_payload(thread_info)
        logger.debug(
            "get_threads: found %s threads, current_thread_id=%s",
            payload.count,
            payload.current_thread_id,
        )
        logger.debug("get_threads: threads data: %s", payload.threads)

        return OperationSuccess(payload)

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Select a specific thread to make it the current thread."""
        result = self._command_runner.execute_command_result(
            f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        if isinstance(raw_payload, dict):
            frame_payload = raw_payload.get("frame")
            if isinstance(frame_payload, dict):
                self._runtime.mark_frame_selected(self._int_or_none(frame_payload.get("level")))
        self._runtime.mark_thread_selected(thread_id)

        return OperationSuccess(
            thread_selection_info_from_payload(
                thread_id,
                raw_payload,
            )
        )

    def get_backtrace(
        self, thread_id: Optional[int] = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Get the stack backtrace for a specific thread or the current thread."""
        selection = self._capture_selection() if thread_id is not None else None
        if isinstance(selection, OperationError):
            return selection

        if thread_id is not None and (selection is None or selection.thread_id != thread_id):
            switch_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(switch_result, OperationError):
                return switch_result

        result = self._command_runner.execute_command_result(
            f"-stack-list-frames 0 {max_frames - 1}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None and selection.thread_id != thread_id:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        payload = backtrace_info_from_payload(
            thread_id,
            extract_mi_result_payload(command_result_payload(result)),
        )

        if selection is not None and selection.thread_id != thread_id:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(payload)

    def get_frame_info(self) -> OperationSuccess[FrameInfo] | OperationError:
        """Get information about the current stack frame."""
        result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        frame_info = frame_info_from_payload(
            extract_mi_result_payload(command_result_payload(result))
        )
        level = frame_info.frame.get("level")
        self._runtime.mark_frame_selected(self._int_or_none(level))

        return OperationSuccess(frame_info)

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
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

        return OperationSuccess(
            frame_selection_info_from_payload(
                frame_number,
                extract_mi_result_payload(command_result_payload(frame_info_result)),
            )
        )

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

        selection_error = self._select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return selection_error

        result = self._command_runner.execute_command_result(
            build_evaluate_expression_command(expression), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        value = raw_payload.get("value") if isinstance(raw_payload, dict) else None
        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(ExpressionValueInfo(expression=expression, value=value))

    def get_variables(
        self, thread_id: Optional[int] = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Get local variables for a specific frame."""
        selection = self._capture_selection()
        if isinstance(selection, OperationError):
            return selection

        if thread_id is not None and selection.thread_id != thread_id:
            thread_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_result, OperationError):
                return thread_result

        if selection.frame_number != frame:
            frame_result = self._command_runner.execute_command_result(
                f"-stack-select-frame {frame}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(frame_result, OperationError):
                return frame_result

        result = self._command_runner.execute_command_result(
            "-stack-list-variables --simple-values", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error
            return result

        payload = variables_info_from_payload(
            thread_id,
            frame,
            extract_mi_result_payload(command_result_payload(result)),
        )
        restore_error = self._restore_selection(selection)
        if restore_error is not None:
            return restore_error

        return OperationSuccess(payload)

    def get_registers(
        self,
        thread_id: Optional[int] = None,
        frame: Optional[int] = None,
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        """Get register values for current frame."""
        selection = (
            self._capture_selection() if thread_id is not None or frame is not None else None
        )
        if isinstance(selection, OperationError):
            return selection

        selection_error = self._select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return selection_error

        result = self._command_runner.execute_command_result(
            "-data-list-register-values x", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        payload = registers_info_from_payload(
            extract_mi_result_payload(command_result_payload(result))
        )
        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(payload)

    def _capture_selection(self) -> _SelectionSnapshot | OperationError:
        """Capture the currently selected thread and frame for later restoration."""

        thread_result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(thread_result, OperationError):
            return thread_result

        thread_payload = extract_mi_result_payload(command_result_payload(thread_result))
        current_thread = None
        if isinstance(thread_payload, dict):
            current_thread = self._int_or_none(thread_payload.get("current-thread-id"))

        frame_result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(frame_result, OperationError):
            return frame_result

        frame_payload = extract_mi_result_payload(command_result_payload(frame_result))
        current_frame = None
        if isinstance(frame_payload, dict):
            current_frame_payload = frame_payload.get("frame")
            if isinstance(current_frame_payload, dict):
                current_frame = self._int_or_none(current_frame_payload.get("level"))

        self._runtime.mark_thread_selected(current_thread)
        self._runtime.mark_frame_selected(current_frame)

        return _SelectionSnapshot(thread_id=current_thread, frame_number=current_frame)

    def _restore_selection(self, selection: _SelectionSnapshot) -> OperationError | None:
        """Restore a previously captured debugger selection."""

        if selection.thread_id is not None:
            thread_restore = self._command_runner.execute_command_result(
                f"-thread-select {selection.thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_restore, OperationError):
                return OperationError(
                    message=(
                        "Inspection completed but failed to restore the original thread selection: "
                        f"{thread_restore.message}"
                    )
                )

        if selection.frame_number is not None:
            frame_restore = self._command_runner.execute_command_result(
                f"-stack-select-frame {selection.frame_number}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if isinstance(frame_restore, OperationError):
                return OperationError(
                    message=(
                        "Inspection completed but failed to restore the original frame selection: "
                        f"{frame_restore.message}"
                    )
                )

        self._runtime.mark_thread_selected(selection.thread_id)
        self._runtime.mark_frame_selected(selection.frame_number)
        return None

    def _select_for_inspection(
        self,
        selection: _SelectionSnapshot | None,
        *,
        thread_id: int | None,
        frame: int | None,
    ) -> OperationError | None:
        """Temporarily switch thread/frame for one inspection call."""

        if selection is not None and thread_id is not None and selection.thread_id != thread_id:
            thread_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_result, OperationError):
                return thread_result

        if selection is not None and frame is not None and selection.frame_number != frame:
            frame_result = self._command_runner.execute_command_result(
                f"-stack-select-frame {frame}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(frame_result, OperationError):
                return frame_result

        return None

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        """Parse a GDB string/integer field into an integer when possible."""

        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None
