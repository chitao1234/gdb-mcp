"""Unit tests for SessionRegistry lifecycle behavior."""

import threading
from unittest.mock import Mock

from gdb_mcp.domain import (
    OperationError,
    OperationSuccess,
    SessionListInfo,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
)
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.service import SessionService
from gdb_mcp.session.state import SessionState


class TestSessionRegistry:
    """Test cases for the session registry."""

    def test_create_session_returns_sequential_ids(self):
        """Test that create_session returns sequential integer IDs starting at 1."""
        manager = SessionRegistry()

        session_id_1 = manager.create_session()
        session_id_2 = manager.create_session()
        session_id_3 = manager.create_session()

        assert session_id_1 == 1
        assert session_id_2 == 2
        assert session_id_3 == 3

    def test_get_session_returns_correct_session(self):
        """Test that get_session returns the default SessionService implementation."""
        manager = SessionRegistry()

        session_id = manager.create_session()
        session = manager.get_session(session_id)

        assert session is not None
        assert isinstance(session, SessionService)

        same_session = manager.get_session(session_id)
        assert same_session is session

    def test_get_session_returns_none_for_invalid_id(self):
        """Test that get_session returns None for non-existent session IDs."""
        manager = SessionRegistry()

        assert manager.get_session(999) is None
        assert manager.get_session(0) is None
        assert manager.get_session(-1) is None

        session_id = manager.create_session()
        assert manager.get_session(session_id + 1) is None

    def test_discard_session_deletes_inactive_session(self):
        """Inactive sessions should be discardable without shutdown."""
        manager = SessionRegistry()

        session_id = manager.create_session()
        session = manager.get_session(session_id)
        assert session is not None

        result = manager.discard_session(session_id)
        assert result is True
        assert manager.get_session(session_id) is None

    def test_discard_session_returns_false_for_nonexistent(self):
        """discard_session should return False for already-removed or unknown IDs."""
        manager = SessionRegistry()

        assert manager.discard_session(999) is False

        session_id = manager.create_session()
        assert manager.discard_session(session_id) is True
        assert manager.discard_session(session_id) is False

    def test_discard_session_rejects_active_sessions(self):
        """Active sessions must be closed explicitly instead of discarded."""

        session = Mock(spec=SessionService)
        session.controller = object()
        session.is_running = True
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()

        assert manager.discard_session(session_id) is False
        assert manager.get_session(session_id) is session

    def test_close_session_stops_and_removes_active_session(self):
        """close_session should stop and remove active sessions atomically."""

        session = Mock(spec=SessionService)
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()
        result = manager.close_session(session_id)

        assert isinstance(result, OperationSuccess)
        session.stop.assert_called_once()
        assert manager.get_session(session_id) is None

    def test_close_session_keeps_session_when_stop_fails(self):
        """Failed close operations should retain registry ownership for retry/inspection."""

        session = Mock(spec=SessionService)
        session.controller = object()
        session.stop.return_value = OperationError(message="stop failed")
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()
        result = manager.close_session(session_id)

        assert isinstance(result, OperationError)
        assert result.message == "stop failed"
        assert manager.get_session(session_id) is session

    def test_close_session_rejects_inconsistent_running_session_without_controller(self):
        """close_session should not silently drop a logically running session."""

        session = Mock(spec=SessionService)
        session.controller = None
        session.is_running = True
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()
        result = manager.close_session(session_id)

        assert isinstance(result, OperationError)
        assert "inconsistent" in result.message.lower()
        assert manager.get_session(session_id) is session

    def test_resolve_session_rejects_closing_sessions(self):
        """Closing sessions should reject new command resolution with a clear error."""

        session = Mock(spec=SessionService)
        manager = SessionRegistry(session_factory=lambda: session)
        session_id = manager.create_session()

        manager._closing_sessions.add(session_id)
        result = manager.resolve_session(session_id)

        assert isinstance(result, OperationError)
        assert "closing" in result.message.lower()

    def test_list_sessions_returns_structured_summaries(self):
        """Session inventory should expose metadata useful for multi-session clients."""

        session = Mock(spec=SessionService)
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                is_running=True,
                target_loaded=True,
                has_controller=True,
                execution_state="paused",
                stop_reason="breakpoint-hit",
                exit_code=None,
            )
        )
        session.state = SessionState.READY
        session.config = Mock(program="/tmp/a.out", core=None, working_dir="/tmp")
        session.runtime = Mock(
            attached_pid=None,
            current_thread_id=2,
            current_frame=1,
            current_inferior_id=1,
            inferior_count=2,
            follow_fork_mode="child",
            detach_on_fork=False,
            last_failure_message=None,
        )
        session.runtime.inferiors_state_summary.return_value = [
            {
                "inferior_id": 1,
                "is_current": True,
                "execution_state": "paused",
                "stop_reason": "breakpoint-hit",
                "exit_code": None,
            },
            {
                "inferior_id": 2,
                "is_current": False,
                "execution_state": "running",
                "stop_reason": None,
                "exit_code": None,
            },
        ]
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()
        result = manager.list_sessions()

        assert isinstance(result, OperationSuccess)
        assert isinstance(result.value, SessionListInfo)
        assert result.value.count == 1
        summary = result.value.sessions[0]
        assert summary.session_id == session_id
        assert summary.lifecycle_state == "ready"
        assert summary.execution_state == "paused"
        assert summary.program == "/tmp/a.out"
        assert summary.current_thread_id == 2
        assert summary.current_frame == 1
        assert summary.current_inferior_id == 1
        assert summary.inferior_count == 2
        assert summary.inferior_states is not None
        assert summary.inferior_states[0]["inferior_id"] == 1
        assert summary.inferior_states[1]["execution_state"] == "running"
        assert summary.follow_fork_mode == "child"
        assert summary.detach_on_fork is False

    def test_close_session_removes_failed_dead_session_without_stop(self):
        """Dead failed sessions should be removable without another stop attempt."""

        session = Mock(spec=SessionService)
        session.controller = None
        session.is_running = False
        session.state = SessionState.FAILED
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()
        result = manager.close_session(session_id)

        assert isinstance(result, OperationSuccess)
        assert result.value.message == "Session removed"
        session.stop.assert_not_called()
        assert manager.get_session(session_id) is None

    def test_concurrent_create_session_thread_safe(self):
        """Test that concurrent create_session calls produce unique IDs."""
        manager = SessionRegistry()
        session_ids = []
        session_ids_lock = threading.Lock()

        def create_sessions():
            session_id = manager.create_session()
            with session_ids_lock:
                session_ids.append(session_id)

        threads = []
        for _ in range(10):
            thread = threading.Thread(target=create_sessions)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(session_ids) == 10
        assert len(set(session_ids)) == 10
        assert sorted(session_ids) == list(range(1, 11))

    def test_start_session_only_publishes_successful_sessions(self):
        """Failed startup should not leave a reachable session in the registry."""

        session = Mock()
        session.start.return_value = OperationError(message="boom")
        manager = SessionRegistry(session_factory=lambda: session)

        session_id, result = manager.start_session(program="/bin/ls")

        assert session_id is None
        assert isinstance(result, OperationError)
        assert result.message == "boom"
        assert manager.get_session(1) is None

    def test_start_session_stores_successful_session(self):
        """Atomic startup should publish the session only after success."""

        session = Mock(spec=SessionService)
        session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
        manager = SessionRegistry(session_factory=lambda: session)

        session_id, result = manager.start_session(program="/bin/ls")

        assert session_id == 1
        assert isinstance(result, OperationSuccess)
        assert manager.get_session(session_id) is session

    def test_shutdown_all_stops_and_clears_sessions(self):
        """Shutdown should stop every registered session and clear the registry."""

        sessions = [Mock(spec=SessionService), Mock(spec=SessionService)]
        for session in sessions:
            session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))

        index = {"value": 0}

        def session_factory():
            session = sessions[index["value"]]
            index["value"] += 1
            return session

        manager = SessionRegistry(session_factory=session_factory)
        session_id_1 = manager.create_session()
        session_id_2 = manager.create_session()

        results = manager.shutdown_all()

        assert isinstance(results[session_id_1], OperationSuccess)
        assert isinstance(results[session_id_2], OperationSuccess)
        assert manager.get_session(session_id_1) is None
        assert manager.get_session(session_id_2) is None

    def test_shutdown_all_removes_failed_dead_sessions_without_stop(self):
        """Shutdown should not try to stop sessions whose transport has already died."""

        session = Mock(spec=SessionService)
        session.controller = None
        session.is_running = False
        session.state = SessionState.FAILED
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = manager.create_session()
        results = manager.shutdown_all()

        assert isinstance(results[session_id], OperationSuccess)
        assert results[session_id].value.message == "Session removed"
        session.stop.assert_not_called()
        assert manager.get_session(session_id) is None

    def test_default_factory_returns_session_service(self):
        """The default registry factory should construct SessionService directly."""

        session = create_default_session_service()

        assert isinstance(session, SessionService)
