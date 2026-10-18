"""Breakpoint-focused session API tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from gdb_mcp.domain import (
    BreakpointListInfo,
    BreakpointRecord,
    OperationError,
    OperationSuccess,
    result_to_mapping,
)
from gdb_mcp.session.breakpoints import SessionBreakpointService, _insert_blocked_reason
from gdb_mcp.session.command_runner import SessionCommandRunner
from gdb_mcp.session.service import SessionService
from tests.support import dig, mapping, mapping_list, sequence, text

MiRecordBuilder = Callable[..., dict[str, object]]
MiConsoleBuilder = Callable[[str], dict[str, object]]


class _FakeStdinView(Protocol):
    """Read-only view of the recording stdin double installed by conftest."""

    writes: list[bytes]


class _FakeIoManagerView(Protocol):
    stdin: _FakeStdinView


class _ControllerDouble(Protocol):
    io_manager: _FakeIoManagerView


ScriptedSessionBuilder = Callable[..., tuple[SessionService, _ControllerDouble]]


class _StubBreakpointService(SessionBreakpointService):
    """Breakpoint service that serves a fixed inventory for unit tests."""

    def __init__(self, records: list[BreakpointRecord]) -> None:
        super().__init__(command_runner=cast(SessionCommandRunner, object()))
        self._records = records

    def list_breakpoints(self) -> OperationSuccess[BreakpointListInfo]:
        return OperationSuccess(
            BreakpointListInfo(breakpoints=self._records, count=len(self._records))
        )


def test_insert_blocked_warning_omits_unknown_breakpoint_number() -> None:
    """A blocked insert whose created record lacks a number must not print None."""

    service = _StubBreakpointService([{"original-location": "start", "type": "breakpoint"}])
    error = OperationError(
        message="Cannot insert breakpoint 1.\nCannot access memory at address 0x0\n"
    )

    result = service._attribute_insert_failure("start", error)

    assert isinstance(result, OperationSuccess)
    assert len(result.warnings) == 1
    assert "None" not in result.warnings[0]
    assert "the requested breakpoint" in result.warnings[0]


def test_insert_blocked_reason_prefers_the_line_after_the_warning() -> None:
    """The reason is the line following the 'Cannot insert' warning, not earlier output."""

    message = (
        "Breakpoint 2 at 0x5555\n"
        "Cannot insert breakpoint 1.\n"
        "Cannot access memory at address 0x0\n"
    )

    assert _insert_blocked_reason(message) == "Cannot access memory at address 0x0"


class TestBreakpointApi:
    """Test breakpoint management through the public API."""

    def test_set_breakpoint_simple(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Setting a simple breakpoint should return breakpoint details."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "bkpt": {
                            "number": "1",
                            "type": "breakpoint",
                            "addr": "0x12345",
                            "func": "main",
                        }
                    }
                )
            ]
        )

        result = result_to_mapping(session.set_breakpoint("main"))

        assert result["status"] == "success"
        assert "breakpoint" in result
        assert dig(result, "breakpoint", "func") == "main"
        assert controller.io_manager.stdin.writes[0].decode().endswith('-break-insert "main"\n')

    def test_set_breakpoint_with_condition(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Conditional and temporary flags should be preserved in the command."""

        session, controller = scripted_running_session([mi_result({"bkpt": {"number": "1"}})])

        result = result_to_mapping(
            session.set_breakpoint(
                "foo.c:42",
                condition="x > 10",
                temporary=True,
            )
        )

        assert result["status"] == "success"
        command = controller.io_manager.stdin.writes[0].decode()
        assert '-break-insert -t -c "x > 10" "foo.c:42"' in command

    def test_set_breakpoint_quotes_locations_with_spaces(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Breakpoint locations should remain one MI argument even with spaces."""

        session, controller = scripted_running_session([mi_result({"bkpt": {"number": "1"}})])

        result = result_to_mapping(session.set_breakpoint("/tmp/my source.c:12"))

        assert result["status"] == "success"
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith('-break-insert "/tmp/my source.c:12"\n')
        )

    def test_list_breakpoints(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Listing breakpoints should surface the breakpoint table body."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {"number": "1", "type": "breakpoint"},
                                {"number": "2", "type": "breakpoint"},
                            ]
                        }
                    }
                )
            ]
        )

        result = result_to_mapping(session.list_breakpoints())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert len(sequence(result["breakpoints"])) == 2
        assert controller.io_manager.stdin.writes[0].decode().endswith("-break-list\n")

    def test_set_watchpoint(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Watchpoint creation should list the resulting watchpoint record."""

        session, controller = scripted_running_session(
            [mi_result({"wpt": {"number": "2", "exp": "value"}})],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "2",
                                    "type": "hw watchpoint",
                                    "what": "value",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_watchpoint("value", access="access"))

        assert result["status"] == "success"
        assert dig(result, "breakpoint", "number") == "2"
        assert dig(result, "breakpoint", "type") == "hw watchpoint"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-break-watch -a "value"\n')
        assert written[1].endswith("-break-list\n")

    def test_set_watchpoint_handles_hw_awpt_payload(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Access watchpoints should accept the hw-awpt result payload shape."""

        session, _controller = scripted_running_session(
            [mi_result({"hw-awpt": {"number": "2", "exp": "value"}})],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "2",
                                    "type": "acc watchpoint",
                                    "what": "value",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_watchpoint("value", access="access"))

        assert result["status"] == "success"
        assert dig(result, "breakpoint", "number") == "2"
        assert dig(result, "breakpoint", "type") == "acc watchpoint"

    def test_set_watchpoint_errors_when_breakpoint_refresh_fails(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Watchpoint creation should error when the follow-up inventory refresh fails."""

        session, controller = scripted_running_session(
            [mi_result({"wpt": {"number": "2", "exp": "value"}})],
            [mi_result({"msg": "break-list failed"}, message="error")],
        )

        result = result_to_mapping(session.set_watchpoint("value"))

        assert result["status"] == "error"
        assert "could not be confirmed" in text(result["message"])
        assert dig(result, "details", "created_breakpoint_number") == 2
        assert dig(result, "details", "create_payload") == {"wpt": {"number": "2", "exp": "value"}}
        assert dig(result, "details", "refresh_error", "message") == "break-list failed"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-break-watch "value"\n')
        assert written[1].endswith("-break-list\n")

    def test_delete_watchpoint(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Watchpoint deletion should reuse GDB's shared breakpoint namespace."""

        session, controller = scripted_running_session([mi_result()])

        result = result_to_mapping(session.delete_watchpoint(4))

        assert result["status"] == "success"
        assert "Watchpoint 4 deleted" in text(result["message"])
        assert controller.io_manager.stdin.writes[0].decode().endswith("-break-delete 4\n")

    def test_set_catchpoint(
        self,
        scripted_running_session: ScriptedSessionBuilder,
        mi_console: MiConsoleBuilder,
        mi_result: MiRecordBuilder,
    ) -> None:
        """Catchpoint creation should parse the CLI catchpoint number and refresh details."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [],
                        }
                    }
                )
            ],
            [
                mi_console("Catchpoint 3 (fork)\n"),
                mi_result(),
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "3",
                                    "type": "catchpoint",
                                    "catch-type": "fork",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_catchpoint("fork", temporary=True))

        assert result["status"] == "success"
        assert dig(result, "breakpoint", "number") == "3"
        assert dig(result, "breakpoint", "type") == "catchpoint"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-break-list\n")
        assert written[1].endswith('-interpreter-exec console "tcatch fork"\n')
        assert written[2].endswith("-break-list\n")

    def test_set_temporary_catchpoint_parses_temporary_prefix(
        self,
        scripted_running_session: ScriptedSessionBuilder,
        mi_console: MiConsoleBuilder,
        mi_result: MiRecordBuilder,
    ) -> None:
        """Temporary catchpoint output should parse correctly without false errors."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [],
                        }
                    }
                )
            ],
            [
                mi_console("Temporary catchpoint  4 (throw)\n"),
                mi_result(),
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "4",
                                    "type": "catchpoint",
                                    "catch-type": "throw",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_catchpoint("throw", temporary=True))

        assert result["status"] == "success"
        assert dig(result, "breakpoint", "number") == "4"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-break-list\n")
        assert written[1].endswith('-interpreter-exec console "tcatch throw"\n')
        assert written[2].endswith("-break-list\n")

    def test_set_catchpoint_errors_when_refreshed_inventory_misses_created_number(
        self,
        scripted_running_session: ScriptedSessionBuilder,
        mi_console: MiConsoleBuilder,
        mi_result: MiRecordBuilder,
    ) -> None:
        """Catchpoint creation should error if the refreshed inventory omits the created number."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [],
                        }
                    }
                )
            ],
            [
                mi_console("Catchpoint 3 (fork)\n"),
                mi_result(),
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "7",
                                    "type": "catchpoint",
                                    "catch-type": "fork",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_catchpoint("fork"))

        assert result["status"] == "error"
        assert "did not return breakpoint 3" in text(result["message"])
        assert dig(result, "details", "created_breakpoint_number") == 3
        assert dig(result, "details", "create_output") == "Catchpoint 3 (fork)"
        assert dig(result, "details", "refreshed_breakpoints") == [
            {
                "number": "7",
                "type": "catchpoint",
                "catch-type": "fork",
            }
        ]
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-break-list\n")
        assert written[1].endswith('-interpreter-exec console "catch fork"\n')
        assert written[2].endswith("-break-list\n")

    def test_set_breakpoint_reports_blocking_existing_breakpoint(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """A stale uninsertable breakpoint should be named, not blamed on the new location."""

        session, controller = scripted_running_session(
            [
                mi_result(
                    {
                        "msg": (
                            "Warning:\nCannot insert breakpoint 1.\n"
                            "Cannot access memory at address 0x114d"
                        )
                    },
                    message="error",
                )
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "1",
                                    "type": "breakpoint",
                                    "addr": "0x000000000000114d",
                                    "original-location": "*0x114d",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_breakpoint("t.c:3"))

        assert result["status"] == "error"
        assert result["code"] == "breakpoint_insert_blocked"
        assert "existing breakpoint(s) 1 cannot be inserted" in text(result["message"])
        assert "Cannot access memory at address 0x114d" in text(result["message"])
        details = mapping(result["details"])
        assert details["requested_location"] == "t.c:3"
        assert details["blocked_breakpoint_numbers"] == [1]
        assert mapping_list(details["blocked_breakpoints"])[0]["number"] == "1"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-break-insert "t.c:3"\n')
        assert written[1].endswith("-break-list\n")

    def test_set_breakpoint_confirms_when_blocker_preempts_the_result(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """When GDB created the new breakpoint anyway, report success and warn about the blocker."""

        session, _controller = scripted_running_session(
            [
                mi_result(
                    {
                        "msg": (
                            "Cannot insert breakpoint 1.\n" "Cannot access memory at address 0x114d"
                        )
                    },
                    message="error",
                )
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "1",
                                    "type": "breakpoint",
                                    "addr": "0x000000000000114d",
                                    "original-location": "*0x114d",
                                },
                                {
                                    "number": "2",
                                    "type": "breakpoint",
                                    "func": "main",
                                    "file": "t.c",
                                    "line": "3",
                                    "original-location": "t.c:3",
                                },
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_breakpoint("t.c:3"))

        assert result["status"] == "success"
        assert dig(result, "breakpoint", "number") == "2"
        assert result["warnings"]
        assert "existing breakpoint(s) 1 cannot be inserted" in text(
            sequence(result["warnings"])[0]
        )

    def test_set_breakpoint_matches_normalized_original_location(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """A created breakpoint is found even if GDB stores a quoted location."""

        session, _controller = scripted_running_session(
            [
                mi_result(
                    {
                        "msg": (
                            "Cannot insert breakpoint 1.\n" "Cannot access memory at address 0x114d"
                        )
                    },
                    message="error",
                )
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "1",
                                    "type": "breakpoint",
                                    "addr": "0x000000000000114d",
                                    "original-location": "*0x114d",
                                },
                                {
                                    "number": "2",
                                    "type": "breakpoint",
                                    "func": "main",
                                    "original-location": '"t.c:3"',
                                },
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_breakpoint("t.c:3"))

        assert result["status"] == "success"
        assert dig(result, "breakpoint", "number") == "2"

    def test_set_breakpoint_reports_when_requested_breakpoint_cannot_be_inserted(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """When the requested breakpoint itself is the blocker, attribute the failure to it."""

        session, _controller = scripted_running_session(
            [
                mi_result(
                    {
                        "msg": (
                            "Warning:\nCannot insert breakpoint 1.\n"
                            "Cannot access memory at address 0x114d"
                        )
                    },
                    message="error",
                )
            ],
            [
                mi_result(
                    {
                        "BreakpointTable": {
                            "body": [
                                {
                                    "number": "1",
                                    "type": "breakpoint",
                                    "addr": "0x000000000000114d",
                                    "original-location": "*0x114d",
                                }
                            ]
                        }
                    }
                )
            ],
        )

        result = result_to_mapping(session.set_breakpoint("*0x114d"))

        assert result["status"] == "error"
        assert result["code"] == "breakpoint_insert_blocked"
        assert result["message"] == (
            "Breakpoint 1 at *0x114d cannot be inserted " "(Cannot access memory at address 0x114d)"
        )
        assert dig(result, "details", "created_breakpoint", "number") == "1"

    def test_set_breakpoint_leaves_unrelated_errors_untouched(
        self, scripted_running_session: ScriptedSessionBuilder, mi_result: MiRecordBuilder
    ) -> None:
        """Errors that are not insert-blocked warnings must pass through unchanged."""

        session, controller = scripted_running_session(
            [mi_result({"msg": 'No symbol "nope" in current context.'}, message="error")]
        )

        result = result_to_mapping(session.set_breakpoint("nope"))

        assert result["status"] == "error"
        assert result["code"] == "error"
        assert result["message"] == 'No symbol "nope" in current context.'
        assert "blocked_breakpoint_numbers" not in mapping(result["details"])
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert len(written) == 1
