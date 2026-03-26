"""Helpers for constructing GDB/MI command strings."""


def is_cli_command(command: str) -> bool:
    """Return True when the command should be run via CLI passthrough."""

    return not command.strip().startswith("-")


def escape_mi_string(value: str) -> str:
    """Escape a string so it can be embedded inside a quoted MI argument."""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def wrap_cli_command(command: str) -> str:
    """Wrap a CLI command in `-interpreter-exec` for structured MI transport."""

    return f'-interpreter-exec console "{escape_mi_string(command)}"'
