# V2 Test Suite Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove legacy test-interface translation, migrate surviving tests to direct v2 tool usage, and shrink the suite to a smaller set of representative high-signal workflows.

**Architecture:** Replace the oversized legacy integration files with smaller direct-v2 integration modules that assert the actual public envelopes and payloads. Keep `tests/mcp/` and `tests/session/` as the primary owners of schema/routing and service semantics, trim only obviously redundant lower-layer tests, and update the test docs to codify the new ownership model.

**Tech Stack:** Python 3.12, pytest, real GDB-backed integration tests, Pydantic-backed MCP schemas, session service tests, `ruff`, `mypy`.

---

## File Structure

**Integration harness**

- Modify: `tests/integration/conftest.py`
  Remove the legacy translation/flattening layer, stop injecting module globals, and keep only direct runtime fixtures plus compile/session helpers.

**New focused integration files**

- Create: `tests/integration/program_sources.py`
  Shared C/C++ source strings used by the smaller v2-only integration modules.
- Create: `tests/integration/test_v2_session_core.py`
  Representative single-session workflows for lifecycle, breakpoints, execution, inferiors, batch, capture, and run-until-failure.
- Create: `tests/integration/test_v2_session_inspect.py`
  Representative direct-v2 context, inspect, startup, escape-hatch, and attach flows.
- Create: `tests/integration/test_v2_multi_session.py`
  Representative multi-session isolation and lifecycle workflows.

**Legacy integration files to remove**

- Delete: `tests/integration/test_gdb_integration.py`
- Delete: `tests/integration/test_multi_session.py`

**Low-signal lower-layer coverage**

- Modify: `tests/domain/test_results.py`
  Collapse overlapping error-envelope cases into a smaller set of core result-mapping tests.

**Test docs**

- Modify: `tests/README.md`
  Document the ownership model and the “no legacy tool names in tests” rule.

### Task 1: Replace The Integration Harness And Land Core V2 Workflows

**Files:**
- Create: `tests/integration/program_sources.py`
- Modify: `tests/integration/conftest.py`
- Create: `tests/integration/test_v2_session_core.py`
- Delete: `tests/integration/test_gdb_integration.py`
- Test/Verify: `uv run pytest -q tests/integration/test_v2_session_core.py`

**Testing approach:** `characterization/integration test`
Reason: This task replaces the legacy harness and lands the first direct-v2 real-GDB file. The value comes from exercising the real MCP runtime with the actual v2 contract rather than writing new unit seams.

- [ ] **Step 1: Add a shared source-fixture module for the new integration files**

```python
# tests/integration/program_sources.py

"""Shared program sources for direct-v2 integration tests."""

TEST_CPP_PROGRAM = """
#include <iostream>

int add(int a, int b) {
    int result = a + b;
    return result;
}

int multiply(int x, int y) {
    int product = x * y;
    return product;
}

int calculate(int num) {
    int sum = add(num, 10);
    int prod = multiply(sum, 2);
    return prod;
}

int main() {
    int value = 5;
    int result = calculate(value);
    std::cout << "Result: " << result << std::endl;
    return 0;
}
"""

CRASHING_C_PROGRAM = """
#include <signal.h>

int main(void) {
    raise(SIGABRT);
    return 0;
}
"""

WATCH_MEMORY_C_PROGRAM = """
int watched = 0x12345678;

int main(void) {
    watched = 0x12345679;
    return watched;
}
"""

FORKING_C_PROGRAM = """
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void) {
    pid_t pid = fork();
    if (pid == 0) {
        _exit(0);
    }
    waitpid(pid, 0, 0);
    return 0;
}
"""

DELAY_EXIT_C_PROGRAM = """
#include <unistd.h>

int main(void) {
    sleep(3);
    return 0;
}
"""

ATTACHABLE_C_PROGRAM = """
#include <unistd.h>

int main(void) {
    while (1) {
        sleep(1);
    }
    return 0;
}
"""

TEST_PROGRAM_1 = """
#include <stdio.h>

int double_value(int x) {
    return x * 2;
}

int main() {
    int num = 10;
    int result = double_value(num);
    printf("Result: %d\\n", result);
    return 0;
}
"""

TEST_PROGRAM_2 = """
#include <stdio.h>

int triple_value(int x) {
    return x * 3;
}

int main() {
    int num = 7;
    int result = triple_value(num);
    printf("Result: %d\\n", result);
    return 0;
}
"""
```

- [ ] **Step 2: Simplify `tests/integration/conftest.py` to a direct v2 harness**

```python
# tests/integration/conftest.py

@pytest.fixture(scope="module")
def call_gdb_tool(integration_runtime):
    """Invoke one MCP tool directly and deserialize the JSON response payload."""

    def invoke(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = asyncio.run(integration_runtime.call_tool(tool_name, arguments))
        return cast(dict[str, object], json.loads(result[0].text))

    return invoke


@pytest.fixture
def start_session_result(call_gdb_tool, default_init_commands):
    """Start a GDB session and return the full start payload."""

    def start(
        program: str,
        *,
        init_commands: list[str] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "program": program,
            "init_commands": (
                list(default_init_commands) if init_commands is None else init_commands
            ),
        }
        payload.update(kwargs)

        result = call_gdb_tool("gdb_session_start", payload)
        assert result["status"] == "success", f"Failed to start session: {result}"
        return result

    return start


@pytest.fixture
def stop_session(call_gdb_tool):
    """Stop a GDB session and optionally ignore cleanup failures."""

    def stop(session_id: int, *, ignore_errors: bool = False) -> dict[str, object]:
        result = call_gdb_tool(
            "gdb_session_manage",
            {"session_id": session_id, "action": "stop", "session": {}},
        )
        if not ignore_errors:
            assert result["status"] == "success", f"Failed to stop session: {result}"
        return result

    return stop
```

- [ ] **Step 3: Add `tests/integration/test_v2_session_core.py` and delete the legacy single-session file**

```python
# tests/integration/test_v2_session_core.py

"""Real-GDB integration coverage for core direct-v2 session workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .program_sources import (
    CRASHING_C_PROGRAM,
    DELAY_EXIT_C_PROGRAM,
    FORKING_C_PROGRAM,
    TEST_CPP_PROGRAM,
    WATCH_MEMORY_C_PROGRAM,
)


@pytest.fixture
def compiled_program(compile_program):
    return compile_program(TEST_CPP_PROGRAM, filename="test_program.cpp", compiler="g++")


@pytest.fixture
def session_id(compiled_program, start_session):
    return start_session(compiled_program)


@pytest.fixture
def compiled_program_and_core(compile_program_with_core):
    return compile_program_with_core(CRASHING_C_PROGRAM, filename="crash.c", compiler="gcc")


@pytest.mark.integration
def test_session_lifecycle_and_status_v2(compiled_program, start_session_result, stop_session, call_gdb_tool):
    start = start_session_result(compiled_program)
    session_id = start["session_id"]

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )

    assert status["status"] == "success"
    assert status["action"] == "status"
    assert status["result"]["is_running"] is True
    assert status["result"]["target_loaded"] is True
    assert status["result"]["execution_state"] == "not_started"

    stop = call_gdb_tool(
        "gdb_session_manage",
        {"session_id": session_id, "action": "stop", "session": {}},
    )
    assert stop["status"] == "success"
    assert stop["action"] == "stop"
    assert "stopped" in stop["result"]["message"].lower()

    after_stop = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert after_stop["status"] == "error"
    assert "Invalid session_id" in after_stop["message"]


@pytest.mark.integration
def test_breakpoint_query_run_and_status_v2(session_id, call_gdb_tool):
    create = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    assert create["status"] == "success"
    assert create["action"] == "create"

    listed = call_gdb_tool(
        "gdb_breakpoint_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert listed["result"]["count"] == 1

    run = call_gdb_tool(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": {}},
    )
    assert run["status"] == "success"
    assert run["action"] == "run"

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert status["result"]["execution_state"] == "paused"
    assert status["result"]["stop_reason"] == "breakpoint-hit"


@pytest.mark.integration
def test_breakpoint_manage_flow_v2(session_id, call_gdb_tool):
    first = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    second = call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )

    number = int(first["result"]["breakpoint"]["number"])
    other_number = int(second["result"]["breakpoint"]["number"])

    disable = call_gdb_tool(
        "gdb_breakpoint_manage",
        {"session_id": session_id, "action": "disable", "breakpoint": {"number": number}},
    )
    assert disable["status"] == "success"

    enable = call_gdb_tool(
        "gdb_breakpoint_manage",
        {"session_id": session_id, "action": "enable", "breakpoint": {"number": number}},
    )
    assert enable["status"] == "success"

    delete = call_gdb_tool(
        "gdb_breakpoint_manage",
        {"session_id": session_id, "action": "delete", "breakpoint": {"number": other_number}},
    )
    assert delete["status"] == "success"

    listed = call_gdb_tool(
        "gdb_breakpoint_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["result"]["count"] == 1


@pytest.mark.integration
def test_workflow_batch_v2(session_id, call_gdb_tool):
    batch = call_gdb_tool(
        "gdb_workflow_batch",
        {
            "session_id": session_id,
            "steps": [
                {
                    "tool": "gdb_breakpoint_manage",
                    "label": "break main",
                    "arguments": {
                        "action": "create",
                        "breakpoint": {"kind": "code", "location": "main"},
                    },
                },
                {
                    "tool": "gdb_execution_manage",
                    "label": "run",
                    "arguments": {"action": "run", "execution": {}},
                },
                {
                    "tool": "gdb_context_query",
                    "label": "stack",
                    "arguments": {"action": "backtrace", "query": {}},
                },
            ],
        },
    )

    assert batch["status"] == "success"
    assert batch["count"] == 3
    assert batch["error_count"] == 0
    assert batch["steps"][0]["tool"] == "gdb_breakpoint_manage"
    assert batch["steps"][1]["tool"] == "gdb_execution_manage"
    assert batch["steps"][2]["tool"] == "gdb_context_query"
    assert batch["steps"][2]["action"] == "backtrace"


@pytest.mark.integration
def test_capture_bundle_v2(session_id, tmp_path, call_gdb_tool):
    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )
    call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run", "execution": {}})

    bundle = call_gdb_tool(
        "gdb_capture_bundle",
        {"session_id": session_id, "output_dir": str(tmp_path), "bundle_name": "capture"},
    )
    assert bundle["status"] == "success"
    assert bundle["artifact_count"] > 0
    manifest = json.loads(Path(bundle["manifest_path"]).read_text())
    assert manifest["bundle_name"] == "capture"


@pytest.mark.integration
def test_run_until_failure_v2(compile_program, tmp_path, call_gdb_tool):
    crashing = compile_program(CRASHING_C_PROGRAM, filename="crash_signal.c", compiler="gcc")
    result = call_gdb_tool(
        "gdb_run_until_failure",
        {
            "startup": {"program": crashing, "init_commands": ["set disable-randomization on"]},
            "max_iterations": 1,
            "failure": {"stop_reasons": ["signal-received"]},
            "capture": {"enabled": True, "output_dir": str(tmp_path), "bundle_name": "failure"},
        },
    )
    assert result["status"] == "success"
    assert result["matched_failure"] is True
    assert result["capture_bundle"]["bundle_name"] == "failure"


@pytest.mark.integration
def test_watchpoint_v2(compile_program, start_session, stop_session, call_gdb_tool):
    program = compile_program(WATCH_MEMORY_C_PROGRAM, filename="watch.c", compiler="gcc")
    session_id = start_session(program)
    try:
        watch = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "watch", "expression": "watched", "access": "write"},
            },
        )
        assert watch["status"] == "success"

        run = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "run", "execution": {}},
        )
        assert run["status"] == "success"

        status = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id, "action": "status", "query": {}},
        )
        assert status["result"]["stop_reason"] in {"watchpoint-trigger", "watchpoint-scope"}
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_catchpoint_v2(compile_program, start_session, stop_session, call_gdb_tool):
    program = compile_program(FORKING_C_PROGRAM, filename="forking.c", compiler="gcc")
    session_id = start_session(program)
    try:
        catch = call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id,
                "action": "create",
                "breakpoint": {"kind": "catch", "event": "fork"},
            },
        )
        assert catch["status"] == "success"

        run = call_gdb_tool(
            "gdb_execution_manage",
            {"session_id": session_id, "action": "run", "execution": {}},
        )
        assert run["status"] == "success"

        status = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id, "action": "status", "query": {}},
        )
        assert status["result"]["execution_state"] == "paused"
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_wait_for_stop_after_background_run_v2(compile_program, start_session, stop_session, call_gdb_tool):
    program = compile_program(DELAY_EXIT_C_PROGRAM, filename="delay.c", compiler="gcc")
    session_id = start_session(program)
    try:
        background = call_gdb_tool(
            "gdb_execution_manage",
            {
                "session_id": session_id,
                "action": "run",
                "execution": {"wait": {"until": "acknowledged"}},
            },
        )
        assert background["status"] == "success"

        waited = call_gdb_tool(
            "gdb_execution_manage",
            {
                "session_id": session_id,
                "action": "wait_for_stop",
                "execution": {"timeout_sec": 10},
            },
        )
        assert waited["status"] == "success"
        assert waited["result"]["matched"] is True
    finally:
        stop_session(session_id, ignore_errors=True)


@pytest.mark.integration
def test_inferior_and_fork_controls_v2(session_id, call_gdb_tool):
    create = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "create", "inferior": {"make_current": False}},
    )
    inferior_id = create["result"]["inferior_id"]

    listed = call_gdb_tool(
        "gdb_inferior_query",
        {"session_id": session_id, "action": "list", "query": {}},
    )
    assert listed["status"] == "success"
    assert listed["result"]["count"] >= 2

    follow = call_gdb_tool(
        "gdb_inferior_manage",
        {
            "session_id": session_id,
            "action": "set_follow_fork_mode",
            "inferior": {"mode": "child"},
        },
    )
    assert follow["status"] == "success"

    detach = call_gdb_tool(
        "gdb_inferior_manage",
        {
            "session_id": session_id,
            "action": "set_detach_on_fork",
            "inferior": {"enabled": False},
        },
    )
    assert detach["status"] == "success"

    remove = call_gdb_tool(
        "gdb_inferior_manage",
        {"session_id": session_id, "action": "remove", "inferior": {"inferior_id": inferior_id}},
    )
    assert remove["status"] == "success"
```

```bash
git rm tests/integration/test_gdb_integration.py
```

- [ ] **Step 4: Run the focused verification for the direct-v2 core integration slice**

Run:

```bash
uv run pytest -q tests/integration/test_v2_session_core.py
rg -n "_translate_legacy_call|_flatten_action_result|_translate_batch_steps|_install_module_tool_caller" tests/integration/conftest.py
```

Expected:
- `tests/integration/test_v2_session_core.py` passes
- the `rg` command returns no matches

- [ ] **Step 5: Commit**

```bash
git add tests/integration/program_sources.py tests/integration/conftest.py tests/integration/test_v2_session_core.py
git rm tests/integration/test_gdb_integration.py
git commit -m "test: replace integration shim with direct v2 core workflows"
```

### Task 2: Add Focused V2 Inspect, Startup, And Privileged Integration Coverage

**Files:**
- Create: `tests/integration/test_v2_session_inspect.py`
- Test/Verify: `uv run pytest -q tests/integration/test_v2_session_core.py tests/integration/test_v2_session_inspect.py`

**Testing approach:** `characterization/integration test`
Reason: This task preserves the single-session integration scenarios that still matter once routing and fine-grained semantics already live in `tests/mcp/` and `tests/session/`.

- [ ] **Step 1: Add a smaller inspect/startup integration file that uses only direct v2 payloads**

```python
# tests/integration/test_v2_session_inspect.py

"""Real-GDB integration coverage for direct-v2 inspect and startup workflows."""

from __future__ import annotations

import subprocess

import pytest

from .program_sources import (
    ATTACHABLE_C_PROGRAM,
    CRASHING_C_PROGRAM,
    TEST_CPP_PROGRAM,
)


@pytest.fixture
def compiled_program(compile_program):
    return compile_program(TEST_CPP_PROGRAM, filename="inspect_program.cpp", compiler="g++")


@pytest.fixture
def session_id(compiled_program, start_session):
    return start_session(compiled_program)


@pytest.fixture
def compiled_program_and_core(compile_program_with_core):
    return compile_program_with_core(CRASHING_C_PROGRAM, filename="inspect_crash.c", compiler="gcc")


@pytest.fixture
def attachable_program(compile_program):
    return compile_program(ATTACHABLE_C_PROGRAM, filename="attachable.c", compiler="gcc")


@pytest.mark.integration
def test_context_and_inspect_queries_v2(session_id, call_gdb_tool):
    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )
    call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run", "execution": {}})

    threads = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "threads", "query": {}},
    )
    assert threads["status"] == "success"
    assert threads["result"]["count"] >= 1

    backtrace = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "backtrace", "query": {"max_frames": 8}},
    )
    assert backtrace["status"] == "success"
    assert backtrace["result"]["count"] > 0

    frame = call_gdb_tool(
        "gdb_context_query",
        {"session_id": session_id, "action": "frame", "query": {}},
    )
    assert frame["status"] == "success"

    variables = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "variables", "query": {"context": {"frame": 0}}},
    )
    assert variables["status"] == "success"

    evaluated = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "evaluate", "query": {"expression": "a"}},
    )
    assert evaluated["status"] == "success"


@pytest.mark.integration
def test_source_and_disassembly_queries_v2(session_id, call_gdb_tool):
    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "add"},
        },
    )
    call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run", "execution": {}})

    disassembly = call_gdb_tool(
        "gdb_inspect_query",
        {
            "session_id": session_id,
            "action": "disassembly",
            "query": {
                "location": {"kind": "function", "function": "add"},
                "instruction_count": 16,
                "mode": "mixed",
            },
        },
    )
    assert disassembly["status"] == "success"
    assert disassembly["result"]["count"] > 0

    source = call_gdb_tool(
        "gdb_inspect_query",
        {
            "session_id": session_id,
            "action": "source",
            "query": {
                "location": {"kind": "function", "function": "add"},
                "context_before": 2,
                "context_after": 2,
            },
        },
    )
    assert source["status"] == "success"
    assert source["result"]["count"] > 0


@pytest.mark.integration
def test_memory_query_v2(session_id, call_gdb_tool):
    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run", "execution": {}})

    memory = call_gdb_tool(
        "gdb_inspect_query",
        {"session_id": session_id, "action": "memory", "query": {"address": "&main", "count": 8}},
    )
    assert memory["status"] == "success"
    assert memory["result"]["captured_bytes"] > 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload_builder", "expected_loaded"),
    [
        (lambda compiled, _: {"program": f"{compiled}.missing"}, False),
        (lambda compiled, _: {"program": compiled}, True),
        (lambda compiled, compiled_and_core: {"program": compiled_and_core[0], "core": compiled_and_core[1]}, True),
    ],
)
def test_startup_target_loaded_variants_v2(
    compiled_program,
    compiled_program_and_core,
    payload_builder,
    expected_loaded,
    call_gdb_tool,
    stop_session,
):
    payload = payload_builder(compiled_program, compiled_program_and_core)
    payload.setdefault("init_commands", ["set disable-randomization on"])

    result = call_gdb_tool("gdb_session_start", payload)
    assert result["status"] == "success"
    assert result["target_loaded"] is expected_loaded

    if "session_id" in result:
        stop_session(result["session_id"], ignore_errors=True)


@pytest.mark.integration
def test_execute_command_escape_hatch_v2(session_id, call_gdb_tool):
    result = call_gdb_tool(
        "gdb_execute_command",
        {"session_id": session_id, "command": "info functions", "timeout_sec": 30},
    )
    assert result["status"] == "success"
    assert "command" in result


@pytest.mark.integration
def test_get_status_reports_exited_state_after_continue_v2(session_id, call_gdb_tool):
    call_gdb_tool(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": "main"},
        },
    )
    call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "run", "execution": {}})
    call_gdb_tool("gdb_execution_manage", {"session_id": session_id, "action": "continue", "execution": {}})

    status = call_gdb_tool(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )
    assert status["status"] == "success"
    assert status["result"]["execution_state"] in {"paused", "exited"}


@pytest.mark.integration
def test_attach_process_v2(attachable_program, start_session_result, stop_session, call_gdb_tool):
    process = subprocess.Popen([attachable_program])
    session_id: int | None = None
    try:
        start = start_session_result(attachable_program)
        session_id = start["session_id"]

        attach = call_gdb_tool(
            "gdb_attach_process",
            {"session_id": session_id, "pid": process.pid, "timeout_sec": 30},
        )
        assert attach["status"] == "success"

        status = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id, "action": "status", "query": {}},
        )
        assert status["status"] == "success"
        assert status["result"]["execution_state"] == "paused"
    finally:
        if session_id is not None:
            stop_session(session_id, ignore_errors=True)
        process.terminate()
        process.wait(timeout=10)
```

- [ ] **Step 2: Run focused verification for the single-session v2 integration files and assert that no legacy tool names remain**

Run:

```bash
uv run pytest -q tests/integration/test_v2_session_core.py tests/integration/test_v2_session_inspect.py
rg -n "gdb_start_session|gdb_get_status|gdb_list_sessions|gdb_stop_session|gdb_batch|gdb_set_breakpoint|gdb_get_backtrace" tests/integration/test_v2_session_core.py tests/integration/test_v2_session_inspect.py
```

Expected:
- both integration files pass
- the `rg` command returns no matches

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_v2_session_inspect.py
git commit -m "test: add focused v2 inspect integration coverage"
```

### Task 3: Replace The Multi-Session Integration Suite With A Smaller Direct-V2 File

**Files:**
- Create: `tests/integration/test_v2_multi_session.py`
- Delete: `tests/integration/test_multi_session.py`
- Test/Verify: `uv run pytest -q tests/integration/test_v2_multi_session.py`

**Testing approach:** `characterization/integration test`
Reason: The goal is to preserve only the multi-session scenarios that prove cross-session isolation against real GDB while deleting overlapping permutations.

- [ ] **Step 1: Add `tests/integration/test_v2_multi_session.py` with representative direct-v2 isolation flows**

```python
# tests/integration/test_v2_multi_session.py

"""Real-GDB integration coverage for representative direct-v2 multi-session workflows."""

from __future__ import annotations

import pytest

from .program_sources import TEST_PROGRAM_1, TEST_PROGRAM_2


@pytest.fixture
def compiled_program_1(compile_program):
    return compile_program(TEST_PROGRAM_1, filename="program1.c", compiler="gcc")


@pytest.fixture
def compiled_program_2(compile_program):
    return compile_program(TEST_PROGRAM_2, filename="program2.c", compiler="gcc")


@pytest.mark.integration
def test_session_inventory_and_status_are_isolated_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        sessions = call_gdb_tool("gdb_session_query", {"action": "list", "query": {}})
        assert sessions["status"] == "success"
        assert sessions["action"] == "list"
        listed_ids = {item["session_id"] for item in sessions["result"]["sessions"]}
        assert listed_ids == {session_id_1, session_id_2}

        status_1 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_1, "action": "status", "query": {}},
        )
        status_2 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_2, "action": "status", "query": {}},
        )
        assert status_1["result"]["is_running"] is True
        assert status_2["result"]["is_running"] is True
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_breakpoints_are_isolated_between_sessions_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        call_gdb_tool(
            "gdb_breakpoint_manage",
            {
                "session_id": session_id_1,
                "action": "create",
                "breakpoint": {"kind": "code", "location": "main"},
            },
        )

        listed_1 = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id_1, "action": "list", "query": {}},
        )
        listed_2 = call_gdb_tool(
            "gdb_breakpoint_query",
            {"session_id": session_id_2, "action": "list", "query": {}},
        )
        assert listed_1["result"]["count"] == 1
        assert listed_2["result"]["count"] == 0
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_execution_state_and_backtrace_are_isolated_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        for session_id in (session_id_1, session_id_2):
            call_gdb_tool(
                "gdb_breakpoint_manage",
                {
                    "session_id": session_id,
                    "action": "create",
                    "breakpoint": {"kind": "code", "location": "main"},
                },
            )
            call_gdb_tool(
                "gdb_execution_manage",
                {"session_id": session_id, "action": "run", "execution": {}},
            )

        backtrace_1 = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id_1, "action": "backtrace", "query": {}},
        )
        backtrace_2 = call_gdb_tool(
            "gdb_context_query",
            {"session_id": session_id_2, "action": "backtrace", "query": {}},
        )
        assert backtrace_1["result"]["count"] > 0
        assert backtrace_2["result"]["count"] > 0
    finally:
        stop_session(session_id_1, ignore_errors=True)
        stop_session(session_id_2, ignore_errors=True)


@pytest.mark.integration
def test_stop_one_session_does_not_affect_other_v2(
    compiled_program_1,
    compiled_program_2,
    start_session,
    stop_session,
    call_gdb_tool,
):
    session_id_1 = start_session(compiled_program_1)
    session_id_2 = start_session(compiled_program_2)
    try:
        stop = call_gdb_tool(
            "gdb_session_manage",
            {"session_id": session_id_2, "action": "stop", "session": {}},
        )
        assert stop["status"] == "success"

        status_1 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_1, "action": "status", "query": {}},
        )
        assert status_1["status"] == "success"

        status_2 = call_gdb_tool(
            "gdb_session_query",
            {"session_id": session_id_2, "action": "status", "query": {}},
        )
        assert status_2["status"] == "error"
    finally:
        stop_session(session_id_1, ignore_errors=True)


@pytest.mark.integration
def test_invalid_session_id_returns_error_v2(call_gdb_tool):
    result = call_gdb_tool(
        "gdb_session_query",
        {"session_id": 999, "action": "status", "query": {}},
    )
    assert result["status"] == "error"
    assert "Invalid session_id: 999" in result["message"]
    assert "gdb_session_start" in result["message"]
```

- [ ] **Step 2: Remove the legacy multi-session file**

```bash
git rm tests/integration/test_multi_session.py
```

- [ ] **Step 3: Run focused verification for multi-session coverage and legacy-name drift**

Run:

```bash
uv run pytest -q tests/integration/test_v2_multi_session.py
rg -n "gdb_start_session|gdb_get_status|gdb_list_sessions|gdb_stop_session|gdb_set_breakpoint|gdb_get_backtrace" tests/integration/test_v2_multi_session.py
```

Expected:
- `tests/integration/test_v2_multi_session.py` passes
- the `rg` command returns no matches

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_v2_multi_session.py
git rm tests/integration/test_multi_session.py
git commit -m "test: rewrite multi-session integration coverage for v2"
```

### Task 4: Trim Low-Signal Lower-Layer Tests And Update Test Docs

**Files:**
- Modify: `tests/domain/test_results.py`
- Modify: `tests/README.md`
- Test/Verify: `uv run pytest -q tests/domain/test_results.py tests/mcp tests/session tests/integration`

**Testing approach:** `existing tests + targeted verification`
Reason: This task removes only obviously redundant lower-layer coverage, documents the new rules, and verifies the final suite shape end to end.

- [ ] **Step 1: Collapse `tests/domain/test_results.py` to a smaller set of core result-mapping tests**

```python
# tests/domain/test_results.py

"""Unit tests for typed domain result helpers."""

from gdb_mcp.domain import OperationError, OperationSuccess, SessionMessage, result_to_mapping


class TestDomainResults:
    """Test conversion helpers for typed internal results."""

    def test_result_to_mapping_wraps_scalar_success_payload(self):
        payload = result_to_mapping(OperationSuccess(42))
        assert payload == {"status": "success", "value": 42}

    def test_result_to_mapping_adds_warnings_for_dataclass_payloads(self):
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
```

- [ ] **Step 2: Update `tests/README.md` to document the v2-only ownership model**

```markdown
# GDB MCP Server Tests

## Test Structure

- `domain/` - Narrow result-mapping tests that are not better owned by the MCP serializer layer
- `mcp/` - Public MCP tool inventory, schemas, routing, envelopes, and runtime dispatch
- `session/` - Session service semantics and transport-normalized behavior
- `transport/` - Low-level GDB/MI transport and parser behavior
- `integration/` - Representative direct-v2 real-GDB workflows

## Ownership Rules

1. Prefer the true ownership boundary.
2. `tests/mcp/` owns request/response shape and action routing.
3. `tests/session/` owns debugger semantics and error details.
4. `tests/integration/` proves only representative cross-layer workflows.
5. Integration tests must not use legacy tool names or response-flattening helpers.

## Running Integration Coverage

```bash
uv run pytest -q tests/integration
```
```

- [ ] **Step 3: Run the final repo verification**

Run:

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
git diff --check
rg -n "gdb_start_session|gdb_get_status|gdb_list_sessions|gdb_stop_session|gdb_batch|gdb_set_breakpoint|gdb_get_backtrace" tests/integration -g '*.py'
```

Expected:
- `ruff` passes
- `mypy` passes
- full `pytest` passes
- `git diff --check` is clean
- the integration `rg` command returns no matches

- [ ] **Step 4: Commit**

```bash
git add tests/domain/test_results.py tests/README.md
git commit -m "test: trim redundant v2 test coverage and document ownership"
```
