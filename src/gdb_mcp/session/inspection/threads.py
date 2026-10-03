"""Thread, backtrace, and frame selection methods."""

from __future__ import annotations

from typing import Optional

from ...domain import (
    BacktraceInfo,
    FrameInfo,
    FrameSelectionInfo,
    OperationError,
    OperationSuccess,
    ThreadListInfo,
    ThreadSelectionInfo,
    backtrace_info_from_payload,
    frame_info_from_payload,
    frame_selection_info_from_payload,
    thread_list_info_from_payload,
    thread_selection_info_from_payload,
)
from ...transport import (
    extract_mi_result_payload,
)
from ..constants import DEFAULT_MAX_BACKTRACE_FRAMES, DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from .base import (
    InspectionBase,
    logger,
)


class InspectionThreadsMixin(InspectionBase):
    """Thread, backtrace, and frame selection methods."""

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Get information about all threads in the debugged process."""
        result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        thread_info = extract_mi_result_payload(command_result_payload(result))

        if thread_info is None:
            logger.warning("get_threads: thread_info is None - GDB returned incomplete data")
            return OperationError(
                message="GDB returned incomplete data - may still be loading symbols"
            )
        payload = thread_list_info_from_payload(thread_info)

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

        effective_thread_id = (
            thread_id if thread_id is not None else self._runtime.current_thread_id
        )
        payload = backtrace_info_from_payload(
            effective_thread_id,
            extract_mi_result_payload(command_result_payload(result)),
        )

        if selection is not None and selection.thread_id != thread_id:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(payload)

    def get_frame_info(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[FrameInfo] | OperationError:
        """Get information about the current stack frame or an overridden context."""

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
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return self._selection_error_with_restore(selection, result)

        frame_info = frame_info_from_payload(
            extract_mi_result_payload(command_result_payload(result))
        )
        level = frame_info.frame.get("level")
        self._runtime.mark_frame_selected(self._int_or_none(level))

        if selection is not None:
            restore_error = self._restore_selection_if_changed(selection, selection_changed)
            if restore_error is not None:
                return restore_error

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
