"""Unit tests for MCP serializer typed-result support."""

import json

from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.mcp.serializer import result_to_payload, serialize_exception, serialize_result


class TestMcpSerializer:
    """Test serialization of typed MCP results."""

    def test_result_to_payload_for_success(self):
        """OperationSuccess should serialize its payload unchanged."""

        payload = result_to_payload(OperationSuccess({"status": "success", "value": 42}))

        assert payload == {"status": "success", "value": 42}

    def test_result_to_payload_for_error(self):
        """OperationError should serialize to the legacy error JSON shape."""

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

    def test_serialize_exception_includes_tool_name(self):
        """Unexpected exceptions should still include the originating tool name."""

        contents = serialize_exception("gdb_get_status", RuntimeError("bad"))
        payload = json.loads(contents[0].text)

        assert payload == {
            "status": "error",
            "message": "bad",
            "tool": "gdb_get_status",
        }

    def test_serialize_result_still_accepts_legacy_dicts(self):
        """The serializer should remain compatible with dict payloads during migration."""

        contents = serialize_result({"status": "success", "ok": True})
        payload = json.loads(contents[0].text)

        assert payload == {"status": "success", "ok": True}
