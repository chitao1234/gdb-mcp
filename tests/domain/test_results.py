"""Unit tests for typed domain result helpers."""

from gdb_mcp.domain import OperationError, OperationSuccess, from_legacy_result


class TestDomainResults:
    """Test conversion helpers for typed internal results."""

    def test_from_legacy_result_wraps_success_payload(self):
        """Successful legacy payloads should become OperationSuccess."""

        result = from_legacy_result({"status": "success", "value": 42})

        assert isinstance(result, OperationSuccess)
        assert result.value == {"status": "success", "value": 42}

    def test_from_legacy_result_wraps_error_payload(self):
        """Error legacy payloads should become OperationError with preserved details."""

        result = from_legacy_result(
            {
                "status": "error",
                "message": "boom",
                "fatal": True,
                "command": "-thread-info",
            }
        )

        assert isinstance(result, OperationError)
        assert result.message == "boom"
        assert result.fatal is True
        assert result.details == {"command": "-thread-info"}
