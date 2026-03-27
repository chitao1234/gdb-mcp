"""Tests for session runtime workflow foundations."""

from __future__ import annotations

from gdb_mcp.domain import CommandTranscriptEntry, StopEvent
from gdb_mcp.session.config import SessionConfig


class TestRuntimeFoundations:
    """Test bounded runtime state used by higher-level workflow tools."""

    def test_begin_startup_clears_stop_history_and_transcript(self, session_service):
        """New startup attempts should clear prior execution observations."""

        session_service.runtime.record_stop_event(
            StopEvent(execution_state="paused", reason="breakpoint-hit")
        )
        session_service.runtime.record_command_transcript(
            CommandTranscriptEntry(command="-exec-run")
        )

        session_service.runtime.begin_startup(
            SessionConfig.from_inputs(gdb_path="gdb", init_commands=["set pagination off"])
        )

        assert session_service.runtime.last_stop_event is None
        assert session_service.runtime.stop_history == []
        assert session_service.runtime.command_transcript == []

    def test_stop_history_is_bounded(self, session_service):
        """Stop-event history should retain only the newest configured entries."""

        session_service.runtime.stop_history_limit = 2

        session_service.runtime.record_stop_event(StopEvent(execution_state="paused", reason="a"))
        session_service.runtime.record_stop_event(StopEvent(execution_state="paused", reason="b"))
        session_service.runtime.record_stop_event(StopEvent(execution_state="exited", reason="c"))

        assert [event.reason for event in session_service.runtime.stop_history] == ["b", "c"]
        assert session_service.runtime.last_stop_event is not None
        assert session_service.runtime.last_stop_event.reason == "c"

    def test_command_transcript_is_bounded(self, session_service):
        """Command transcript history should retain only the newest configured entries."""

        session_service.runtime.command_transcript_limit = 2

        session_service.runtime.record_command_transcript(
            CommandTranscriptEntry(command="one", status="success")
        )
        session_service.runtime.record_command_transcript(
            CommandTranscriptEntry(command="two", status="error")
        )
        session_service.runtime.record_command_transcript(
            CommandTranscriptEntry(command="three", status="timeout")
        )

        assert [entry.command for entry in session_service.runtime.command_transcript] == [
            "two",
            "three",
        ]
