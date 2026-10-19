"""Tests for LLDB `source list` command building and output parsing."""

from __future__ import annotations

import pytest

from gdb_mcp.transport.lldb_source import (
    SourceListLine,
    build_source_list_command,
    count_for_window,
    first_line_number,
    needs_quoting,
    parse_source_list_output,
    quote_lldb_argument,
    select_window,
)

# Verbatim samples measured from lldb-20; the grammar is what these lock in.
WINDOW_OUTPUT = (
    "   9   \t    int nums[3] = {1, 2, 3};\n"
    "   10  \t    int total = nums[0] + nums[1] + nums[2];\n"
    "   11  \t    return add(p.x + p.y, total);\n"
    "   12  \t}\n"
)
SYMBOL_OUTPUT = (
    "File: /tmp/opencode/spike/probe.c\n"
    "   8   \tint describe(struct point p) {\n"
    "   9   \t    int nums[3] = {1, 2, 3};\n"
    "   10  \t\n"
)
ADDRESS_OUTPUT = (
    "/tmp/opencode/spike/probe_c`describe at /tmp/opencode/spike/probe.c:8:30\n"
    "-> 8   \tint describe(struct point p) {\n"
    "   9   \t    int nums[3] = {1, 2, 3};\n"
    "    \t        ^\n"
)
EMBEDDED_OUTPUT = "File: /tmp/-ac5777.probe.c\n" "   9   \t    int nums[3] = {1, 2, 3};\n"


class TestBuildSourceListCommand:
    """The command shapes that were measured to work."""

    def test_file_line_and_count(self) -> None:
        assert build_source_list_command(file="probe.c", line=9, count=3) == (
            "source list -f probe.c -l 9 -c 3"
        )

    def test_function_selector(self) -> None:
        assert build_source_list_command(function="describe") == "source list -n describe"

    def test_function_selector_with_count(self) -> None:
        assert build_source_list_command(function="describe", count=5) == (
            "source list -n describe -c 5"
        )

    def test_address_selector(self) -> None:
        assert build_source_list_command(address="0x1181") == "source list -a 0x1181"

    def test_linespec_selector(self) -> None:
        assert build_source_list_command(linespec="probe.c:9") == "source list -y probe.c:9"

    def test_path_with_spaces_is_quoted(self) -> None:
        assert build_source_list_command(file="/tmp/my source.c", line=1) == (
            'source list -f "/tmp/my source.c" -l 1'
        )

    def test_requires_exactly_one_selector(self) -> None:
        with pytest.raises(ValueError):
            build_source_list_command()
        with pytest.raises(ValueError):
            build_source_list_command(file="probe.c", function="describe")

    def test_line_requires_a_file(self) -> None:
        with pytest.raises(ValueError):
            build_source_list_command(line=9)

    def test_rejects_out_of_range_numbers(self) -> None:
        with pytest.raises(ValueError):
            build_source_list_command(file="probe.c", line=0)
        with pytest.raises(ValueError):
            build_source_list_command(file="probe.c", line=1, count=0)

    def test_rejects_newlines_in_arguments(self) -> None:
        with pytest.raises(ValueError):
            build_source_list_command(file="probe.c\nquit")


class TestQuoteLldbArgument:
    """Quoting follows lldb/source/Utility/Args.cpp."""

    def test_plain_values_need_no_quotes(self) -> None:
        assert needs_quoting("probe.c") is False
        assert needs_quoting("/tmp/probe.c") is False

    def test_special_characters_need_quotes(self) -> None:
        for value in ("my source.c", "a'b", "a`b", "a\\b", 'a"b'):
            assert needs_quoting(value) is True

    def test_escapes_backslashes_and_quotes(self) -> None:
        assert quote_lldb_argument('a\\b"c') == '"a\\\\b\\"c"'

    def test_rejects_newlines(self) -> None:
        with pytest.raises(ValueError):
            quote_lldb_argument("a\nb")


class TestCountForWindow:
    """`-c N` prints N+1 lines, so the request is one short."""

    def test_single_line_window(self) -> None:
        assert count_for_window(1) == 1

    def test_multi_line_window(self) -> None:
        assert count_for_window(10) == 9

    def test_rejects_empty_window(self) -> None:
        with pytest.raises(ValueError):
            count_for_window(0)


class TestParseSourceListOutput:
    """Grammar and the silent failure modes."""

    def test_window_output(self) -> None:
        result = parse_source_list_output(WINDOW_OUTPUT)

        assert result.file is None
        assert result.errors == ()
        assert [line.number for line in result.lines] == [9, 10, 11, 12]
        assert result.lines[0].text == "    int nums[3] = {1, 2, 3};"
        assert result.has_lines is True

    def test_symbol_output_carries_the_file_header(self) -> None:
        result = parse_source_list_output(SYMBOL_OUTPUT)

        assert result.file == "/tmp/opencode/spike/probe.c"
        assert [line.number for line in result.lines] == [8, 9, 10]
        assert result.lines[2].text == ""

    def test_embedded_source_header_is_a_synthetic_path(self) -> None:
        result = parse_source_list_output(EMBEDDED_OUTPUT)

        assert result.file == "/tmp/-ac5777.probe.c"
        assert [line.number for line in result.lines] == [9]

    def test_address_output_keeps_the_marked_current_line(self) -> None:
        result = parse_source_list_output(ADDRESS_OUTPUT)

        assert [line.number for line in result.lines] == [8, 9]
        assert result.lines[0].text == "int describe(struct point p) {"

    def test_strips_the_command_echo(self) -> None:
        result = parse_source_list_output(
            "(lldb) source list -f probe.c -l 9 -c 3\n" + WINDOW_OUTPUT
        )

        assert [line.number for line in result.lines] == [9, 10, 11, 12]

    def test_missing_file_is_an_error(self) -> None:
        result = parse_source_list_output('error: Could not find source file "nosuchfile.c".\n')

        assert result.errors == ('Could not find source file "nosuchfile.c".',)
        assert result.has_lines is False

    def test_past_end_of_file_is_silently_empty(self) -> None:
        result = parse_source_list_output("")

        assert result.lines == ()
        assert result.errors == ()
        assert result.file is None
        assert result.has_lines is False

    def test_invalid_line_count_is_an_error(self) -> None:
        result = parse_source_list_output("error: invalid line count: '-1'\n")

        assert result.errors == ("invalid line count: '-1'",)


class TestSelectWindow:
    """`-c` overshoots and LLDB clamps, so the window is re-applied."""

    def test_slices_to_the_requested_range(self) -> None:
        lines = [SourceListLine(number=n, text=f"line {n}") for n in range(8, 14)]

        selected = select_window(lines, start_line=9, end_line=11)

        assert [line.number for line in selected] == [9, 10, 11]

    def test_rejects_an_inverted_range(self) -> None:
        with pytest.raises(ValueError):
            select_window([], start_line=5, end_line=4)

    def test_first_line_number(self) -> None:
        assert first_line_number([]) is None
        assert first_line_number([SourceListLine(number=9, text="x")]) == 9
