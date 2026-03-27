"""Session registry and lifecycle coordination."""

from __future__ import annotations

from contextlib import nullcontext
import threading
from collections.abc import Callable
from typing import ContextManager, cast

from ..domain import (
    OperationError,
    OperationResult,
    OperationSuccess,
    SessionListInfo,
    SessionMessage,
    SessionStartInfo,
    SessionSummary,
)
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
        self._closing_sessions: set[int] = set()
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

    def create_untracked_session(self) -> SessionService:
        """Create a fresh session without publishing it in the registry."""

        return self._session_factory()

    def start_session(
        self,
        *,
        program: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        gdb_path: str | None = None,
        working_dir: str | None = None,
        core: str | None = None,
    ) -> tuple[int | None, OperationSuccess[SessionStartInfo] | OperationError]:
        """
        Start a session and only publish it if startup succeeds.

        Returns:
            A tuple of `(session_id, result)` where `session_id` is `None` on failure.
        """

        session_id = self._allocate_session_id()
        session = self._session_factory()
        result = session.start(
            program=program,
            args=args,
            init_commands=init_commands,
            env=env,
            gdb_path=gdb_path,
            working_dir=working_dir,
            core=core,
        )

        if isinstance(result, OperationError):
            return None, result

        with self._lock:
            self._sessions[session_id] = session

        return session_id, result

    def get_session(self, session_id: int) -> SessionService | None:
        """Retrieve a session by ID."""

        with self._lock:
            return self._sessions.get(session_id)

    def resolve_session(self, session_id: int) -> SessionService | OperationError:
        """Resolve a session for one MCP call, rejecting sessions that are closing."""

        with self._lock:
            if session_id in self._closing_sessions:
                return OperationError(
                    message=(
                        f"Session {session_id} is closing and cannot accept new commands. "
                        "Wait for gdb_stop_session to finish or start a new session."
                    )
                )

            session = self._sessions.get(session_id)

        if session is None:
            return OperationError(
                message=f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session."
            )

        return session

    def list_sessions(self) -> OperationSuccess[SessionListInfo]:
        """Return structured summaries for all currently registered sessions."""

        with self._lock:
            sessions = list(self._sessions.items())
            closing_sessions = set(self._closing_sessions)

        summaries: list[SessionSummary] = []
        for session_id, session in sorted(sessions, key=lambda item: item[0]):
            status_result = session.get_status()
            status = status_result.value
            config = session.config
            lifecycle_state = session.state.value
            if session_id in closing_sessions:
                lifecycle_state = "closing"

            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    lifecycle_state=lifecycle_state,
                    execution_state=status.execution_state,
                    target_loaded=status.target_loaded,
                    has_controller=status.has_controller,
                    program=config.program if config is not None else None,
                    core=config.core if config is not None else None,
                    working_dir=config.working_dir if config is not None else None,
                    attached_pid=session.runtime.attached_pid,
                    current_thread_id=session.runtime.current_thread_id,
                    current_frame=session.runtime.current_frame,
                    stop_reason=status.stop_reason,
                    exit_code=status.exit_code,
                    current_inferior_id=session.runtime.current_inferior_id,
                    inferior_count=session.runtime.inferior_count,
                    follow_fork_mode=session.runtime.follow_fork_mode,
                    detach_on_fork=session.runtime.detach_on_fork,
                    last_failure_message=session.runtime.last_failure_message,
                )
            )

        return OperationSuccess(SessionListInfo(sessions=summaries, count=len(summaries)))

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

    def close_session(self, session_id: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Stop and remove a session in one explicit lifecycle operation."""

        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                self._closing_sessions.add(session_id)

        if session is None:
            return OperationError(
                message=f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session."
            )

        if session.controller is None:
            if session.is_running:
                return OperationError(
                    message=(
                        "Session state is inconsistent: session reports running "
                        "without an active controller."
                    )
                )
            with self._lock:
                current = self._sessions.get(session_id)
                if current is session:
                    del self._sessions[session_id]
                self._closing_sessions.discard(session_id)
            return OperationSuccess(SessionMessage(message="Session removed"))

        try:
            with self._session_workflow_context(session):
                result = session.stop()
        except Exception as exc:
            with self._lock:
                self._closing_sessions.discard(session_id)
            return OperationError(message=str(exc))

        if isinstance(result, OperationError):
            with self._lock:
                self._closing_sessions.discard(session_id)
            return result

        with self._lock:
            current = self._sessions.get(session_id)
            if current is session:
                del self._sessions[session_id]
            self._closing_sessions.discard(session_id)

        return result

    def shutdown_all(self) -> dict[int, OperationSuccess[SessionMessage] | OperationError]:
        """Stop and remove all registered sessions."""

        with self._lock:
            sessions = self._sessions
            self._sessions = {}
            self._closing_sessions = set(sessions)

        results: dict[int, OperationSuccess[SessionMessage] | OperationError] = {}
        for session_id, session in sessions.items():
            if session.controller is None:
                results[session_id] = OperationSuccess(SessionMessage(message="Session removed"))
                continue
            try:
                with self._session_workflow_context(session):
                    results[session_id] = session.stop()
            except Exception as exc:
                results[session_id] = OperationError(message=str(exc))

        with self._lock:
            self._closing_sessions.clear()

        return results

    @staticmethod
    def _session_workflow_context(session: SessionService) -> ContextManager[object]:
        """Return the session workflow lock when it is available."""

        runtime = getattr(session, "runtime", None)
        workflow_lock = getattr(runtime, "workflow_lock", None)
        if (
            workflow_lock is None
            or not hasattr(workflow_lock, "__enter__")
            or not hasattr(workflow_lock, "__exit__")
        ):
            return nullcontext()
        return cast(ContextManager[object], workflow_lock)
