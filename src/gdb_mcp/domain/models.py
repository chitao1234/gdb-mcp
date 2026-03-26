"""Typed domain models shared across service layers."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SessionStatusSnapshot:
    """Serializable snapshot of the externally visible session status."""

    is_running: bool
    target_loaded: bool
    has_controller: bool
