"""Composed inspection service."""

from __future__ import annotations

from .inferior_state import InspectionInferiorStateMixin
from .memory import InspectionMemoryMixin
from .source import InspectionSourceMixin
from .threads import InspectionThreadsMixin
from .variables import InspectionVariablesMixin


class SessionInspectionService(
    InspectionThreadsMixin,
    InspectionInferiorStateMixin,
    InspectionVariablesMixin,
    InspectionMemoryMixin,
    InspectionSourceMixin,
):
    """Inspection and navigation helpers."""
