"""Tests for `info inferiors` CLI parsing."""

from __future__ import annotations

from gdb_mcp.session.inferiors import parse_inferiors_output

# GDB 10+ prints a Connection column between Description and Executable.
_MODERN_HEADER = "  Num  Description       Connection           Executable        \n"
_MODERN_ROWS = (
    "* 1    process 4242      1 (native)           /tmp/app \n",
    "  2    <null>            1 (native)                             \n",
)

# GDB 7.1-8.3 prints Executable but no Connection.
_LEGACY_EXEC_HEADER = "  Num  Description       Executable        \n"
_LEGACY_EXEC_ROWS = (
    "* 1    process 1803825   /tmp/app \n",
    "  2    <null>                      \n",
)

# GDB 7.0 prints only Num and Description.
_LEGACY_BARE_HEADER = "  Num  Description      \n"
_LEGACY_BARE_ROWS = (
    "* 1    process 4242     \n",
    "  2    <null>           \n",
)


def test_parses_connection_column() -> None:
    payload = parse_inferiors_output(
        _MODERN_HEADER + "".join(_MODERN_ROWS), current_inferior_id=None
    )

    assert len(payload.inferiors) == 2
    assert payload.current_inferior_id == 1
    first, second = payload.inferiors
    assert first.inferior_id == 1
    assert first.is_current is True
    assert first.description == "process 4242"
    assert first.connection == "1 (native)"
    assert first.executable == "/tmp/app"
    assert second.is_current is False
    assert second.description == "<null>"
    assert second.connection == "1 (native)"
    assert second.executable is None
    assert "executable" not in second.to_payload()


def test_parses_executable_without_connection() -> None:
    payload = parse_inferiors_output(
        _LEGACY_EXEC_HEADER + "".join(_LEGACY_EXEC_ROWS), current_inferior_id=None
    )

    first = payload.inferiors[0]
    assert first.inferior_id == 1
    assert first.is_current is True
    assert first.description == "process 1803825"
    assert first.executable == "/tmp/app"
    assert first.connection is None
    assert "connection" not in first.to_payload()


def test_parses_bare_header() -> None:
    payload = parse_inferiors_output(
        _LEGACY_BARE_HEADER + "".join(_LEGACY_BARE_ROWS), current_inferior_id=None
    )

    first = payload.inferiors[0]
    assert first.inferior_id == 1
    assert first.is_current is True
    assert first.description == "process 4242"
    assert "connection" not in first.to_payload()
    assert "executable" not in first.to_payload()


def test_parses_stripped_output() -> None:
    """The CLI layer strips the output, dropping the header's leading spaces."""

    payload = parse_inferiors_output(
        (_MODERN_HEADER + "".join(_MODERN_ROWS)).strip(), current_inferior_id=None
    )

    first = payload.inferiors[0]
    assert first.inferior_id == 1
    assert first.is_current is True
    assert first.executable == "/tmp/app"


def test_parses_executable_containing_spaces() -> None:
    output = _MODERN_HEADER + "* 1    process 5         1 (native)           /opt/my app/bin \n"

    first = parse_inferiors_output(output, current_inferior_id=None).inferiors[0]
    assert first.executable == "/opt/my app/bin"


def test_reports_known_current_when_no_marker() -> None:
    output = _LEGACY_BARE_HEADER + "  2    <null>           \n"

    payload = parse_inferiors_output(output, current_inferior_id=7)

    assert payload.current_inferior_id == 7
    assert payload.inferiors[0].is_current is False


def test_no_inferiors_outputs_empty_inventory() -> None:
    payload = parse_inferiors_output("No inferiors.\n", current_inferior_id=1)

    assert len(payload.inferiors) == 0
    assert payload.inferiors == []
    assert payload.current_inferior_id == 1
