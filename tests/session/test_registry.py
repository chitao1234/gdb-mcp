"""Unit tests for SessionRegistry lifecycle behavior."""

import threading
from unittest.mock import MagicMock, Mock

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


def _session_double() -> Mock:
    """Create a registry session double with a usable workflow lock."""

    session = Mock(spec=SessionService)
    workflow_lock = MagicMock()
    workflow_lock.__enter__.return_value = None
    workflow_lock.__exit__.return_value = None
    session.runtime = Mock(
        workflow_lock=workflow_lock,
        attached_pid=None,
        current_thread_id=None,
        current_frame=None,
        current_inferior_id=None,
        inferior_count=None,
        follow_fork_mode=None,
        detach_on_fork=None,
        last_failure_message=None,
    )
    session.runtime.inferiors_state_summary.return_value = []
    session.controller = None
    session.is_running = False
    session.state = SessionState.CREATED
    session.config = None
    session.get_status.return_value = OperationSuccess(
        SessionStatusSnapshot(is_running=False, target_loaded=False, has_controller=False)
    )
    return session


def _publish_session(manager: SessionRegistry, session: Mock, *, program: str = "/bin/ls") -> int:
    """Publish one session through the public start_session path."""

    session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
    session_id, result = manager.start_session(program=program)
    assert session_id is not None
    assert isinstance(result, OperationSuccess)
    return session_id


class TestSessionRegistry:
    """Test cases for the session registry."""

    def test_start_session_returns_sequential_ids(self):
        """Successful start_session calls should allocate sequential IDs starting at 1."""

        sessions = [_session_double(), _session_double(), _session_double()]
        for session in sessions:
            session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))

        index = {"value": 0}

        def session_factory():
            session = sessions[index["value"]]
            index["value"] += 1
            return session

        manager = SessionRegistry(session_factory=session_factory)

        session_id_1, result_1 = manager.start_session(program="/bin/ls")
        session_id_2, result_2 = manager.start_session(program="/bin/ls")
        session_id_3, result_3 = manager.start_session(program="/bin/ls")

        assert [session_id_1, session_id_2, session_id_3] == [1, 2, 3]
        assert isinstance(result_1, OperationSuccess)
        assert isinstance(result_2, OperationSuccess)
        assert isinstance(result_3, OperationSuccess)

    def test_get_session_returns_correct_session(self):
        """get_session should return the exact published session object."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = _publish_session(manager, session)

        assert manager.get_session(session_id) is session
        assert manager.get_session(session_id) is session

    def test_get_session_returns_none_for_invalid_id(self):
        """Test that get_session returns None for non-existent session IDs."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        assert manager.get_session(999) is None
        assert manager.get_session(0) is None
        assert manager.get_session(-1) is None

        session_id = _publish_session(manager, session)
        assert manager.get_session(session_id + 1) is None

    def test_close_session_stops_and_removes_active_session(self):
        """close_session should stop and remove active sessions atomically."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = _publish_session(manager, session)
        session.controller = object()
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))

        result = manager.close_session(session_id)

        assert isinstance(result, OperationSuccess)
        session.stop.assert_called_once()
        assert manager.get_session(session_id) is None

    def test_close_session_keeps_session_when_stop_fails(self):
        """Failed close operations should retain registry ownership for retry/inspection."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = _publish_session(manager, session)
        session.controller = object()
        session.stop.return_value = OperationError(message="stop failed")

        result = manager.close_session(session_id)

        assert isinstance(result, OperationError)
        assert result.message == "stop failed"
        assert manager.get_session(session_id) is session

    def test_close_session_rejects_inconsistent_running_session_without_controller(self):
        """close_session should not silently drop a logically running session."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = _publish_session(manager, session)
        session.controller = None
        session.is_running = True

        result = manager.close_session(session_id)

        assert isinstance(result, OperationError)
        assert "inconsistent" in result.message.lower()
        assert manager.get_session(session_id) is session
        assert session_id not in manager._closing_sessions
        assert manager.resolve_session(session_id) is session

    def test_resolve_session_rejects_closing_sessions(self):
        """Closing sessions should reject new command resolution with a clear error."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)
        session_id = _publish_session(manager, session)

        manager._closing_sessions.add(session_id)
        result = manager.resolve_session(session_id)

        assert isinstance(result, OperationError)
        assert "closing" in result.message.lower()

    def test_list_sessions_returns_structured_summaries(self):
        """Session inventory should expose metadata useful for multi-session clients."""

        session = _session_double()
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
            workflow_lock=session.runtime.workflow_lock,
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

        session_id = _publish_session(manager, session)
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

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = _publish_session(manager, session)
        session.controller = None
        session.is_running = False
        session.state = SessionState.FAILED

        result = manager.close_session(session_id)

        assert isinstance(result, OperationSuccess)
        assert result.value.message == "Session removed"
        session.stop.assert_not_called()
        assert manager.get_session(session_id) is None

    def test_concurrent_start_session_thread_safe(self):
        """Concurrent start_session calls should publish unique IDs."""

        sessions = [_session_double() for _ in range(10)]
        for session in sessions:
            session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))

        index = {"value": 0}
        index_lock = threading.Lock()

        def session_factory():
            with index_lock:
                session = sessions[index["value"]]
                index["value"] += 1
                return session

        manager = SessionRegistry(session_factory=session_factory)
        session_ids: list[int] = []
        session_ids_lock = threading.Lock()

        def start_sessions():
            session_id, result = manager.start_session(program="/bin/ls")
            assert isinstance(result, OperationSuccess)
            assert session_id is not None
            with session_ids_lock:
                session_ids.append(session_id)

        threads = []
        for _ in range(10):
            thread = threading.Thread(target=start_sessions)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(session_ids) == 10
        assert len(set(session_ids)) == 10
        assert sorted(session_ids) == list(range(1, 11))

    def test_start_session_only_publishes_successful_sessions(self):
        """Failed startup should not leave a reachable session in the registry."""

        session = _session_double()
        session.start.return_value = OperationError(message="boom")
        manager = SessionRegistry(session_factory=lambda: session)

        session_id, result = manager.start_session(program="/bin/ls")

        assert session_id is None
        assert isinstance(result, OperationError)
        assert result.message == "boom"
        assert manager.get_session(1) is None

    def test_start_session_stores_successful_session(self):
        """Atomic startup should publish the session only after success."""

        session = _session_double()
        session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
        manager = SessionRegistry(session_factory=lambda: session)

        session_id, result = manager.start_session(program="/bin/ls")

        assert session_id == 1
        assert isinstance(result, OperationSuccess)
        assert manager.get_session(session_id) is session

    def test_shutdown_all_stops_and_clears_sessions(self):
        """Shutdown should stop every registered session and clear the registry."""

        sessions = [_session_double(), _session_double()]
        for session in sessions:
            session.start.return_value = OperationSuccess(SessionStartInfo(message="started"))
            session.controller = object()
            session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))

        index = {"value": 0}

        def session_factory():
            session = sessions[index["value"]]
            index["value"] += 1
            return session

        manager = SessionRegistry(session_factory=session_factory)
        session_id_1, result_1 = manager.start_session(program="/bin/ls")
        session_id_2, result_2 = manager.start_session(program="/bin/ls")

        assert isinstance(result_1, OperationSuccess)
        assert isinstance(result_2, OperationSuccess)
        assert session_id_1 is not None
        assert session_id_2 is not None

        results = manager.shutdown_all()

        assert isinstance(results[session_id_1], OperationSuccess)
        assert isinstance(results[session_id_2], OperationSuccess)
        assert manager.get_session(session_id_1) is None
        assert manager.get_session(session_id_2) is None

    def test_shutdown_all_removes_failed_dead_sessions_without_stop(self):
        """Shutdown should not try to stop sessions whose transport has already died."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda: session)

        session_id = _publish_session(manager, session)
        session.controller = None
        session.is_running = False
        session.state = SessionState.FAILED

        results = manager.shutdown_all()

        assert isinstance(results[session_id], OperationSuccess)
        assert results[session_id].value.message == "Session removed"
        session.stop.assert_not_called()
        assert manager.get_session(session_id) is None

    def test_default_factory_returns_session_service(self):
        """The default registry factory should construct SessionService directly."""

        session = create_default_session_service()

        assert isinstance(session, SessionService)
