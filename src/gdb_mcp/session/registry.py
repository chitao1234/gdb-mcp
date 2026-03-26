"""Session registry and lifecycle coordination."""

from __future__ import annotations

import threading
from typing import Any, Callable

from ..domain import OperationError, OperationResult, OperationSuccess, SessionMessage
from .factory import create_default_session_service
from .service import SessionService


class SessionRegistry:
    """
    Thread-safe registry for debugger sessions.

    `start_session` publishes sessions atomically so failed startup never leaks
    a reachable half-initialized session.
    """

    def __init__(self, session_factory: Callable[[], SessionService] | None = None):
        if session_factory is None:
            session_factory = create_default_session_service
        self._session_factory = session_factory
        self._sessions: dict[int, SessionService] = {}
        self._next_session_id: int = 1
        self._lock = threading.Lock()

    def _allocate_session_id(self) -> int:
        """Allocate a new session ID without publishing a session yet."""

        with self._lock:
            session_id = self._next_session_id
            self._next_session_id += 1
            return session_id

    def create_session(self) -> int:
        """
        Create and store a new empty session.
        """

        session_id = self._allocate_session_id()
        session = self._session_factory()
        with self._lock:
            self._sessions[session_id] = session
        return session_id

    def start_session(self, **start_kwargs: Any) -> tuple[int | None, OperationResult[Any]]:
        """
        Start a session and only publish it if startup succeeds.

        Returns:
            A tuple of `(session_id, result)` where `session_id` is `None` on failure.
        """

        session_id = self._allocate_session_id()
        session = self._session_factory()
        result = session.start(**start_kwargs)

        if isinstance(result, OperationError):
            return None, result

        with self._lock:
            self._sessions[session_id] = session

        return session_id, result

    def get_session(self, session_id: int) -> SessionService | None:
        """Retrieve a session by ID."""

        with self._lock:
            return self._sessions.get(session_id)

    def discard_session(self, session_id: int) -> bool:
        """Discard an already-inactive session without attempting shutdown."""

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if session.controller is not None or session.is_running:
                return False
            del self._sessions[session_id]
            return True

    def close_session(self, session_id: int) -> OperationResult[Any]:
        """Stop and remove a session in one explicit lifecycle operation."""

        with self._lock:
            session = self._sessions.pop(session_id, None)

        if session is None:
            return OperationError(
                message=f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session."
            )

        if session.controller is None:
            return OperationSuccess(SessionMessage(message="Session removed"))

        try:
            return session.stop()
        except Exception as exc:
            return OperationError(message=str(exc))

    def shutdown_all(self) -> dict[int, OperationResult[Any]]:
        """Stop and remove all registered sessions."""

        with self._lock:
            sessions = self._sessions
            self._sessions = {}

        results: dict[int, OperationResult[Any]] = {}
        for session_id, session in sessions.items():
            if session.controller is None:
                results[session_id] = OperationSuccess(SessionMessage(message="Session removed"))
                continue
            try:
                results[session_id] = session.stop()
            except Exception as exc:
                results[session_id] = OperationError(message=str(exc))

        return results
