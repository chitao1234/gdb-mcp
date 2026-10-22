"""Session registry and lifecycle coordination."""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..contracts import DEBUGGER_GDB, SessionState
from ..domain import (
    OperationError,
    OperationSuccess,
    SessionListInfo,
    SessionMessage,
    SessionStartInfo,
    SessionSummary,
    payload_to_mapping,
)
from .factory import create_default_session_service
from .locking import session_workflow_context
from .service import SessionService

_SHUTDOWN_TIMEOUT_SEC = 10.0


class SessionRegistry:
    """
    Thread-safe registry for debugger sessions.

    `start_session` publishes sessions atomically so failed startup never leaks
    a reachable half-initialized session. `create_untracked_session` remains
    available for ephemeral flows that should never publish a tracked session.
    """

    def __init__(self, session_factory: Callable[[str], SessionService] | None = None):
        if session_factory is None:
            session_factory = create_default_session_service
        self._session_factory = session_factory
        self._sessions: dict[int, SessionService] = {}
        self._closing_sessions: set[int] = set()
        self._next_session_id: int = 1
        self._lock = threading.Lock()

    def active_session_count(self) -> int:
        """Return the number of tracked (including closing) sessions."""

        with self._lock:
            return len(self._sessions) + len(self._closing_sessions)

    def _allocate_session_id(self) -> int:
        """Allocate a new session ID without publishing a session yet."""

        with self._lock:
            session_id = self._next_session_id
            self._next_session_id += 1
            return session_id

    def create_untracked_session(self, debugger: str = DEBUGGER_GDB) -> SessionService:
        """Create a fresh session without publishing it in the registry."""

        return self._session_factory(debugger)

    def start_session(
        self,
        *,
        program: str | None = None,
        debugger: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        debugger_path: str | None = None,
        debugger_args: list[str] | None = None,
        working_dir: str | None = None,
        core: str | None = None,
        endpoint: str | None = None,
        rr_replay: str | None = None,
    ) -> OperationSuccess[SessionStartInfo] | OperationError:
        """
        Start a session and only publish it if startup succeeds.

        Returns:
            A startup result carrying the registered session id, or the startup error.
        """

        session_id = self._allocate_session_id()
        session = self._session_factory(debugger or DEBUGGER_GDB)
        result = session.start(
            program=program,
            debugger=debugger,
            args=args,
            init_commands=init_commands,
            env=env,
            debugger_path=debugger_path,
            debugger_args=debugger_args,
            working_dir=working_dir,
            core=core,
            endpoint=endpoint,
            rr_replay=rr_replay,
        )

        if isinstance(result, OperationError):
            return result

        with self._lock:
            self._sessions[session_id] = session

        startup = result.value
        return OperationSuccess(
            SessionStartInfo(
                session_id=session_id,
                message=startup.message,
                program=startup.program,
                core=startup.core,
                target_loaded=startup.target_loaded,
                execution_state=startup.execution_state,
                stop_reason=startup.stop_reason,
                stop_kind=startup.stop_kind,
                exit_code=startup.exit_code,
                diagnostics=startup.diagnostics,
                endpoint=startup.endpoint,
                rr_replay=startup.rr_replay,
            ),
            warnings=result.warnings,
            action=result.action,
        )

    def get_session(self, session_id: int) -> SessionService | None:
        """Retrieve a session by ID."""

        with self._lock:
            return self._sessions.get(session_id)

    def _unknown_session_error(self, session_id: int) -> OperationError:
        """Report an unknown ID together with the sessions that do exist.

        Session IDs restart when the server process does, so a caller can hold an
        ID from an earlier instance. Name the live sessions so it recovers in one
        round trip instead of retrying blind.
        """

        with self._lock:
            live = sorted(self._sessions)

        listed = ", ".join(str(value) for value in live) if live else "none"
        return OperationError(
            message=(
                f"Invalid session_id: {session_id}. Live sessions: {listed}. "
                "Use gdb_session_start to create a new session."
            ),
            code="invalid_session",
            details={"live_session_ids": payload_to_mapping(live)},
        )

    def resolve_session(self, session_id: int) -> SessionService | OperationError:
        """Resolve a session for one MCP call, rejecting sessions that are closing."""

        with self._lock:
            if session_id in self._closing_sessions:
                return OperationError(
                    message=(
                        f"Session {session_id} is closing and cannot accept new commands. "
                        "Wait for gdb_session_manage(action=stop) to finish or start a new session."
                    ),
                    code="session_closing",
                )

            session = self._sessions.get(session_id)

        if session is None:
            return self._unknown_session_error(session_id)

        return session

    def list_sessions(self) -> OperationSuccess[SessionListInfo]:
        """Return structured summaries for all currently registered sessions."""

        with self._lock:
            sessions = list(self._sessions.items())
            closing_sessions = set(self._closing_sessions)

        summaries: list[SessionSummary] = []
        for session_id, session in sorted(sessions, key=lambda item: item[0]):
            summaries.append(
                self._summarize_session(session_id, session, closing=session_id in closing_sessions)
            )

        return OperationSuccess(SessionListInfo(sessions=summaries))

    def _summarize_session(
        self, session_id: int, session: SessionService, *, closing: bool
    ) -> SessionSummary:
        """Build one coherent summary while the session's own locks are held.

        ``list_sessions`` is only entered while holding no session workflow
        lock, so acquiring one here cannot invert the workflow/lifecycle order
        used by ``close_session``.
        """

        with session_workflow_context(session), session.runtime.lifecycle_lock:
            status = session.get_status().value
            runtime = session.runtime
            snapshot = runtime.snapshot(
                debugger_alive=status.debugger_alive, state_detail=status.state_detail
            )
            status = snapshot.status
            config = snapshot.config
            session_state = SessionState.CLOSING if closing else status.session_state

            return SessionSummary(
                session_id=session_id,
                session_state=session_state,
                execution_state=status.execution_state,
                target_loaded=status.target_loaded,
                debugger_alive=status.debugger_alive,
                program=config.program if config is not None else None,
                core=config.core if config is not None else None,
                working_dir=config.working_dir if config is not None else None,
                attached_pid=snapshot.attached_pid,
                current_thread_id=snapshot.current_thread_id,
                current_frame=snapshot.current_frame,
                stop_reason=status.stop_reason,
                exit_code=status.exit_code,
                current_inferior_id=status.current_inferior_id,
                inferior_count=status.inferior_count,
                inferior_states=status.inferior_states,
                follow_fork_mode=status.follow_fork_mode,
                detach_on_fork=status.detach_on_fork,
                last_failure_message=snapshot.last_failure_message,
                endpoint=config.endpoint if config is not None else None,
                rr_replay=config.rr_replay if config is not None else None,
            )

    def close_session(self, session_id: int) -> OperationSuccess[SessionMessage] | OperationError:
        """Stop and remove a session in one explicit lifecycle operation."""

        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                self._closing_sessions.add(session_id)

        if session is None:
            return self._unknown_session_error(session_id)

        try:
            with session_workflow_context(session):
                if session.has_controller:
                    result = session.stop()
                elif session.is_running:
                    result = OperationError(
                        message=(
                            "Session state is inconsistent: session reports running "
                            "without an active controller."
                        )
                    )
                else:
                    session.runtime.mark_stopped()
                    result = OperationSuccess(SessionMessage(message="Session removed"))
        except Exception:
            with self._lock:
                self._closing_sessions.discard(session_id)
            raise

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
            results[session_id] = self._stop_session_for_shutdown(session)

        with self._lock:
            self._closing_sessions.clear()

        return results

    def _stop_session_for_shutdown(
        self, session: SessionService
    ) -> OperationSuccess[SessionMessage] | OperationError:
        """Stop one session with a timeout so a hung GDB cannot block shutdown."""

        if not session.has_controller:
            return OperationSuccess(SessionMessage(message="Session removed"))

        outcome: list[OperationSuccess[SessionMessage] | OperationError] = []

        def stop() -> None:
            try:
                with session_workflow_context(session):
                    outcome.append(session.stop())
            except Exception as exc:  # pragma: no cover - defensive during teardown
                outcome.append(OperationError(message=str(exc)))

        worker = threading.Thread(target=stop, name="session-shutdown", daemon=True)
        worker.start()
        worker.join(timeout=_SHUTDOWN_TIMEOUT_SEC)

        if worker.is_alive():
            return OperationError(
                message=(
                    "Timed out stopping a debugger session during shutdown; "
                    "the GDB process may need to be killed"
                )
            )
        if not outcome:
            return OperationError(message="Session stop produced no result")
        return outcome[0]
