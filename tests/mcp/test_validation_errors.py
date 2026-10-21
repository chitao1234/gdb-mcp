"""Tests for the documented ``validation_error`` envelope mapping."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import Mock

from mcp.types import TextContent

from gdb_mcp.domain import OperationSuccess, SessionMessage
from gdb_mcp.mcp.handlers import dispatch_tool_call
from gdb_mcp.session.registry import SessionRegistry
from tests.support import dig, json_object, mapping_list, text


def _dispatch(name: str, arguments: object) -> dict[str, object]:
    """Dispatch one raw payload and return the decoded error envelope."""

    result = asyncio.run(
        dispatch_tool_call(
            name,
            arguments,
            Mock(spec=SessionRegistry),
            logger=logging.getLogger("test-validation-errors"),
        )
    )
    assert result.isError is True
    content = result.content[0]
    assert isinstance(content, TextContent)
    payload = json.loads(content.text)
    assert isinstance(payload, dict)
    return payload


def _field_errors(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the documented field-error entries from a validation envelope."""

    return mapping_list(dig(payload, "details", "field_errors"))


class TestValidationErrorEnvelope:
    """Validation failures should map onto actionable field errors."""

    def test_missing_conditional_field_names_the_union_tag(self) -> None:
        """A missing kind-specific field should produce the documented message."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "create", "breakpoint": {"kind": "code"}},
        )

        assert payload["status"] == "error"
        assert payload["code"] == "validation_error"
        assert payload["action"] == "create"
        assert payload["tool"] == "gdb_breakpoint_manage"
        assert payload["message"] == "breakpoint.location is required for kind=code"
        assert _field_errors(payload) == [
            {
                "field": "breakpoint.location",
                "issue": "missing",
                "message": "breakpoint.location is required for kind=code",
            }
        ]

    def test_multiple_failures_are_all_reported(self) -> None:
        """Every failure should be visible; truncation is the caller's decision."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {
                "session_id": 1,
                "action": "create",
                "breakpoint": {"kind": "code", "bogus": 1},
            },
        )

        assert payload["message"] == (
            "breakpoint.location is required for kind=code; " "breakpoint.bogus is not allowed"
        )
        assert [entry["field"] for entry in _field_errors(payload)] == [
            "breakpoint.location",
            "breakpoint.bogus",
        ]

    def test_missing_discriminator_is_reported_as_missing_field(self) -> None:
        """A missing kind should point at the discriminator property itself."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "create", "breakpoint": {"location": "main"}},
        )

        assert payload["message"] == "breakpoint.kind is required"
        assert _field_errors(payload)[0]["field"] == "breakpoint.kind"
        assert _field_errors(payload)[0]["issue"] == "missing"

    def test_unknown_action_lists_expected_values(self) -> None:
        """An invalid action should list the accepted values."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "explode"},
        )

        assert payload["code"] == "validation_error"
        assert text(payload["message"]).startswith("Unknown action 'explode'")
        assert "expected one of" in text(payload["message"])
        assert _field_errors(payload)[0] == {
            "field": "action",
            "issue": "invalid",
            "message": payload["message"],
        }

    def test_missing_session_id_mentions_the_action(self) -> None:
        """Missing top-level fields should name the selected action."""

        payload = _dispatch("gdb_session_query", {"action": "status"})

        assert payload["message"] == "session_id is required for action=status"
        assert _field_errors(payload)[0]["field"] == "session_id"
        assert _field_errors(payload)[0]["issue"] == "missing"

    def test_unknown_field_reports_not_allowed(self) -> None:
        """Unexpected keys should be reported as not allowed."""

        payload = _dispatch(
            "gdb_session_query",
            {"session_id": 1, "action": "status", "unexpected": True},
        )

        assert _field_errors(payload)[0]["field"] == "unexpected"
        assert _field_errors(payload)[0]["issue"] == "not_allowed"

    def test_model_validator_failure_keeps_the_domain_message(self) -> None:
        """Custom model validators should surface their own message."""

        payload = _dispatch(
            "gdb_breakpoint_manage",
            {"session_id": 1, "action": "update", "breakpoint": {"number": 1}, "changes": {}},
        )

        assert payload["message"] == "changes: At least one breakpoint change is required"
        assert _field_errors(payload)[0]["field"] == "changes"
        assert payload["action"] == "update"

    def test_non_object_arguments_fail_as_validation_error(self) -> None:
        """Non-object arguments should still produce a structured error."""

        payload = _dispatch("gdb_session_query", ["not", "an", "object"])

        assert payload["code"] == "validation_error"
        assert payload["message"] == "Tool arguments must be a JSON object"
        assert payload["tool"] == "gdb_session_query"
        assert _field_errors(payload)[0]["field"] == "(arguments)"


class TestSuccessfulDispatch:
    """Successful calls should not be flagged as MCP errors."""

    def test_success_result_keeps_is_error_false(self) -> None:
        """The dispatcher should only set isError for error envelopes."""

        manager = Mock(spec=SessionRegistry)
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
        content = result.content[0]
        assert isinstance(content, TextContent)
        payload = json_object(content.text)
        assert payload["status"] == "success"
