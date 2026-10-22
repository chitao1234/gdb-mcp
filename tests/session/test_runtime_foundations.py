"""Tests for session runtime workflow foundations."""

from __future__ import annotations

import threading

from gdb_mcp.domain import CommandTranscriptEntry, StopEvent
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.service import SessionService


def _stop_kind(session: SessionService) -> str:
    """Return the runtime's current canonical stop kind."""

    return session.runtime.stop_kind


def _execution_state(session: SessionService) -> str:
    return session.runtime.execution_state


class TestRuntimeFoundations:
    """Test bounded runtime state used by higher-level workflow tools."""

    def test_begin_startup_clears_stop_history_and_transcript(
        self, session_service: SessionService
    ) -> None:
        """New startup attempts should clear prior execution observations."""

        session_service.runtime.record_stop_event(
            StopEvent(execution_state="paused", reason="breakpoint-hit")
        )
        session_service.runtime.record_command_transcript(
            CommandTranscriptEntry(command="-exec-run")
        )

        session_service.runtime.begin_startup(
            SessionConfig.from_inputs(debugger_path="gdb", init_commands=["set pagination off"])
        )

        assert session_service.runtime.last_stop_event is None
        assert session_service.runtime.stop_history == []
        assert session_service.runtime.command_transcript == []

    def test_stop_history_is_bounded(self, session_service: SessionService) -> None:
        """Stop-event history should retain only the newest configured entries."""

        session_service.runtime.stop_history_limit = 2

        session_service.runtime.record_stop_event(StopEvent(execution_state="paused", reason="a"))
        session_service.runtime.record_stop_event(StopEvent(execution_state="paused", reason="b"))
        session_service.runtime.record_stop_event(StopEvent(execution_state="exited", reason="c"))

        assert [event.reason for event in session_service.runtime.stop_history] == ["b", "c"]
        assert session_service.runtime.last_stop_event is not None
        assert session_service.runtime.last_stop_event.reason == "c"

    def test_command_transcript_is_bounded(self, session_service: SessionService) -> None:
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

    def test_inferior_state_tracking_follows_selected_inferior(
        self, session_service: SessionService
    ) -> None:
        """Global execution fields should mirror the currently selected inferior state."""

        runtime = session_service.runtime
        runtime.mark_inferior_not_started()
        runtime.mark_inferior_running(inferior_id=1)
        runtime.mark_inferior_paused("breakpoint-hit", inferior_id=1)
        runtime.mark_inferior_exited("exited-normally", 0, inferior_id=2)

        runtime.mark_inferior_selected(2)
        assert runtime.execution_state == "exited"
        assert runtime.stop_reason == "exited-normally"
        assert runtime.exit_code == 0

        summaries = runtime.inferiors_state_summary()
        assert len(summaries) == 2
        assert summaries[0].inferior_id == 1
        assert summaries[0].execution_state == "paused"
        assert summaries[1].inferior_id == 2
        assert summaries[1].execution_state == "exited"
        assert "is_current" not in summaries[1].to_payload()
        assert runtime.current_inferior_id == 2

    def test_updates_to_an_unselected_inferior_do_not_change_selected_status(
        self, session_service: SessionService
    ) -> None:
        runtime = session_service.runtime
        runtime.mark_inferior_paused("breakpoint-hit", inferior_id=1)
        runtime.mark_inferior_exited("exited-normally", 7, inferior_id=2)

        assert _execution_state(session_service) == "paused"
        assert runtime.stop_reason == "breakpoint-hit"
        assert runtime.exit_code is None

        runtime.mark_inferior_selected(2)
        runtime.mark_inferior_running(inferior_id=1)

        assert _execution_state(session_service) == "exited"
        assert runtime.stop_reason == "exited-normally"
        assert runtime.exit_code == 7

    def test_selected_state_survives_inventory_refresh_and_clears_with_last_inferior(
        self, session_service: SessionService
    ) -> None:
        runtime = session_service.runtime
        runtime.mark_inferior_exited("exited-normally", 0, inferior_id=2)
        runtime.update_inferior_inventory(current_inferior_id=2, count=1, inferior_ids=(2,))

        assert _execution_state(session_service) == "exited"
        assert runtime.exit_code == 0

        runtime.remove_inferior(2)

        assert runtime.current_inferior_id is None
        assert _execution_state(session_service) == "unknown"
        assert runtime.stop_reason is None
        assert runtime.exit_code is None

    def test_startup_discards_previous_inferior_execution_state(
        self, session_service: SessionService
    ) -> None:
        runtime = session_service.runtime
        runtime.mark_inferior_exited("exited-normally", 7, inferior_id=2)

        runtime.begin_startup(SessionConfig())
        runtime.mark_ready()

        assert runtime.current_inferior_id == 1
        assert runtime.execution_state == "unknown"
        assert runtime.stop_reason is None
        assert runtime.exit_code is None
        assert [state.inferior_id for state in runtime.inferiors_state_summary()] == [1]

    def test_a_refined_stop_kind_belongs_to_its_reason(
        self, session_service: SessionService
    ) -> None:
        """A backend refinement applies only to the stop reason it was recorded with."""

        runtime = session_service.runtime

        runtime.mark_inferior_paused("breakpoint", stop_kind="watchpoint")
        assert _stop_kind(session_service) == "watchpoint"

        runtime.mark_inferior_paused("breakpoint-hit")
        assert _stop_kind(session_service) == "breakpoint"

    def test_update_inferior_inventory_reconciles_state_map(
        self, session_service: SessionService
    ) -> None:
        """Inventory refresh should drop stale inferiors and keep active ones."""

        runtime = session_service.runtime
        runtime.mark_inferior_paused("breakpoint-hit", inferior_id=1)
        runtime.mark_inferior_exited("exited-normally", 0, inferior_id=2)
        runtime.update_inferior_inventory(
            current_inferior_id=1,
            count=1,
            inferior_ids=(1,),
        )

        summaries = runtime.inferiors_state_summary()
        assert len(summaries) == 1
        assert summaries[0].inferior_id == 1
        assert summaries[0].execution_state == "paused"

    def test_remove_inferior_reselects_remaining_state(
        self, session_service: SessionService
    ) -> None:
        """Removing the selected inferior should synchronize status to a remaining inferior."""

        runtime = session_service.runtime
        runtime.mark_inferior_running(inferior_id=1)
        runtime.mark_inferior_exited("thread-group-exited", 7, inferior_id=2)
        runtime.mark_inferior_selected(2)

        runtime.remove_inferior(2)

        assert runtime.current_inferior_id == 1
        assert runtime.execution_state == "running"
        summaries = runtime.inferiors_state_summary()
        assert [record.inferior_id for record in summaries] == [1]

    def test_ensure_inferior_allocates_state_and_count(
        self, session_service: SessionService
    ) -> None:
        """ensure_inferior should allocate state records without mutating selected status."""

        runtime = session_service.runtime
        runtime.mark_inferior_selected(1)

        runtime.ensure_inferior(3)

        summaries = runtime.inferiors_state_summary()
        assert [record.inferior_id for record in summaries] == [1, 3]
        assert runtime.inferior_count == 2
        assert runtime.current_inferior_id == 1

    def test_inferior_summary_reads_are_serialized_by_the_state_lock(
        self, session_service: SessionService
    ) -> None:
        """Snapshot readers must take the state lock instead of racing writers."""

        runtime = session_service.runtime
        runtime.mark_inferior_running(inferior_id=1)
        finished = threading.Event()

        def read_summary() -> None:
            runtime.inferiors_state_summary()
            finished.set()

        with runtime._state_lock:
            worker = threading.Thread(target=read_summary)
            worker.start()
            # The reader cannot complete while the lock is held by the writer.
            assert not finished.wait(timeout=0.2)

        worker.join(timeout=1.0)
        assert finished.is_set()
