"""Unit tests for parsed MI response helpers."""

from __future__ import annotations

from gdb_mcp.transport.mi_parser import extract_mi_result_payload, parse_mi_responses


class TestMiParser:
    """Test parser-only behavior independent of the session layer."""

    def test_parse_mi_responses_collects_streams_and_notifications(self):
        """Parser output should retain console, result, and notify records."""

        responses = [
            {"type": "console", "payload": "Test output\n"},
            {"type": "result", "message": "done", "payload": {"msg": "done"}},
            {"type": "notify", "payload": {"msg": "thread-created"}},
        ]

        parsed = parse_mi_responses(responses)

        assert "Test output\n" in parsed.console
        assert parsed.result == {"msg": "done"}
        assert parsed.result_class == "done"
        assert {"msg": "thread-created"} in parsed.notify

    def test_extract_mi_result_payload_from_command_execution_shape(self):
        """Inner MI result payloads should be extracted from command results."""

        payload = extract_mi_result_payload(
            {
                "status": "success",
                "result": {
                    "result": {"threads": []},
                    "result_class": "done",
                },
            }
        )

        assert payload == {"threads": []}

    def test_parsed_mi_response_extracts_error_message(self):
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
