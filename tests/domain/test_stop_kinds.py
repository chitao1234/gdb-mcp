"""Tests for the canonical stop kind derived from debugger stop reasons."""

from __future__ import annotations

import pytest

from gdb_mcp.domain.stop_kinds import STOP_KINDS, stop_kind_from_reason


class TestStopKindFromReason:
    """GDB/MI and DAP spell the same event differently."""

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            # GDB/MI
            ("breakpoint-hit", "breakpoint"),
            ("watchpoint-trigger", "watchpoint"),
            ("read-watchpoint-trigger", "watchpoint"),
            ("access-watchpoint-trigger", "watchpoint"),
            ("end-stepping-range", "step"),
            ("function-finished", "step"),
            ("signal-received", "signal"),
            ("exited-normally", "exit"),
            ("exited-signalled", "exit"),
            # DAP
            ("breakpoint", "breakpoint"),
            ("data breakpoint", "watchpoint"),
            ("step", "step"),
            ("function breakpoint", "breakpoint"),
            ("instruction breakpoint", "breakpoint"),
            ("exception", "exception"),
            ("entry", "entry"),
            ("pause", "pause"),
            ("interrupt", "pause"),
        ],
    )
    def test_maps_known_reasons(self, reason: str, expected: str) -> None:
        assert stop_kind_from_reason(reason) == expected

    def test_is_case_insensitive_and_ignores_padding(self) -> None:
        assert stop_kind_from_reason("  Breakpoint-Hit  ") == "breakpoint"

    def test_unknown_reason_falls_back(self) -> None:
        assert stop_kind_from_reason("something-new") == "unknown"

    def test_missing_reason_is_unknown(self) -> None:
        assert stop_kind_from_reason(None) == "unknown"
        assert stop_kind_from_reason("") == "unknown"

    def test_a_data_breakpoint_wins_over_the_generic_word(self) -> None:
        """DAP reports a data breakpoint as a breakpoint; the phrase decides."""

        assert stop_kind_from_reason("data breakpoint") == "watchpoint"

    def test_published_kinds_are_the_enum_members(self) -> None:
        assert stop_kind_from_reason("breakpoint-hit") in STOP_KINDS
        assert stop_kind_from_reason(None) in STOP_KINDS
