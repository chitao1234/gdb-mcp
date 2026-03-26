"""Session lifecycle state definitions."""

from enum import Enum


class SessionState(str, Enum):
    """Lifecycle states for a debugger session."""

    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"
