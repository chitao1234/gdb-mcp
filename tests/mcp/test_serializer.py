"""Unit tests for MCP serializer typed-result support."""

import json

from gdb_mcp.domain import OperationError, OperationSuccess, SessionMessage
from gdb_mcp.mcp.serializer import result_to_payload, serialize_exception, serialize_result


class TestMcpSerializer:
    """Test serialization of typed MCP results."""

    def test_result_to_payload_for_success(self):
        """OperationSuccess should serialize its payload unchanged."""

        payload = result_to_payload(OperationSuccess({"status": "success", "value": 42}))

        assert payload == {"status": "success", "value": 42}

    def test_result_to_payload_for_error(self):
        """OperationError should serialize to the standard external error shape."""

        payload = result_to_payload(
            OperationError(
                message="boom",
                fatal=True,
                details={"tool": "gdb_get_status"},
            )
        )

        assert payload == {
            "status": "error",
            "message": "boom",
            "fatal": True,
            "tool": "gdb_get_status",
        }

    def test_result_to_payload_preserves_reserved_error_fields(self):
        """Error details should not overwrite the standard error envelope."""

        payload = result_to_payload(
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

    def test_serialize_exception_includes_tool_name(self):
        """Unexpected exceptions should still include the originating tool name."""

        contents = serialize_exception("gdb_get_status", RuntimeError("bad"))
        payload = json.loads(contents[0].text)

        assert payload == {
            "status": "error",
            "message": "bad",
            "tool": "gdb_get_status",
        }

    def test_serialize_result_serializes_typed_payloads(self):
        """Typed payload objects should serialize through the shared result mapper."""

        contents = serialize_result(OperationSuccess(SessionMessage(message="ok")))
        payload = json.loads(contents[0].text)

        assert payload == {"status": "success", "message": "ok"}

    def test_serialize_result_preserves_warning_payload_contract(self):
        """Serializer output should include wrapper warnings for typed payloads."""

        contents = serialize_result(
            OperationSuccess(
                SessionMessage(message="ok"),
                warnings=("symbols missing",),
            )
        )
        payload = json.loads(contents[0].text)

        assert payload == {
            "status": "success",
            "message": "ok",
            "warnings": ["symbols missing"],
        }

    def test_result_to_payload_normalizes_nested_typed_payloads(self):
        """Nested typed payload objects should be converted into JSON-ready structures."""

        payload = result_to_payload(
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
