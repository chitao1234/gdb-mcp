"""Unit tests for parsed MI response helpers."""

from __future__ import annotations

import pytest

from gdb_mcp.transport.mi_parser import (
    extract_mi_result_payload,
    mi_payload,
    parse_mi_responses,
)


class TestMiParser:
    """Test parser-only behavior independent of the session layer."""

    def test_parse_mi_responses_collects_streams_and_notifications(self) -> None:
        """Parser output should retain console, result, and notify records."""

        responses: list[dict[str, object]] = [
            {"type": "console", "payload": "Test output\n"},
            {"type": "result", "message": "done", "payload": {"msg": "done"}},
            {"type": "notify", "payload": {"msg": "thread-created"}},
        ]

        parsed = parse_mi_responses(responses)

        assert "Test output\n" in parsed.console
        assert parsed.result == {"msg": "done"}
        assert parsed.result_class == "done"
        assert {"message": None, "payload": {"msg": "thread-created"}} in parsed.notify

    def test_a_result_record_without_fields_has_no_payload(self) -> None:
        """`^done` carries no fields, which the parser records as an absent payload."""

        parsed = parse_mi_responses([{"type": "result", "message": "done", "payload": None}])

        assert parsed.result is None
        assert parsed.result_class == "done"

    def test_a_non_mapping_payload_is_rejected(self) -> None:
        """MI records are key/value fields, so any other payload shape is a bug."""

        with pytest.raises(TypeError, match="must be a mapping or absent"):
            parse_mi_responses([{"type": "result", "message": "done", "payload": ["a", "b"]}])

    def test_extract_mi_result_payload_from_a_command_result(self) -> None:
        """The MI result payload is read from a parsed command result."""

        parsed = parse_mi_responses(
            [{"type": "result", "message": "done", "payload": {"threads": []}}]
        )

        assert extract_mi_result_payload(parsed.to_dict()) == {"threads": []}

    def test_extract_mi_result_payload_without_a_result(self) -> None:
        """A command that reported no fields has no payload to extract."""

        parsed = parse_mi_responses([{"type": "result", "message": "done", "payload": None}])

        assert extract_mi_result_payload(parsed.to_dict()) is None

    def test_mi_payload_keeps_a_mapping_unchanged(self) -> None:
        """A mapping is the payload; it is passed through, not copied."""

        payload = {"reason": "breakpoint-hit"}

        assert mi_payload(payload) is payload
        assert mi_payload(None) is None

    def test_error_message_keeps_the_context_gdb_printed(self) -> None:
        """GDB puts the error in `msg` and context in the console; both are the message."""

        parsed = parse_mi_responses(
            [
                {"type": "console", "payload": '\nThread 1 (Thread 0x1 "probe"):\n'},
                {
                    "type": "result",
                    "message": "error",
                    "payload": {"msg": 'No symbol "nosuchvar" in current context.'},
                },
            ]
        )

        assert parsed.error_message() == (
            'Thread 1 (Thread 0x1 "probe"):\n' 'No symbol "nosuchvar" in current context.'
        )

    def test_error_message_does_not_repeat_a_message_it_already_contains(self) -> None:
        """When GDB printed the message to the console, it is not appended twice."""

        parsed = parse_mi_responses(
            [
                {"type": "console", "payload": "Cannot access memory at address 0x1\n"},
                {
                    "type": "result",
                    "message": "error",
                    "payload": {"msg": "Cannot access memory at address 0x1"},
                },
            ]
        )

        assert parsed.error_message() == "Cannot access memory at address 0x1"

    def test_parsed_mi_response_extracts_error_message(self) -> None:
        """MI error results should expose their human-readable message."""

        parsed = parse_mi_responses(
            [
                {
                    "type": "result",
                    "message": "error",
                    "payload": {"msg": "Thread ID 999 not known"},
                }
            ]
        )

        assert parsed.is_error_result() is True
        assert parsed.error_message() == "Thread ID 999 not known"
