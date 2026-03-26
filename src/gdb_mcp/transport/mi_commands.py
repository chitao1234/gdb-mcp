"""Helpers for constructing GDB/MI command strings."""

from __future__ import annotations

from collections.abc import Sequence


def is_cli_command(command: str) -> bool:
    """Return True when the command should be run via CLI passthrough."""

    return not command.strip().startswith("-")


def escape_mi_string(value: str) -> str:
    """Escape a string so it can be embedded inside a quoted MI argument."""

    escaped_parts: list[str] = []
    for char in value:
        if char == "\\":
            escaped_parts.append("\\\\")
        elif char == '"':
            escaped_parts.append('\\"')
        elif char == "\n":
            escaped_parts.append("\\n")
        elif char == "\r":
            escaped_parts.append("\\r")
        elif char == "\t":
            escaped_parts.append("\\t")
        else:
            escaped_parts.append(char)

    return "".join(escaped_parts)


def quote_mi_string(value: str) -> str:
    """Return one fully quoted MI string argument."""

    return f'"{escape_mi_string(value)}"'


def build_evaluate_expression_command(expression: str) -> str:
    """Build a safe `-data-evaluate-expression` command."""

    return f"-data-evaluate-expression {quote_mi_string(expression)}"


def build_exec_arguments_command(args: Sequence[str]) -> str:
    """Build a safe `-exec-arguments` command from structured argv."""

    encoded_args = " ".join(quote_mi_string(arg) for arg in args)
    return f"-exec-arguments {encoded_args}" if encoded_args else "-exec-arguments"


def wrap_cli_command(command: str) -> str:
    """Wrap a CLI command in `-interpreter-exec` for structured MI transport."""

    return f"-interpreter-exec console {quote_mi_string(command)}"
