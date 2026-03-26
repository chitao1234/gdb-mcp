"""Breakpoint-focused session API tests."""

from __future__ import annotations

from unittest.mock import patch

from gdb_mcp.domain import result_to_mapping


class TestBreakpointApi:
    """Test breakpoint management through the public API."""

    def test_set_breakpoint_simple(self, running_session, command_result):
        """Setting a simple breakpoint should return breakpoint details."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(
                command,
                result={
                    "result": {
                        "bkpt": {
                            "number": "1",
                            "type": "breakpoint",
                            "addr": "0x12345",
                            "func": "main",
                        }
                    }
                },
            )

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.set_breakpoint("main"))

        assert result["status"] == "success"
        assert "breakpoint" in result
        assert result["breakpoint"]["func"] == "main"

    def test_set_breakpoint_with_condition(self, running_session, command_result):
        """Conditional and temporary flags should be preserved in the command."""

        commands_executed: list[str] = []

        def mock_execute(command, **kwargs):
            del kwargs
            commands_executed.append(command)
            return command_result(command, result={"result": {"bkpt": {"number": "1"}}})

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(
                running_session.set_breakpoint(
                    "foo.c:42",
                    condition="x > 10",
                    temporary=True,
                )
            )

        assert result["status"] == "success"
        assert any("-break-insert" in command for command in commands_executed)

    def test_list_breakpoints(self, running_session, command_result):
        """Listing breakpoints should surface the breakpoint table body."""

        def mock_execute(command, **kwargs):
            del kwargs
            return command_result(
                command,
                result={
                    "result": {
                        "BreakpointTable": {
                            "body": [
                                {"number": "1", "type": "breakpoint"},
                                {"number": "2", "type": "breakpoint"},
                            ]
                        }
                    }
                },
            )

        with patch.object(running_session, "_execute_command_result", side_effect=mock_execute):
            result = result_to_mapping(running_session.list_breakpoints())

        assert result["status"] == "success"
        assert result["count"] == 2
        assert len(result["breakpoints"]) == 2
