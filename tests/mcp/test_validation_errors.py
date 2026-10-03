"""Tests for the documented ``validation_error`` envelope mapping."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import Mock

from gdb_mcp.domain import OperationSuccess, SessionMessage
from gdb_mcp.mcp.handlers import dispatch_tool_call


def _dispatch(name: str, arguments: object, manager: Mock) -> dict[str, Any]:
    result = asyncio.run(
        dispatch_tool_call(
            name,
            arguments,
            manager,
            logger=logging.getLogger("test-validation-errors"),
        )
    )
    assert result.isError is True
    return json.loads(result.content[0].text)


class TestValidationErrorEnvelope:
    """Validation failures should map onto actionable field errors."""

    def test_missing_conditional_field_names_the_union_tag(self):
        """A missing kind-specific field should produce the documented message."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "create", "breakpoint": {"kind": "code"}},
            Mock(),
        )

        assert payload["status"] == "error"
        assert payload["code"] == "validation_error"
        assert payload["action"] == "create"
        assert payload["tool"] == "gdb_breakpoint_manage"
        assert payload["message"] == "breakpoint.location is required for kind=code"
        assert payload["details"]["field_errors"] == [
            {
                "field": "breakpoint.location",
                "issue": "missing",
                "message": "breakpoint.location is required for kind=code",
            }
        ]

    def test_missing_discriminator_is_reported_as_missing_field(self):
        """A missing kind should point at the discriminator property itself."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "create", "breakpoint": {"location": "main"}},
            Mock(),
        )

        assert payload["message"] == "breakpoint.kind is required"
        assert payload["details"]["field_errors"][0]["field"] == "breakpoint.kind"
        assert payload["details"]["field_errors"][0]["issue"] == "missing"

    def test_unknown_action_lists_expected_values(self):
        """An invalid action should list the accepted values."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "explode"},
            Mock(),
        )

        assert payload["code"] == "validation_error"
        assert payload["message"].startswith("Unknown action 'explode'")
        assert "expected one of" in payload["message"]
        assert payload["details"]["field_errors"][0] == {
            "field": "action",
            "issue": "invalid",
            "message": payload["message"],
        }

    def test_missing_session_id_mentions_the_action(self):
        """Missing top-level fields should name the selected action."""

        payload = _dispatch("gdb_session_query", {"action": "status"}, Mock())

        assert payload["message"] == "session_id is required for action=status"
        assert payload["details"]["field_errors"][0]["field"] == "session_id"
        assert payload["details"]["field_errors"][0]["issue"] == "missing"

    def test_unknown_field_reports_not_allowed(self):
        """Unexpected keys should be reported as not allowed."""

        payload = _dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "unexpected": True},
            Mock(),
        )

        assert payload["details"]["field_errors"][0]["field"] == "unexpected"
        assert payload["details"]["field_errors"][0]["issue"] == "not_allowed"

    def test_model_validator_failure_keeps_the_domain_message(self):
        """Custom model validators should surface their own message."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "update", "breakpoint": {"number": 1}, "changes": {}},
            Mock(),
        )

        assert payload["message"] == "changes: At least one breakpoint change is required"
        assert payload["details"]["field_errors"][0]["field"] == "changes"
        assert payload["action"] == "update"

    def test_non_object_arguments_fail_as_validation_error(self):
        """Non-object arguments should still produce a structured error."""

        payload = _dispatch("gdb_session_query", ["not", "an", "object"], Mock())

        assert payload["code"] == "validation_error"
        assert payload["message"] == "Tool arguments must be a JSON object"
        assert payload["tool"] == "gdb_session_query"
        assert payload["details"]["field_errors"][0]["field"] == "(arguments)"


class TestSuccessfulDispatch:
    """Successful calls should not be flagged as MCP errors."""

    def test_success_result_keeps_is_error_false(self):
        """The dispatcher should only set isError for error envelopes."""

        manager = Mock()
        manager.close_session.return_value = OperationSuccess(
            SessionMessage(message="Session stopped")
        )

        result = asyncio.run(
            dispatch_tool_call(
                "gdb_session_manage",
                {"session_id": 1, "action": "stop", "session": {}},
                manager,
                logger=logging.getLogger("test-validation-errors"),
            )
        )

        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "success"
