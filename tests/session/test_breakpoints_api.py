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

    def test_set_watchpoint_errors_when_breakpoint_refresh_fails(
        self, scripted_running_session, mi_result
    ):
        """Watchpoint creation should error when the follow-up inventory refresh fails."""

        session, controller = scripted_running_session(
            [mi_result({"wpt": {"number": "2", "exp": "value"}})],
            [mi_result({"msg": "break-list failed"}, message="error")],
        )

        result = result_to_mapping(session.set_watchpoint("value"))

        assert result["status"] == "error"
        assert "could not be confirmed" in result["message"]
        assert result["details"]["created_breakpoint_number"] == 2
        assert result["details"]["create_payload"] == {"wpt": {"number": "2", "exp": "value"}}
        assert result["details"]["refresh_error"]["message"] == "break-list failed"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-break-watch "value"\n')
        assert written[1].endswith("-break-list\n")

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

    def test_set_catchpoint_errors_when_refreshed_inventory_misses_created_number(
        self, scripted_running_session, mi_console, mi_result
    ):
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
        assert "did not return breakpoint 3" in result["message"]
        assert result["details"]["created_breakpoint_number"] == 3
        assert result["details"]["create_output"] == "Catchpoint 3 (fork)"
        assert result["details"]["refreshed_breakpoints"] == [
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
        self, scripted_running_session, mi_result
    ):
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
        assert "existing breakpoint(s) 1 cannot be inserted" in result["message"]
        assert "Cannot access memory at address 0x114d" in result["message"]
        details = result["details"]
        assert details["requested_location"] == "t.c:3"
        assert details["blocked_breakpoint_numbers"] == [1]
        assert details["blocked_breakpoints"][0]["number"] == "1"
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert written[0].endswith('-break-insert "t.c:3"\n')
        assert written[1].endswith("-break-list\n")

    def test_set_breakpoint_confirms_when_blocker_preempts_the_result(
        self, scripted_running_session, mi_result
    ):
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
        assert result["breakpoint"]["number"] == "2"
        assert result["warnings"]
        assert "existing breakpoint(s) 1 cannot be inserted" in result["warnings"][0]

    def test_set_breakpoint_reports_when_requested_breakpoint_cannot_be_inserted(
        self, scripted_running_session, mi_result
    ):
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
        assert result["details"]["created_breakpoint"]["number"] == "1"

    def test_set_breakpoint_leaves_unrelated_errors_untouched(
        self, scripted_running_session, mi_result
    ):
        """Errors that are not insert-blocked warnings must pass through unchanged."""

        session, controller = scripted_running_session(
            [mi_result({"msg": 'No symbol "nope" in current context.'}, message="error")]
        )

        result = result_to_mapping(session.set_breakpoint("nope"))

        assert result["status"] == "error"
        assert result["code"] == "error"
        assert result["message"] == 'No symbol "nope" in current context.'
        assert "blocked_breakpoint_numbers" not in result["details"]
        written = [command.decode() for command in controller.io_manager.stdin.writes]
        assert len(written) == 1
