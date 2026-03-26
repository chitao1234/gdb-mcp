"""Domain types for the GDB MCP server."""

from .errors import FatalTransportError, GdbMcpError, TransportError, ValidationFailure
from .models import SessionStatusSnapshot
from .results import OperationError, OperationSuccess

__all__ = [
    "FatalTransportError",
    "GdbMcpError",
    "OperationError",
    "OperationSuccess",
    "SessionStatusSnapshot",
    "TransportError",
    "ValidationFailure",
]
