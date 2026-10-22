"""Thread, backtrace, and frame selection methods."""

from __future__ import annotations

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
from ...domain.parsing import as_int
from ..constants import DEFAULT_MAX_BACKTRACE_FRAMES, DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_mapping
from .base import (
    InspectionContext,
    logger,
)


class InspectionThreads:
    """Thread, backtrace, and frame selection methods."""

    def __init__(self, context: InspectionContext):
        self._context = context
        self._runtime = context.runtime
        self._command_runner = context.command_runner

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Get information about all threads in the debugged process."""
        result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        thread_info = command_result_mapping(result)

        if not thread_info:
            logger.warning("get_threads: GDB returned incomplete data")
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

        raw_payload = command_result_mapping(result)
        thread_info = thread_selection_info_from_payload(thread_id, raw_payload)
        if thread_info.frame is not None:
            self._runtime.mark_frame_selected(as_int(thread_info.frame.level))
        self._runtime.mark_thread_selected(thread_id)

        return OperationSuccess(thread_info)

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Get the stack backtrace for a specific thread or the current thread."""
        selection = self._context.capture_selection() if thread_id is not None else None
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
                restore_error = self._context.restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        effective_thread_id = (
            thread_id if thread_id is not None else self._runtime.current_thread_id
        )
        payload = backtrace_info_from_payload(
            effective_thread_id,
            command_result_mapping(result),
        )

        if selection is not None and selection.thread_id != thread_id:
            restore_error = self._context.restore_selection(selection)
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
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return self._context.selection_error_with_restore(selection, result)

        frame_info = frame_info_from_payload(command_result_mapping(result))
        frame_details = frame_info.frame
        level = frame_details.level if frame_details is not None else None
        self._runtime.mark_frame_selected(as_int(level))

        if selection is not None:
            restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
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
                command_result_mapping(frame_info_result),
            )
        )
