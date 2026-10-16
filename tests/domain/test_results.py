"""Unit tests for typed domain result helpers."""

import types

import pytest

from gdb_mcp.domain import (
    MessageResult,
    OperationError,
    OperationSuccess,
    SessionMessage,
    payload_to_mapping,
    result_to_mapping,
)


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

    def test_payload_to_mapping_copies_non_dict_mappings(self):
        """Read-only mappings should still serialize into plain JSON objects."""

        proxy = types.MappingProxyType({"key": [1, 2]})

        assert payload_to_mapping(proxy) == {"key": [1, 2]}

    def test_payload_to_mapping_rejects_non_json_values(self):
        """Unserializable values should fail at conversion, not at json.dumps."""

        with pytest.raises(TypeError):
            payload_to_mapping({"bad": {1, 2}})

    def test_payload_to_mapping_copies_dataclass_container_fields(self):
        """Dataclass fields are rebuilt, not aliased, without a deep copy."""

        inner = {"nested": [1, 2]}

        payload = payload_to_mapping(MessageResult(message="m", result=inner))

        assert payload["result"] == {"nested": [1, 2]}
        assert payload["result"] is not inner

    def test_payload_to_mapping_rejects_non_serializable_dataclass_field(self):
        """A dataclass holding a non-JSON field should fail during conversion."""

        with pytest.raises(TypeError):
            payload_to_mapping(MessageResult(message="m", result={"bad": {1, 2}}))
