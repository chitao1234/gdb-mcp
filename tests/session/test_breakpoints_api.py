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
