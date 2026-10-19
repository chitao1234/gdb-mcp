"""Unit tests for MCP serializer typed-result support."""

from __future__ import annotations

import json

from mcp.types import CallToolResult, TextContent

from gdb_mcp.domain import OperationError, OperationSuccess, SessionMessage
from gdb_mcp.mcp.serializer import result_to_payload, serialize_exception, serialize_result


def _payload_text(result: CallToolResult) -> str:
    """Return the JSON text the serializer published as the first content item."""

    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


class TestMcpSerializer:
    """Test serialization of typed MCP results."""

    def test_result_to_payload_preserves_v2_success_envelope(self) -> None:
        """The serializer should preserve explicit v2 success envelope fields."""

        payload = result_to_payload(
            OperationSuccess(
                {
                    "action": "status",
                    "result": {
                        "session": {
                            "execution_state": "paused",
                        }
                    },
                }
            )
        )

        assert payload == {
            "status": "success",
            "action": "status",
            "result": {
                "session": {
                    "execution_state": "paused",
                }
            },
        }

    def test_result_to_payload_includes_error_code_and_nested_details(self) -> None:
        """Error payloads should expose a stable code and nested details object."""

        payload = result_to_payload(
            OperationError(
                message="breakpoint.location is required for kind=code",
                code="validation_error",
                details={
                    "action": "create",
                    "field_errors": [
                        {
                            "field": "breakpoint.location",
                            "issue": "missing",
                        }
                    ],
                },
            )
        )

        assert payload == {
            "status": "error",
            "code": "validation_error",
            "message": "breakpoint.location is required for kind=code",
            "action": "create",
            "details": {
                "field_errors": [
                    {
                        "field": "breakpoint.location",
                        "issue": "missing",
                    }
                ]
            },
        }

    def test_result_to_payload_for_success(self) -> None:
        """OperationSuccess should serialize its payload unchanged."""

        payload = result_to_payload(OperationSuccess({"status": "success", "value": 42}))

        assert payload == {"status": "success", "value": 42}

    def test_result_to_payload_for_error(self) -> None:
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
            "code": "error",
            "message": "boom",
            "fatal": True,
            "tool": "gdb_get_status",
        }

    def test_result_to_payload_preserves_reserved_error_fields(self) -> None:
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
            "code": "error",
            "message": "boom",
            "fatal": True,
            "tool": "x",
        }

    def test_serialize_exception_includes_tool_name(self) -> None:
        """Unexpected exceptions should still include the originating tool name."""

        contents = serialize_exception("gdb_get_status", RuntimeError("bad"))
        payload = json.loads(_payload_text(contents))

        assert payload == {
            "status": "error",
            "code": "internal_error",
            "message": "bad",
            "tool": "gdb_get_status",
        }

    def test_serialize_result_serializes_typed_payloads(self) -> None:
        """Typed payload objects should serialize through the shared result mapper."""

        contents = serialize_result(OperationSuccess(SessionMessage(message="ok")))
        payload = json.loads(_payload_text(contents))

        assert payload == {"status": "success", "message": "ok"}

    def test_serialize_result_preserves_warning_payload_contract(self) -> None:
        """Serializer output should include wrapper warnings for typed payloads."""

        contents = serialize_result(
            OperationSuccess(
                SessionMessage(message="ok"),
                warnings=("symbols missing",),
            )
        )
        payload = json.loads(_payload_text(contents))

        assert payload == {
            "status": "success",
            "message": "ok",
            "warnings": ["symbols missing"],
        }

    def test_serialize_result_marks_errors_as_mcp_errors(self) -> None:
        """Error results should set the MCP isError flag."""

        contents = serialize_result(OperationError(message="boom"))

        assert contents.isError is True
        assert json.loads(_payload_text(contents))["status"] == "error"

    def test_serialize_result_marks_success_as_non_error(self) -> None:
        """Success results should keep the MCP isError flag false."""

        contents = serialize_result(OperationSuccess(SessionMessage(message="ok")))

        assert contents.isError is False

    def test_serialize_result_exposes_structured_content(self) -> None:
        """Structured tool output should mirror the JSON text payload."""

        contents = serialize_result(
            OperationSuccess(
                SessionMessage(message="ok"),
                warnings=("symbols missing",),
            )
        )

        assert contents.structuredContent == {
            "status": "success",
            "message": "ok",
            "warnings": ["symbols missing"],
        }
        assert json.loads(_payload_text(contents)) == contents.structuredContent

    def test_serialize_error_exposes_structured_content(self) -> None:
        """Errors should be readable as structured output, not only as text."""

        contents = serialize_result(
            OperationError(
                message="No registers.",
                code="no_registers",
                details={"execution_state": "exited"},
            )
        )

        assert contents.isError is True
        assert contents.structuredContent == {
            "status": "error",
            "code": "no_registers",
            "message": "No registers.",
            "details": {"execution_state": "exited"},
        }
        assert json.loads(_payload_text(contents)) == contents.structuredContent

    def test_serialize_exception_exposes_structured_content(self) -> None:
        """Unexpected exceptions should carry structured output too."""

        contents = serialize_exception("gdb_get_status", RuntimeError("bad"))

        assert contents.structuredContent == {
            "status": "error",
            "code": "internal_error",
            "message": "bad",
            "tool": "gdb_get_status",
        }

    def test_result_to_payload_normalizes_nested_typed_payloads(self) -> None:
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
