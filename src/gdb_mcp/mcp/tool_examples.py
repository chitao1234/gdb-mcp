"""Validated example payloads published with selected tool input schemas.

Every entry is verified against its typed request model by
``tests/mcp/test_tool_examples.py``; the examples are attached to the tool's
published ``inputSchema`` under the standard JSON Schema ``examples`` keyword.
"""

from __future__ import annotations

from ..contracts import (
    TOOL_BREAKPOINT_MANAGE,
    TOOL_CAPTURE_BUNDLE,
    TOOL_EXECUTION_MANAGE,
    TOOL_INFERIOR_MANAGE,
    TOOL_INSPECT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
)

TOOL_EXAMPLES: dict[str, tuple[dict[str, object], ...]] = {
    TOOL_SESSION_QUERY: (
        {"action": "list"},
        {"session_id": 7, "action": "status"},
    ),
    TOOL_INFERIOR_MANAGE: (
        {
            "session_id": 7,
            "action": "create",
            "inferior": {"executable": "/bin/ls", "make_current": True},
        },
        {"session_id": 7, "action": "remove", "inferior": {"inferior_id": 2}},
        {"session_id": 7, "action": "set_follow_fork_mode", "inferior": {"mode": "child"}},
    ),
    TOOL_EXECUTION_MANAGE: (
        {"session_id": 7, "action": "run"},
        {
            "session_id": 7,
            "action": "run",
            "execution": {
                "args": ["--mode", "fast"],
                "wait_until": "acknowledged",
                "timeout_sec": 5,
            },
        },
        {"session_id": 7, "action": "continue"},
        {"session_id": 7, "action": "interrupt"},
        {
            "session_id": 7,
            "action": "wait_for_stop",
            "execution": {"timeout_sec": 60, "stop_reasons": ["breakpoint-hit"]},
        },
    ),
    TOOL_BREAKPOINT_MANAGE: (
        {
            "session_id": 7,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
        {
            "session_id": 7,
            "action": "create",
            "breakpoint": {"kind": "watch", "expression": "state->ready", "access": "read"},
        },
        {
            "session_id": 7,
            "action": "create",
            "breakpoint": {"kind": "catch", "event": "syscall", "argument": "open"},
        },
        {
            "session_id": 7,
            "action": "update",
            "breakpoint": {"number": 4},
            "changes": {"condition": "count > 100"},
        },
        {"session_id": 7, "action": "disable", "breakpoint": {"number": 4}},
    ),
    TOOL_INSPECT_QUERY: (
        {
            "session_id": 7,
            "action": "evaluate",
            "query": {"expression": "config->retries", "context": {"thread_id": 2}},
        },
        {
            "session_id": 7,
            "action": "memory",
            "query": {"address": "&config", "count": 16},
        },
        {
            "session_id": 7,
            "action": "disassembly",
            "query": {
                "location": {"kind": "function", "function": "process_data"},
                "instruction_count": 24,
                "mode": "mixed",
            },
        },
        {
            "session_id": 7,
            "action": "source",
            "query": {
                "location": {
                    "kind": "file_range",
                    "file": "src/main.c",
                    "start_line": 40,
                    "end_line": 60,
                }
            },
        },
    ),
    TOOL_WORKFLOW_BATCH: (
        {
            "session_id": 7,
            "steps": [
                {
                    "tool": "gdb_breakpoint_manage",
                    "label": "set-main",
                    "arguments": {
                        "action": "create",
                        "breakpoint": {"kind": "code", "location": "main"},
                    },
                },
                {"tool": "gdb_execution_manage", "arguments": {"action": "run"}},
            ],
        },
        {"session_id": 7, "steps": [{"tool": "gdb_capture_bundle"}]},
    ),
    TOOL_CAPTURE_BUNDLE: (
        {
            "session_id": 7,
            "bundle_name": "crash-7",
            "expressions": ["config->retries"],
            "memory_ranges": [
                {"address": "&config", "count": 16},
                {"address": "&state", "count": 8, "offset": 4},
            ],
        },
    ),
    TOOL_RUN_UNTIL_FAILURE: (
        {
            "startup": {"program": "./server", "working_dir": "/tmp/server"},
            "run_args": ["--stress", "--seed", "42"],
            "max_iterations": 100,
        },
        {
            "startup": {"program": "./server"},
            "setup_steps": [
                {
                    "tool": "gdb_breakpoint_manage",
                    "arguments": {
                        "action": "create",
                        "breakpoint": {"kind": "code", "location": "critical_path"},
                    },
                }
            ],
            "failure": {"stop_reasons": ["signal-received"]},
            "capture": {"bundle_name_prefix": "flaky", "include_backtraces": True},
        },
    ),
}
