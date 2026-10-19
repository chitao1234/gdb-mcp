"""Tests for the canonical fields the adapters guarantee."""

from __future__ import annotations

from gdb_mcp.domain.adapters import (
    breakpoint_record,
    breakpoint_records,
    frame_info_from_payload,
    frame_records,
    frame_selection_info_from_payload,
    thread_selection_info_from_payload,
)


class TestBreakpointCanonicalFields:
    """GDB spells booleans and counters as strings; the contract does not."""

    def test_enabled_becomes_a_boolean_and_times_an_integer(self) -> None:
        record = breakpoint_record({"bkpt": {"number": "1", "enabled": "y", "times": "3"}})

        assert record["enabled"] is True
        assert record["times"] == 3
        assert record["number"] == "1"

    def test_disabled_breakpoint(self) -> None:
        record = breakpoint_record({"enabled": "n", "times": "0"})

        assert record["enabled"] is False
        assert record["times"] == 0

    def test_unparsable_values_are_left_alone(self) -> None:
        record = breakpoint_record({"enabled": "maybe", "times": "abc"})

        assert record["enabled"] == "maybe"
        assert record["times"] == "abc"

    def test_missing_fields_stay_missing(self) -> None:
        record = breakpoint_record({"number": "1"})

        assert "enabled" not in record
        assert "times" not in record

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

        assert [record["enabled"] for record in records] == [True, False]
        assert [record["times"] for record in records] == [2, 0]


class TestFrameCanonicalFields:
    """`level` arrives as a string and is published as an integer."""

    def test_stack_levels_become_integers(self) -> None:
        frames = frame_records({"stack": [{"level": "0", "func": "main"}, {"level": "1"}]})

        assert [frame["level"] for frame in frames] == [0, 1]
        assert frames[0]["func"] == "main"

    def test_current_frame_level_is_canonical(self) -> None:
        info = frame_info_from_payload({"frame": {"level": "2", "func": "worker"}})

        assert info.frame is not None
        assert info.frame["level"] == 2

    def test_frame_selection_level_is_canonical(self) -> None:
        info = frame_selection_info_from_payload(1, {"frame": {"level": "1"}})

        assert info.frame is not None
        assert info.frame["level"] == 1

    def test_thread_selection_frame_level_is_canonical(self) -> None:
        info = thread_selection_info_from_payload(2, {"frame": {"level": "0"}})

        assert info.frame is not None
        assert info.frame["level"] == 0

    def test_frame_without_a_level_is_untouched(self) -> None:
        frames = frame_records({"stack": [{"func": "main"}]})

        assert frames[0] == {"func": "main"}

    def test_unparsable_level_is_left_alone(self) -> None:
        frames = frame_records({"stack": [{"level": "top"}]})

        assert frames[0]["level"] == "top"
