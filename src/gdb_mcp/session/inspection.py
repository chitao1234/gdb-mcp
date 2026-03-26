"""Inspection and navigation operations for SessionService."""

from __future__ import annotations

import logging
from typing import Optional

from ..transport import extract_mi_result_payload
from .constants import DEFAULT_MAX_BACKTRACE_FRAMES

logger = logging.getLogger(__name__)


class SessionInspectionMixin:
    """Inspection and navigation methods used by SessionService."""

    def get_threads(self) -> dict[str, object]:
        """Get information about all threads in the debugged process."""
        logger.debug("get_threads() called")
        result = self.execute_command("-thread-info")
        logger.debug("get_threads: execute_command returned: %s", result)

        if result["status"] == "error":
            logger.debug("get_threads: returning error from execute_command")
            return result

        thread_info = extract_mi_result_payload(result)
        logger.debug("get_threads: thread_info type=%s, value=%s", type(thread_info), thread_info)

        if thread_info is None:
            logger.warning("get_threads: thread_info is None - GDB returned incomplete data")
            return {
                "status": "error",
                "message": "GDB returned incomplete data - may still be loading symbols",
            }

        if not isinstance(thread_info, dict):
            thread_info = {}
        threads = thread_info.get("threads", [])
        current_thread = thread_info.get("current-thread-id")
        logger.debug(
            "get_threads: found %s threads, current_thread_id=%s", len(threads), current_thread
        )
        logger.debug("get_threads: threads data: %s", threads)

        return {
            "status": "success",
            "threads": threads,
            "current_thread_id": current_thread,
            "count": len(threads),
        }

    def select_thread(self, thread_id: int) -> dict[str, object]:
        """Select a specific thread to make it the current thread."""
        result = self.execute_command(f"-thread-select {thread_id}")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}

        return {
            "status": "success",
            "thread_id": thread_id,
            "new_thread_id": mi_result.get("new-thread-id"),
            "frame": mi_result.get("frame"),
        }

    def get_backtrace(
        self, thread_id: Optional[int] = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> dict[str, object]:
        """Get the stack backtrace for a specific thread or the current thread."""
        if thread_id is not None:
            switch_result = self.execute_command(f"-thread-select {thread_id}")
            if switch_result["status"] == "error":
                return switch_result

        result = self.execute_command(f"-stack-list-frames 0 {max_frames}")

        if result["status"] == "error":
            return result

        stack_data = extract_mi_result_payload(result) or {}
        frames = stack_data.get("stack", [])

        return {"status": "success", "thread_id": thread_id, "frames": frames, "count": len(frames)}

    def get_frame_info(self) -> dict[str, object]:
        """Get information about the current stack frame."""
        result = self.execute_command("-stack-info-frame")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        frame = mi_result.get("frame", {})

        return {"status": "success", "frame": frame}

    def select_frame(self, frame_number: int) -> dict[str, object]:
        """Select a specific stack frame to make it the current frame."""
        result = self.execute_command(f"-stack-select-frame {frame_number}")

        if result["status"] == "error":
            return result

        frame_info_result = self.execute_command("-stack-info-frame")

        if frame_info_result["status"] == "error":
            return {
                "status": "success",
                "frame_number": frame_number,
                "message": f"Frame {frame_number} selected",
            }

        mi_result = extract_mi_result_payload(frame_info_result) or {}
        frame_info = mi_result.get("frame", {})

        return {
            "status": "success",
            "frame_number": frame_number,
            "frame": frame_info,
        }

    def evaluate_expression(self, expression: str) -> dict[str, object]:
        """Evaluate an expression in the current context."""
        result = self.execute_command(f'-data-evaluate-expression "{expression}"')

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        value = mi_result.get("value")

        return {"status": "success", "expression": expression, "value": value}

    def get_variables(self, thread_id: Optional[int] = None, frame: int = 0) -> dict[str, object]:
        """Get local variables for a specific frame."""
        if thread_id is not None:
            thread_result = self.execute_command(f"-thread-select {thread_id}")
            if thread_result.get("status") == "error":
                return thread_result

        frame_result = self.execute_command(f"-stack-select-frame {frame}")
        if frame_result.get("status") == "error":
            return frame_result

        result = self.execute_command("-stack-list-variables --simple-values")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        variables = mi_result.get("variables", [])

        return {"status": "success", "thread_id": thread_id, "frame": frame, "variables": variables}

    def get_registers(self) -> dict[str, object]:
        """Get register values for current frame."""
        result = self.execute_command("-data-list-register-values x")

        if result["status"] == "error":
            return result

        mi_result = extract_mi_result_payload(result) or {}
        registers = mi_result.get("register-values", [])

        return {"status": "success", "registers": registers}
