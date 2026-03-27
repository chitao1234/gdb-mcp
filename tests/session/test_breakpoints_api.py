"""Breakpoint-focused session API tests."""

from __future__ import annotations

from gdb_mcp.domain import result_to_mapping


class TestBreakpointApi:
    """Test breakpoint management through the public API."""

    def test_set_breakpoint_simple(self, scripted_running_session, mi_result):
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
        assert result["breakpoint"]["func"] == "main"
        assert controller.io_manager.stdin.writes[0].decode().endswith('-break-insert "main"\n')

    def test_set_breakpoint_with_condition(self, scripted_running_session, mi_result):
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

    def test_set_breakpoint_quotes_locations_with_spaces(self, scripted_running_session, mi_result):
        """Breakpoint locations should remain one MI argument even with spaces."""

        session, controller = scripted_running_session([mi_result({"bkpt": {"number": "1"}})])

        result = result_to_mapping(session.set_breakpoint("/tmp/my source.c:12"))

        assert result["status"] == "success"
        assert (
            controller.io_manager.stdin.writes[0]
            .decode()
            .endswith('-break-insert "/tmp/my source.c:12"\n')
        )

    def test_list_breakpoints(self, scripted_running_session, mi_result):
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
        assert len(result["breakpoints"]) == 2
        assert controller.io_manager.stdin.writes[0].decode().endswith("-break-list\n")

    def test_set_watchpoint(self, scripted_running_session, mi_result):
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
        assert result["breakpoint"]["number"] == "2"
        assert result["breakpoint"]["type"] == "hw watchpoint"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-break-watch -a "value"\n')
        assert written[1].endswith("-break-list\n")

    def test_set_watchpoint_handles_hw_awpt_payload(self, scripted_running_session, mi_result):
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
        assert result["breakpoint"]["number"] == "2"
        assert result["breakpoint"]["type"] == "acc watchpoint"

    def test_delete_watchpoint(self, scripted_running_session, mi_result):
        """Watchpoint deletion should reuse GDB's shared breakpoint namespace."""

        session, controller = scripted_running_session([mi_result()])

        result = result_to_mapping(session.delete_watchpoint(4))

        assert result["status"] == "success"
        assert "Watchpoint 4 deleted" in result["message"]
        assert controller.io_manager.stdin.writes[0].decode().endswith("-break-delete 4\n")

    def test_set_catchpoint(self, scripted_running_session, mi_console, mi_result):
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
        assert result["breakpoint"]["number"] == "3"
        assert result["breakpoint"]["type"] == "catchpoint"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-break-list\n")
        assert written[1].endswith('-interpreter-exec console "tcatch fork"\n')
        assert written[2].endswith("-break-list\n")

    def test_set_temporary_catchpoint_parses_temporary_prefix(
        self, scripted_running_session, mi_console, mi_result
    ):
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
        assert result["breakpoint"]["number"] == "4"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith("-break-list\n")
        assert written[1].endswith('-interpreter-exec console "tcatch throw"\n')
        assert written[2].endswith("-break-list\n")
