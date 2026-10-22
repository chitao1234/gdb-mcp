"""Failures interpreting data received from a debugger."""


class DebuggerDecodeError(Exception):
    """A debugger field used by an operation could not be decoded."""
