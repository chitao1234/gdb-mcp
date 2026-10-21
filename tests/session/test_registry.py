"""Unit tests for SessionRegistry lifecycle behavior."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from unittest.mock import MagicMock, Mock

import pytest

from gdb_mcp.domain import (
    InferiorStateRecord,
    OperationError,
    OperationSuccess,
    SessionListInfo,
    SessionMessage,
    SessionStartupInfo,
    SessionStatusSnapshot,
)
from gdb_mcp.session import registry as registry_module
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.runtime import SessionRuntime
from gdb_mcp.session.service import SessionService
from gdb_mcp.session.state import SessionState
from tests.support import failed, succeeded


def _session_double() -> Mock:
    """Create a registry session double with a usable workflow lock."""

    session = Mock(spec=SessionService)
    workflow_lock = MagicMock()
    workflow_lock.__enter__.return_value = None
    workflow_lock.__exit__.return_value = None
    session.runtime = Mock(
        spec=SessionRuntime,
        workflow_lock=workflow_lock,
        lifecycle_lock=MagicMock(),
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
    session.has_controller = False
    session.is_running = False
    session.state = SessionState.CREATED
    session.config = None
    session.get_status.return_value = OperationSuccess(
        SessionStatusSnapshot(
            debugger_alive=False, target_loaded=False, session_state=SessionState.CREATED
        )
    )
    return session


def _publish_session(manager: SessionRegistry, session: Mock, *, program: str = "/bin/ls") -> int:
    """Publish one session through the public start_session path."""

    session.start.return_value = OperationSuccess(SessionStartupInfo(message="started"))
    result = manager.start_session(program=program)
    assert isinstance(result, OperationSuccess)
    return result.value.session_id


def _session_factory_from(
    sessions: list[Mock], *, lock: threading.Lock | None = None
) -> Callable[[str], Mock]:
    """Return a factory that hands out prebuilt sessions in order.

    ``lock`` guards the shared cursor when the factory is called from racing
    threads; single-threaded callers omit it.
    """

    index = {"value": 0}

    def session_factory(_debugger: str = "gdb") -> Mock:
        with lock if lock is not None else contextlib.nullcontext():
            session = sessions[index["value"]]
            index["value"] += 1
            return session

    return session_factory


class TestSessionRegistry:
    """Test cases for the session registry."""

    def test_start_session_returns_sequential_ids(self) -> None:
        """Successful start_session calls should allocate sequential IDs starting at 1."""

        sessions = [_session_double(), _session_double(), _session_double()]
        for session in sessions:
            session.start.return_value = OperationSuccess(SessionStartupInfo(message="started"))

        manager = SessionRegistry(session_factory=_session_factory_from(sessions))

        result_1 = manager.start_session(program="/bin/ls")
        result_2 = manager.start_session(program="/bin/ls")
        result_3 = manager.start_session(program="/bin/ls")

        assert isinstance(result_1, OperationSuccess)
        assert isinstance(result_2, OperationSuccess)
        assert isinstance(result_3, OperationSuccess)
        assert [
            result_1.value.session_id,
            result_2.value.session_id,
            result_3.value.session_id,
        ] == [1, 2, 3]

    def test_get_session_returns_correct_session(self) -> None:
        """get_session should return the exact published session object."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)

        assert manager.get_session(session_id) is session

    def test_get_session_returns_none_for_invalid_id(self) -> None:
        """Test that get_session returns None for non-existent session IDs."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        assert manager.get_session(999) is None
        assert manager.get_session(0) is None
        assert manager.get_session(-1) is None

        session_id = _publish_session(manager, session)
        assert manager.get_session(session_id + 1) is None

    def test_close_session_stops_and_removes_active_session(self) -> None:
        """close_session should stop and remove active sessions atomically."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)
        session.has_controller = True
        session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))

        result = manager.close_session(session_id)

        assert isinstance(result, OperationSuccess)
        session.stop.assert_called_once()
        assert manager.get_session(session_id) is None

    def test_close_session_keeps_session_when_stop_fails(self) -> None:
        """Failed close operations should retain registry ownership for retry/inspection."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)
        session.has_controller = True
        session.stop.return_value = OperationError(message="stop failed")

        result = manager.close_session(session_id)

        assert isinstance(result, OperationError)
        assert result.message == "stop failed"
        assert manager.get_session(session_id) is session

    def test_close_session_rejects_inconsistent_running_session_without_controller(self) -> None:
        """close_session should not silently drop a logically running session."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)
        session.has_controller = False
        session.is_running = True

        result = manager.close_session(session_id)

        assert isinstance(result, OperationError)
        assert "inconsistent" in result.message.lower()
        assert manager.get_session(session_id) is session
        assert session_id not in manager._closing_sessions
        assert manager.resolve_session(session_id) is session

    def test_resolve_session_rejects_closing_sessions(self) -> None:
        """Closing sessions should reject new command resolution with a clear error."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)
        session_id = _publish_session(manager, session)

        manager._closing_sessions.add(session_id)
        result = manager.resolve_session(session_id)

        assert isinstance(result, OperationError)
        assert "closing" in result.message.lower()
        assert result.code == "session_closing"

    def test_resolve_session_reports_an_unknown_id_with_its_own_code(self) -> None:
        """An unknown id should be distinguishable from other session failures."""

        manager = SessionRegistry(session_factory=lambda _debugger: _session_double())

        result = manager.resolve_session(404)

        assert isinstance(result, OperationError)
        assert "Invalid session_id: 404" in result.message
        assert result.code == "invalid_session"

    def test_unknown_session_error_lists_the_live_sessions(self) -> None:
        """The error should name the sessions a caller can switch to."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)
        first = _publish_session(manager, session)
        second = _publish_session(manager, session)

        result = manager.resolve_session(404)

        assert isinstance(result, OperationError)
        assert f"Live sessions: {first}, {second}" in result.message
        assert result.details["live_session_ids"] == [first, second]

    def test_unknown_session_error_says_when_nothing_is_live(self) -> None:
        """An empty registry should report that no live sessions are available."""

        manager = SessionRegistry(session_factory=lambda _debugger: _session_double())

        result = manager.resolve_session(404)

        assert isinstance(result, OperationError)
        assert "Live sessions: none" in result.message
        assert result.details["live_session_ids"] == []

    def test_list_sessions_returns_structured_summaries(self) -> None:
        """Session inventory should expose metadata useful for multi-session clients."""

        session = _session_double()
        session.get_status.return_value = OperationSuccess(
            SessionStatusSnapshot(
                debugger_alive=True,
                target_loaded=True,
                session_state=SessionState.READY,
                execution_state="paused",
                stop_reason="breakpoint-hit",
                exit_code=None,
            )
        )
        session.state = SessionState.READY
        session.config = Mock(
            spec=SessionConfig, program="/tmp/a.out", core=None, working_dir="/tmp"
        )
        session.runtime = Mock(
            spec=SessionRuntime,
            workflow_lock=session.runtime.workflow_lock,
            lifecycle_lock=session.runtime.lifecycle_lock,
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
            InferiorStateRecord(
                inferior_id=1,
                is_current=True,
                execution_state="paused",
                stop_reason="breakpoint-hit",
            ),
            InferiorStateRecord(
                inferior_id=2,
                is_current=False,
                execution_state="running",
            ),
        ]
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)
        result = manager.list_sessions()

        assert isinstance(result, OperationSuccess)
        assert isinstance(result.value, SessionListInfo)
        assert len(result.value.sessions) == 1
        summary = result.value.sessions[0]
        assert summary.session_id == session_id
        assert summary.session_state == "ready"
        assert summary.execution_state == "paused"
        assert summary.program == "/tmp/a.out"
        assert summary.current_thread_id == 2
        assert summary.current_frame == 1
        assert summary.current_inferior_id == 1
        assert summary.inferior_count == 2
        assert summary.inferior_states is not None
        assert summary.inferior_states[0].inferior_id == 1
        assert summary.inferior_states[1].execution_state == "running"
        assert summary.follow_fork_mode == "child"
        assert summary.detach_on_fork is False

    def test_close_session_removes_failed_dead_session_without_stop(self) -> None:
        """Dead failed sessions should be removable without another stop attempt."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)
        session.has_controller = False
        session.is_running = False
        session.state = SessionState.FAILED

        result = manager.close_session(session_id)

        assert isinstance(result, OperationSuccess)
        assert result.value.message == "Session removed"
        session.stop.assert_not_called()
        assert manager.get_session(session_id) is None

    def test_concurrent_start_session_thread_safe(self) -> None:
        """Concurrent start_session calls should publish unique IDs."""

        sessions = [_session_double() for _ in range(10)]
        for session in sessions:
            session.start.return_value = OperationSuccess(SessionStartupInfo(message="started"))

        index_lock = threading.Lock()

        manager = SessionRegistry(session_factory=_session_factory_from(sessions, lock=index_lock))
        session_ids: list[int] = []
        session_ids_lock = threading.Lock()

        def start_sessions() -> None:
            result = manager.start_session(program="/bin/ls")
            assert isinstance(result, OperationSuccess)
            with session_ids_lock:
                session_ids.append(result.value.session_id)

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

    def test_start_session_only_publishes_successful_sessions(self) -> None:
        """Failed startup should not leave a reachable session in the registry."""

        session = _session_double()
        session.start.return_value = OperationError(message="boom")
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        result = manager.start_session(program="/bin/ls")

        assert isinstance(result, OperationError)
        assert result.message == "boom"
        assert manager.get_session(1) is None

    def test_start_session_stores_successful_session(self) -> None:
        """Atomic startup should publish the session only after success."""

        session = _session_double()
        session.start.return_value = OperationSuccess(SessionStartupInfo(message="started"))
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        result = manager.start_session(program="/bin/ls")

        assert isinstance(result, OperationSuccess)
        assert result.value.session_id == 1
        assert manager.get_session(result.value.session_id) is session

    def test_shutdown_all_stops_and_clears_sessions(self) -> None:
        """Shutdown should stop every registered session and clear the registry."""

        sessions = [_session_double(), _session_double()]
        for session in sessions:
            session.start.return_value = OperationSuccess(SessionStartupInfo(message="started"))
            session.has_controller = True
            session.stop.return_value = OperationSuccess(SessionMessage(message="stopped"))

        manager = SessionRegistry(session_factory=_session_factory_from(sessions))
        result_1 = manager.start_session(program="/bin/ls")
        result_2 = manager.start_session(program="/bin/ls")

        assert isinstance(result_1, OperationSuccess)
        assert isinstance(result_2, OperationSuccess)
        session_id_1 = result_1.value.session_id
        session_id_2 = result_2.value.session_id

        results = manager.shutdown_all()

        assert isinstance(results[session_id_1], OperationSuccess)
        assert isinstance(results[session_id_2], OperationSuccess)
        assert manager.get_session(session_id_1) is None
        assert manager.get_session(session_id_2) is None

    def test_shutdown_all_removes_failed_dead_sessions_without_stop(self) -> None:
        """Shutdown should not try to stop sessions whose transport has already died."""

        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)

        session_id = _publish_session(manager, session)
        session.has_controller = False
        session.is_running = False
        session.state = SessionState.FAILED

        results = manager.shutdown_all()

        assert isinstance(results[session_id], OperationSuccess)
        assert succeeded(results[session_id]).message == "Session removed"
        session.stop.assert_not_called()
        assert manager.get_session(session_id) is None

    def test_default_factory_returns_session_service(self) -> None:
        """The default registry factory should construct SessionService directly."""

        session = create_default_session_service()

        assert isinstance(session, SessionService)

    def test_shutdown_all_times_out_on_hung_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A session whose stop hangs must not block shutdown forever."""

        monkeypatch.setattr(registry_module, "_SHUTDOWN_TIMEOUT_SEC", 0.1)
        session = _session_double()
        manager = SessionRegistry(session_factory=lambda _debugger: session)
        session_id = _publish_session(manager, session)
        session.has_controller = True
        release = threading.Event()

        def hang() -> OperationSuccess[SessionMessage]:
            release.wait(timeout=5)
            return OperationSuccess(SessionMessage(message="stopped"))

        session.stop.side_effect = hang
        try:
            results = manager.shutdown_all()
        finally:
            release.set()

        assert isinstance(results[session_id], OperationError)
        assert "Timed out" in failed(results[session_id]).message
