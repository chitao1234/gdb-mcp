"""Tests for the canonical fields the adapters guarantee."""

from __future__ import annotations

import pytest

from gdb_mcp.domain.adapters import (
    breakpoint_record,
    breakpoint_records,
    frame_info_from_payload,
    frame_record,
    frame_records,
    frame_selection_info_from_payload,
    register_records,
    thread_selection_info_from_payload,
)
from gdb_mcp.domain.errors import DebuggerDecodeError


class TestBreakpointCanonicalFields:
    """GDB spells booleans and counters as strings; the records do not."""

    def test_enabled_becomes_a_boolean_and_times_an_integer(self) -> None:
        record = breakpoint_record({"bkpt": {"number": "1", "enabled": "y", "times": "3"}})

        assert record.enabled is True
        assert record.times == 3
        assert record.number == "1"

    def test_disabled_breakpoint(self) -> None:
        record = breakpoint_record({"enabled": "n", "times": "0"})

        assert record.enabled is False
        assert record.times == 0

    def test_missing_fields_stay_missing(self) -> None:
        record = breakpoint_record({"number": "1"})

        assert record.enabled is None
        assert record.times is None
        assert record.to_payload() == {"number": "1", "locations": []}

    def test_undeclared_fields_are_kept(self) -> None:
        """A payload field the record does not declare survives round-tripping."""

        record = breakpoint_record({"number": "1", "disp": "keep", "thread-groups": ["i1"]})

        assert record.to_payload()["disp"] == "keep"
        assert record.to_payload()["thread-groups"] == ["i1"]

    def test_flat_location_fields_become_one_location(self) -> None:
        record = breakpoint_record(
            {
                "number": "2",
                "type": "breakpoint",
                "addr": "0x401000",
                "func": "main",
                "file": "main.c",
                "fullname": "/tmp/main.c",
                "line": "7",
                "column": "3",
            }
        )

        assert "addr" not in record.to_payload()
        assert "func" not in record.to_payload()
        assert record.locations[0].func == "main"
        assert record.locations[0].line == 7
        assert record.locations[0].column == 3
        assert record.locations[0].resolved is True

    def test_multi_location_payload_is_published_as_locations(self) -> None:
        record = breakpoint_record(
            {
                "number": "3",
                "type": "breakpoint",
                "locations": [
                    {"addr": "0x401000", "func": "a", "line": "1"},
                    {"func": "b", "line": "2"},
                ],
            }
        )

        assert [location.func for location in record.locations] == ["a", "b"]
        assert [location.resolved for location in record.locations] == [True, False]

    def test_watchpoint_details_are_derived_from_gdb_fields(self) -> None:
        record = breakpoint_record(
            {
                "number": "4",
                "type": "read watchpoint",
                "addr": "0x7ffffff0",
                "what": "counter",
                "size": "4",
            }
        )

        assert "what" not in record.to_payload()
        assert record.watch is not None
        assert record.watch.access == "read"
        assert record.watch.address == "0x7ffffff0"
        assert record.watch.size == 4
        assert record.watch.spec == "counter"

    def test_a_non_numeric_hit_count_is_a_contract_violation(self) -> None:
        """MI promises a number, so anything else is raised instead of hidden."""

        with pytest.raises(DebuggerDecodeError):
            breakpoint_record({"number": "1", "times": "many"})

    def test_a_flag_that_is_neither_y_nor_n_is_a_contract_violation(self) -> None:
        """Reading a malformed flag as `False` would disable a live breakpoint."""

        with pytest.raises(DebuggerDecodeError):
            breakpoint_record({"number": "1", "enabled": "maybe"})

    def test_breakpoint_table_records_are_canonicalized(self) -> None:
        records = breakpoint_records(
            {
                "BreakpointTable": {
                    "body": [
                        {"number": "1", "enabled": "y", "times": "2"},
                        {"number": "2", "enabled": "n", "times": "0"},
                    ]
                }
            }
        )

        assert [record.enabled for record in records] == [True, False]
        assert [record.times for record in records] == [2, 0]


class TestFrameCanonicalFields:
    """Frame levels and source lines arrive as strings and publish as integers."""

    def test_stack_levels_become_integers(self) -> None:
        frames = frame_records({"stack": [{"level": "0", "func": "main"}, {"level": "1"}]})

        assert [frame.level for frame in frames] == [0, 1]
        assert frames[0].func == "main"

    def test_frame_lines_become_integers(self) -> None:
        frame = frame_record({"level": "0", "line": "42"})

        assert frame.line == 42
        assert frame.to_payload() == {"level": 0, "line": 42}

    def test_invalid_frame_line_is_rejected(self) -> None:
        with pytest.raises(DebuggerDecodeError):
            frame_record({"line": "unknown"})

    def test_single_frame_record(self) -> None:
        record = frame_record({"level": "3", "func": "worker"})

        assert record.level == 3
        assert record.func == "worker"

    def test_current_frame_level_is_canonical(self) -> None:
        info = frame_info_from_payload({"frame": {"level": "2", "func": "worker"}})

        assert info.frame is not None
        assert info.frame.level == 2

    def test_frame_selection_level_is_canonical(self) -> None:
        info = frame_selection_info_from_payload(1, {"frame": {"level": "1"}})

        assert info.frame is not None
        assert info.frame.level == 1

    def test_thread_selection_frame_level_is_canonical(self) -> None:
        info = thread_selection_info_from_payload(2, {"frame": {"level": "0"}})

        assert info.frame is not None
        assert info.frame.level == 0

    def test_frame_without_a_level_is_untouched(self) -> None:
        frames = frame_records({"stack": [{"func": "main"}]})

        assert frames[0].func == "main"
        assert frames[0].level is None
        assert frames[0].to_payload() == {"func": "main"}


class TestRegisterCanonicalFields:
    """MI spells a register number as a string; the record indexes names with it."""

    def test_register_numbers_become_integers(self) -> None:
        records = register_records(
            {"register-values": [{"number": "0", "value": "0x1"}, {"number": "17"}]}
        )

        assert [record.number for record in records] == [0, 17]
        assert records[0].value == "0x1"

    def test_a_register_without_a_number_is_untouched(self) -> None:
        records = register_records({"register-values": [{"value": "0x1"}]})

        assert records[0].number is None
        assert records[0].to_payload() == {"value": "0x1"}

    def test_a_non_numeric_register_number_is_a_contract_violation(self) -> None:
        with pytest.raises(DebuggerDecodeError):
            register_records({"register-values": [{"number": "rax"}]})
