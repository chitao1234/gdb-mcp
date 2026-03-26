"""Unit tests for typed domain result helpers."""

from gdb_mcp.domain import OperationError, OperationSuccess, result_to_mapping


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
