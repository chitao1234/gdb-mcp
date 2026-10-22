"""Native bridge response framing and extension compatibility."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from gdb_mcp.session.lldb_bridge import BridgeClient
from gdb_mcp.session.lldb_bridge_types import BridgeError, BridgeLaunch


def _bridge(tmp_path: Path, response: object) -> BridgeClient:
    if os.name == "nt":
        pytest.skip("The fake bridge uses a Unix executable script")
    executable = tmp_path / "bridge"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"response = json.loads({json.dumps(response)!r})\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request['op'] == 'shutdown':\n"
        "        print(json.dumps({'request_id': request['request_id'], 'ok': True}), flush=True)\n"
        "        break\n"
        "    print(json.dumps(response), flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return BridgeClient(BridgeLaunch(executable, None, dict(os.environ)))


@pytest.mark.parametrize("response", [None, [], "unexpected", 7, False])
def test_non_object_response_is_a_protocol_failure(tmp_path: Path, response: object) -> None:
    client = _bridge(tmp_path, response)
    try:
        with pytest.raises(BridgeError, match="must be an object") as caught:
            client.request("initialize", timeout_sec=2)
        assert caught.value.code == "protocol_error"
    finally:
        client.close()


def test_bridge_response_preserves_unknown_and_dashed_fields(tmp_path: Path) -> None:
    fields = {
        "target-id": "native",
        "target_id": "distinct",
        "future-key": {"nested-key": [None, False, 0]},
    }
    client = _bridge(tmp_path, {"request_id": 1, "ok": True, **fields})
    try:
        assert client.request("initialize", timeout_sec=2) == fields
    finally:
        client.close()


def test_unknown_debugger_failure_keeps_its_code_and_message(tmp_path: Path) -> None:
    client = _bridge(
        tmp_path,
        {"request_id": 1, "ok": False, "error": "future debugger failure", "code": "future-code"},
    )
    try:
        with pytest.raises(BridgeError, match="future debugger failure") as caught:
            client.request("initialize", timeout_sec=2)
        assert caught.value.code == "future-code"
        assert client._responsive
    finally:
        client.close()
