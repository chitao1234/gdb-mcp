"""Domain types for the GDB MCP server."""

from .errors import FatalTransportError, GdbMcpError, TransportError, ValidationFailure
from .models import SessionStatusSnapshot
from .results import OperationError, OperationResult, OperationSuccess, from_legacy_result

__all__ = [
    "FatalTransportError",
    "GdbMcpError",
    "OperationError",
    "OperationResult",
    "OperationSuccess",
    "SessionStatusSnapshot",
    "TransportError",
    "ValidationFailure",
    "from_legacy_result",
]
