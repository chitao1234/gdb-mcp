"""Unit tests for MI command formatting helpers."""

from __future__ import annotations

from gdb_mcp.transport.mi_commands import (
    build_read_memory_command,
    escape_mi_string,
    is_cli_command,
    wrap_cli_command,
)


class TestMiCommands:
    """Test MI command formatting helpers."""

    def test_is_cli_command(self) -> None:
        """Commands without a leading dash should be treated as CLI commands."""

        assert is_cli_command("info threads") is True
        assert is_cli_command("-thread-info") is False

    def test_escape_mi_string(self) -> None:
        """Backslashes and double quotes should be escaped for MI strings."""

        assert escape_mi_string('say "hello" \\ world') == 'say \\"hello\\" \\\\ world'

    def test_escape_mi_string_escapes_control_characters(self) -> None:
        """Control characters should be escaped instead of being written literally."""

        assert escape_mi_string("line1\nline2\r\t") == "line1\\nline2\\r\\t"

    def test_wrap_cli_command(self) -> None:
        """CLI commands should be wrapped with interpreter-exec."""

        wrapped = wrap_cli_command('print "hello"')
        assert wrapped == '-interpreter-exec console "print \\"hello\\""'

    def test_wrap_cli_command_escapes_newlines(self) -> None:
        """Wrapped CLI commands should not contain literal line breaks."""

        wrapped = wrap_cli_command('print "hello"\nshow version\r')
        assert wrapped == '-interpreter-exec console "print \\"hello\\"\\nshow version\\r"'

    def test_build_read_memory_command(self) -> None:
        """Structured memory reads should quote the address expression safely."""

        command = build_read_memory_command("&value + 4", 16, offset=2)
        assert command == '-data-read-memory-bytes -o 2 "&value + 4" 16'
