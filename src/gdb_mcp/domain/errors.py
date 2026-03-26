"""Typed internal errors used by the refactored service layers."""


class GdbMcpError(Exception):
    """Base class for internal errors in the GDB MCP server."""


class ValidationFailure(GdbMcpError):
    """Raised when a request cannot be validated."""


class TransportError(GdbMcpError):
    """Raised when the GDB transport layer fails."""


class FatalTransportError(TransportError):
    """Raised when the GDB transport has failed irrecoverably."""
