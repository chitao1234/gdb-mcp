"""Unit tests for typed domain result helpers."""

from gdb_mcp.domain import OperationError, OperationSuccess, SessionMessage, result_to_mapping


class TestDomainResults:
    """Test conversion helpers for typed internal results."""

    def test_result_to_mapping_wraps_scalar_success_payload(self):
        """Scalar success payloads should be wrapped into the external value shape."""

        payload = result_to_mapping(OperationSuccess(42))

        assert payload == {"status": "success", "value": 42}

    def test_result_to_mapping_adds_warnings_for_dataclass_payloads(self):
        """Warnings should be surfaced when the payload itself has no warnings field."""

        payload = result_to_mapping(
            OperationSuccess(
                SessionMessage(message="started"),
                warnings=("debug symbols missing",),
            )
        )

        assert payload == {
            "status": "success",
            "message": "started",
            "warnings": ["debug symbols missing"],
        }

    def test_result_to_mapping_normalizes_nested_dataclasses_and_tuples(self):
        """Nested dataclass payloads should serialize into builtin JSON-like containers."""

        payload = result_to_mapping(
            OperationSuccess(
                {
                    "messages": (
                        SessionMessage(message="first"),
                        SessionMessage(message="second"),
                    )
                }
            )
        )

        assert payload == {
            "status": "success",
            "messages": [
                {"message": "first"},
                {"message": "second"},
            ],
        }

    def test_result_to_mapping_serializes_error_code_and_nested_details(self):
        """Error payloads should preserve machine-readable code and nested details."""

        payload = result_to_mapping(
            OperationError(
                message="boom",
                code="unknown_tool",
                fatal=True,
                details={"tool": "x", "command": "-thread-info"},
            )
        )

        assert payload == {
            "status": "error",
            "code": "unknown_tool",
            "message": "boom",
            "fatal": True,
            "tool": "x",
            "details": {"command": "-thread-info"},
        }
