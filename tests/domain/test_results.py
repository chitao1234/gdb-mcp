"""Unit tests for typed domain result helpers."""

from gdb_mcp.domain import OperationError, OperationSuccess, SessionMessage, result_to_mapping


class TestDomainResults:
    """Test conversion helpers for typed internal results."""

    def test_result_to_mapping_wraps_scalar_success_payload(self):
        """Scalar success payloads should be wrapped into the external value shape."""

        payload = result_to_mapping(OperationSuccess(42))

        assert payload == {"status": "success", "value": 42}

    def test_result_to_mapping_serializes_error_details(self):
        """Structured error details should be merged into the external payload."""

        payload = result_to_mapping(
            OperationError(
                message="boom",
                fatal=True,
                details={"command": "-thread-info"},
            )
        )

        assert payload == {
            "status": "error",
            "message": "boom",
            "fatal": True,
            "command": "-thread-info",
        }

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

    def test_result_to_mapping_preserves_existing_payload_warnings(self):
        """Payload-defined warnings should take precedence over wrapper warnings."""

        payload = result_to_mapping(
            OperationSuccess(
                {"status": "success", "warnings": ["payload-warning"]},
                warnings=("wrapper-warning",),
            )
        )

        assert payload == {
            "status": "success",
            "warnings": ["payload-warning"],
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

    def test_result_to_mapping_does_not_serialize_error_code_field(self):
        """Internal error codes should not leak into the external payload shape by default."""

        payload = result_to_mapping(OperationError(message="boom", code="unknown_tool"))

        assert payload == {
            "status": "error",
            "message": "boom",
        }

    def test_result_to_mapping_preserves_reserved_error_envelope_keys(self):
        """Error details should not overwrite reserved top-level error fields."""

        payload = result_to_mapping(
            OperationError(
                message="boom",
                fatal=True,
                details={"status": "success", "message": "shadowed", "fatal": False, "tool": "x"},
            )
        )

        assert payload == {
            "status": "error",
            "message": "boom",
            "fatal": True,
            "tool": "x",
        }
