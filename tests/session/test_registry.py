"""Unit tests for SessionRegistry lifecycle behavior."""

import threading
from unittest.mock import Mock

from gdb_mcp.domain import OperationError, OperationSuccess, SessionMessage, SessionStartInfo
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.service import SessionService


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

    def test_default_factory_returns_session_service(self):
        """The default registry factory should construct SessionService directly."""

        session = create_default_session_service()

        assert isinstance(session, SessionService)
