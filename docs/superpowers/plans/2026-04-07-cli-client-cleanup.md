# CLI Client Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. If the user has expressed a strong preference for one of these execution styles, keep using that style unless they explicitly ask to switch. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For Codex subagent-driven execution:** Subagents cannot stream partial progress back to the controller while still running. The controller should assign each subagent a unique shared progress file and inspect that file during execution when visibility is needed.

**Goal:** Refactor `gdb-mcp-client` to use typed internal command inputs and pure payload builders instead of dynamic `argparse.Namespace` inspection, while preserving the public MCP interface and setting a reusable typed-boundary pattern for adjacent code.

**Architecture:** Introduce a typed internal layer in `src/gdb_mcp/client/inputs.py` and `src/gdb_mcp/client/input_parsers.py`, move payload construction into family-specific modules under `src/gdb_mcp/client/builders/`, and reduce `src/gdb_mcp/client/specs.py` to registry/orchestration only. After the client is migrated, apply the same pattern to action-union dispatch in `src/gdb_mcp/mcp/handlers.py` so the refactor sets a broader internal precedent.

**Tech Stack:** Python 3.10+, `argparse`, dataclasses, Pydantic v2 schema validation, pytest, ruff, mypy.

---

### Task 1: Establish Typed Client Inputs And The Reference Session Family

**Files:**
- Create: `src/gdb_mcp/client/inputs.py`
- Create: `src/gdb_mcp/client/input_parsers.py`
- Create: `src/gdb_mcp/client/builders/__init__.py`
- Create: `src/gdb_mcp/client/builders/session.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `src/gdb_mcp/client/cli.py`
- Create: `tests/mcp/test_client_builders.py`
- Modify: `tests/mcp/test_client_cli.py`

**Testing approach:** `TDD`
Reason: This task introduces the new internal seam (`Namespace -> TypedInput -> payload`) with a smaller, well-understood family. A failing test first is the cleanest way to lock down the pattern before the broader migration.

- [ ] **Step 1: Write failing tests for typed session inputs and session payload builders**

```python
# tests/mcp/test_client_builders.py

from __future__ import annotations

import argparse

from gdb_mcp.client.builders.session import (
    build_session_query_payload,
    build_session_start_payload,
)
from gdb_mcp.client.input_parsers import (
    parse_session_query_input,
    parse_session_start_input,
)
from gdb_mcp.client.inputs import SessionQueryInput, SessionStartInput


def test_parse_session_start_input_from_namespace() -> None:
    namespace = argparse.Namespace(
        program="/bin/true",
        args=["--mode", "fast"],
        init_commands=["set pagination off"],
        env=[("TERM", "dumb")],
        core=None,
        gdb_path=None,
        working_dir=None,
    )

    assert parse_session_start_input(namespace) == SessionStartInput(
        program="/bin/true",
        args=["--mode", "fast"],
        init_commands=["set pagination off"],
        env={"TERM": "dumb"},
        core=None,
        gdb_path=None,
        working_dir=None,
    )


def test_build_session_start_payload_from_typed_input() -> None:
    typed_input = SessionStartInput(
        program="/bin/true",
        args=["--mode", "fast"],
        init_commands=["set pagination off"],
        env={"TERM": "dumb"},
        core=None,
        gdb_path=None,
        working_dir=None,
    )

    assert build_session_start_payload(typed_input) == {
        "program": "/bin/true",
        "args": ["--mode", "fast"],
        "init_commands": ["set pagination off"],
        "env": {"TERM": "dumb"},
    }


def test_parse_session_query_input_from_namespace() -> None:
    namespace = argparse.Namespace(action="status", session_id=7)

    assert parse_session_query_input(namespace) == SessionQueryInput(
        action="status",
        session_id=7,
    )


def test_build_session_query_payload_from_typed_input() -> None:
    typed_input = SessionQueryInput(action="status", session_id=7)

    assert build_session_query_payload(typed_input) == {
        "action": "status",
        "session_id": 7,
    }
```

```python
# tests/mcp/test_client_cli.py

from gdb_mcp.client.cli import build_parser


def test_build_parser_still_exposes_session_subcommands() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "gdb_session_start" in help_text
    assert "gdb_session_query" in help_text
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'session' tests/mcp/test_client_cli.py -k 'session'`
Expected: FAIL because the typed input and builder modules do not exist yet.

- [ ] **Step 3: Implement the typed input boundary and migrate the session family**

```python
# src/gdb_mcp/client/inputs.py

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionStartInput:
    program: str | None
    args: list[str]
    init_commands: list[str]
    env: dict[str, str] | None
    core: str | None
    gdb_path: str | None
    working_dir: str | None


@dataclass(frozen=True)
class SessionQueryInput:
    action: str
    session_id: int | None = None
```

```python
# src/gdb_mcp/client/input_parsers.py

from __future__ import annotations

import argparse

from .inputs import SessionQueryInput, SessionStartInput
from .parsers import collapse_key_value_entries


def parse_session_start_input(namespace: argparse.Namespace) -> SessionStartInput:
    return SessionStartInput(
        program=namespace.program,
        args=list(namespace.args),
        init_commands=list(namespace.init_commands),
        env=collapse_key_value_entries(namespace.env),
        core=namespace.core,
        gdb_path=namespace.gdb_path,
        working_dir=namespace.working_dir,
    )


def parse_session_query_input(namespace: argparse.Namespace) -> SessionQueryInput:
    return SessionQueryInput(
        action=namespace.action,
        session_id=getattr(namespace, "session_id", None),
    )
```

```python
# src/gdb_mcp/client/builders/session.py

from __future__ import annotations

from gdb_mcp.client.inputs import SessionQueryInput, SessionStartInput


def build_session_start_payload(typed_input: SessionStartInput) -> dict[str, object]:
    payload: dict[str, object] = {}
    if typed_input.program is not None:
        payload["program"] = typed_input.program
    if typed_input.args:
        payload["args"] = typed_input.args
    if typed_input.init_commands:
        payload["init_commands"] = typed_input.init_commands
    if typed_input.env:
        payload["env"] = typed_input.env
    if typed_input.core is not None:
        payload["core"] = typed_input.core
    if typed_input.gdb_path is not None:
        payload["gdb_path"] = typed_input.gdb_path
    if typed_input.working_dir is not None:
        payload["working_dir"] = typed_input.working_dir
    return payload


def build_session_query_payload(typed_input: SessionQueryInput) -> dict[str, object]:
    payload: dict[str, object] = {"action": typed_input.action}
    if typed_input.session_id is not None:
        payload["session_id"] = typed_input.session_id
    return payload
```

```python
# src/gdb_mcp/client/specs.py

@dataclass(frozen=True)
class ToolCliSpec:
    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    parse_input: Callable[[argparse.Namespace], object]
    build_arguments: Callable[[object], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]
```

```python
# src/gdb_mcp/client/cli.py

typed_input = spec.parse_input(args)
payload = spec.build_arguments(typed_input)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'session' tests/mcp/test_client_cli.py -k 'session'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/inputs.py src/gdb_mcp/client/input_parsers.py src/gdb_mcp/client/builders/__init__.py src/gdb_mcp/client/builders/session.py src/gdb_mcp/client/specs.py src/gdb_mcp/client/cli.py tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py
git commit -m "refactor: add typed client input scaffolding"
```

### Task 2: Migrate Inferior, Execution, And Context Families To Typed Builders

**Files:**
- Create: `src/gdb_mcp/client/builders/inferior.py`
- Create: `src/gdb_mcp/client/builders/execution.py`
- Create: `src/gdb_mcp/client/builders/context.py`
- Modify: `src/gdb_mcp/client/inputs.py`
- Modify: `src/gdb_mcp/client/input_parsers.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `tests/mcp/test_client_builders.py`
- Modify: `tests/mcp/test_client_cli.py`

**Testing approach:** `TDD`
Reason: These tool families are still action-based, but their branching is simpler than breakpoint/inspect/workflow. Migrating them next validates that the new pattern scales beyond the session reference family.

- [ ] **Step 1: Write failing tests for typed inferior/execution/context builders**

```python
# tests/mcp/test_client_builders.py

from gdb_mcp.client.builders.context import build_context_query_payload
from gdb_mcp.client.builders.execution import build_execution_manage_payload
from gdb_mcp.client.builders.inferior import build_inferior_manage_payload
from gdb_mcp.client.inputs import (
    ContextQueryInput,
    ExecutionManageInput,
    ExecutionWaitInput,
    InferiorManageInput,
)


def test_build_inferior_manage_create_payload() -> None:
    typed_input = InferiorManageInput(
        action="create",
        session_id=7,
        executable="/bin/true",
        make_current=True,
        inferior_id=None,
        mode=None,
        enabled=None,
    )

    assert build_inferior_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "create",
        "inferior": {
            "executable": "/bin/true",
            "make_current": True,
        },
    }


def test_build_execution_manage_run_payload() -> None:
    typed_input = ExecutionManageInput(
        action="run",
        session_id=7,
        args=["--mode", "fast"],
        wait=ExecutionWaitInput(until="stop", timeout_sec=30),
        timeout_sec=None,
        stop_reasons=[],
    )

    assert build_execution_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "run",
        "execution": {
            "args": ["--mode", "fast"],
            "wait": {"until": "stop", "timeout_sec": 30},
        },
    }


def test_build_context_query_backtrace_payload() -> None:
    typed_input = ContextQueryInput(
        action="backtrace",
        session_id=7,
        thread_id=3,
        frame=None,
        max_frames=20,
    )

    assert build_context_query_payload(typed_input) == {
        "session_id": 7,
        "action": "backtrace",
        "query": {"thread_id": 3, "max_frames": 20},
    }
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'inferior or execution or context' tests/mcp/test_client_cli.py -k 'inferior or execution or context'`
Expected: FAIL because the typed core-family inputs and builders do not exist yet.

- [ ] **Step 3: Implement typed inputs and family-specific builders for inferior, execution, and context**

```python
# src/gdb_mcp/client/inputs.py

@dataclass(frozen=True)
class ExecutionWaitInput:
    until: str | None
    timeout_sec: int | None


@dataclass(frozen=True)
class InferiorManageInput:
    action: str
    session_id: int
    executable: str | None
    make_current: bool | None
    inferior_id: int | None
    mode: str | None
    enabled: bool | None


@dataclass(frozen=True)
class ExecutionManageInput:
    action: str
    session_id: int
    args: list[str]
    wait: ExecutionWaitInput | None
    timeout_sec: int | None
    stop_reasons: list[str]


@dataclass(frozen=True)
class ContextQueryInput:
    action: str
    session_id: int
    thread_id: int | None
    frame: int | None
    max_frames: int | None
```

```python
# src/gdb_mcp/client/builders/execution.py

from __future__ import annotations

from gdb_mcp.client.inputs import ExecutionManageInput


def build_execution_manage_payload(typed_input: ExecutionManageInput) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "action": typed_input.action,
    }
    if typed_input.action in {"run", "continue", "step", "next", "finish"}:
        execution: dict[str, object] = {}
        if typed_input.args:
            execution["args"] = typed_input.args
        if typed_input.wait is not None:
            wait: dict[str, object] = {}
            if typed_input.wait.until is not None:
                wait["until"] = typed_input.wait.until
            if typed_input.wait.timeout_sec is not None:
                wait["timeout_sec"] = typed_input.wait.timeout_sec
            if wait:
                execution["wait"] = wait
        if execution:
            payload["execution"] = execution
    if typed_input.action == "wait_for_stop":
        payload["execution"] = {
            "timeout_sec": typed_input.timeout_sec,
            "stop_reasons": typed_input.stop_reasons,
        }
    return payload
```

```python
# src/gdb_mcp/client/specs.py

from .builders.context import build_context_manage_payload, build_context_query_payload
from .builders.execution import build_execution_manage_payload
from .builders.inferior import build_inferior_manage_payload, build_inferior_query_payload
from .input_parsers import (
    parse_context_manage_input,
    parse_context_query_input,
    parse_execution_manage_input,
    parse_inferior_manage_input,
    parse_inferior_query_input,
)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'inferior or execution or context' tests/mcp/test_client_cli.py -k 'inferior or execution or context'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/builders/inferior.py src/gdb_mcp/client/builders/execution.py src/gdb_mcp/client/builders/context.py src/gdb_mcp/client/inputs.py src/gdb_mcp/client/input_parsers.py src/gdb_mcp/client/specs.py tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py
git commit -m "refactor: migrate core client action builders"
```

### Task 3: Migrate Breakpoint And Inspect Families To Typed Inputs

**Files:**
- Create: `src/gdb_mcp/client/builders/breakpoint.py`
- Create: `src/gdb_mcp/client/builders/inspect.py`
- Modify: `src/gdb_mcp/client/inputs.py`
- Modify: `src/gdb_mcp/client/input_parsers.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `tests/mcp/test_client_builders.py`
- Modify: `tests/mcp/test_client_cli.py`

**Testing approach:** `TDD`
Reason: Breakpoint and inspect logic contain many of the dynamic-field branches that motivated the cleanup. Refactoring them with failing builder tests first will keep the typed migration honest and localized.

- [ ] **Step 1: Write failing tests for typed breakpoint and inspect payload builders**

```python
# tests/mcp/test_client_builders.py

from gdb_mcp.client.builders.breakpoint import (
    build_breakpoint_manage_payload,
    build_breakpoint_query_payload,
)
from gdb_mcp.client.builders.inspect import build_inspect_query_payload
from gdb_mcp.client.inputs import (
    BreakpointCreateInput,
    BreakpointManageInput,
    BreakpointQueryInput,
    InspectQueryInput,
    LocationInput,
)


def test_build_breakpoint_query_filtered_list_payload() -> None:
    typed_input = BreakpointQueryInput(
        action="list",
        session_id=7,
        number=None,
        kinds=["code", "watch"],
        enabled=False,
    )

    assert build_breakpoint_query_payload(typed_input) == {
        "session_id": 7,
        "action": "list",
        "query": {"kinds": ["code", "watch"], "enabled": False},
    }


def test_build_breakpoint_manage_create_payload() -> None:
    typed_input = BreakpointManageInput(
        action="create",
        session_id=7,
        breakpoint=BreakpointCreateInput(
            kind="code",
            location="main",
            expression=None,
            access=None,
            event=None,
            argument=None,
            condition=None,
            temporary=True,
        ),
        selector_number=None,
        condition=None,
        clear_condition=None,
    )

    assert build_breakpoint_manage_payload(typed_input) == {
        "session_id": 7,
        "action": "create",
        "breakpoint": {"kind": "code", "location": "main", "temporary": True},
    }


def test_build_inspect_source_payload() -> None:
    typed_input = InspectQueryInput(
        action="source",
        session_id=7,
        thread_id=None,
        frame=None,
        expression=None,
        register_numbers=[],
        register_names=[],
        include_vector_registers=None,
        max_registers=None,
        value_format=None,
        address=None,
        count=None,
        offset=None,
        location=LocationInput(kind="file_line", file="src/main.c", line=42),
        instruction_count=None,
        mode=None,
        context_before=2,
        context_after=3,
    )

    assert build_inspect_query_payload(typed_input) == {
        "session_id": 7,
        "action": "source",
        "query": {
            "location": {"kind": "file_line", "file": "src/main.c", "line": 42},
            "context_before": 2,
            "context_after": 3,
        },
    }
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'breakpoint or inspect' tests/mcp/test_client_cli.py -k 'breakpoint or inspect'`
Expected: FAIL because the typed complex-family inputs and builders do not exist yet.

- [ ] **Step 3: Implement typed breakpoint and inspect models and builders**

```python
# src/gdb_mcp/client/inputs.py

@dataclass(frozen=True)
class LocationInput:
    kind: str
    function: str | None = None
    address: str | None = None
    start_address: str | None = None
    end_address: str | None = None
    file: str | None = None
    line: int | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True)
class BreakpointCreateInput:
    kind: str
    location: str | None
    expression: str | None
    access: str | None
    event: str | None
    argument: str | None
    condition: str | None
    temporary: bool
```

```python
# src/gdb_mcp/client/builders/inspect.py

from __future__ import annotations

from gdb_mcp.client.inputs import InspectQueryInput, LocationInput


def _build_location_payload(location: LocationInput) -> dict[str, object]:
    if location.kind == "file_line":
        return {"kind": "file_line", "file": location.file, "line": location.line}
    if location.kind == "current":
        return {"kind": "current"}
    if location.kind == "function":
        return {"kind": "function", "function": location.function}
    if location.kind == "address":
        return {"kind": "address", "address": location.address}
    if location.kind == "address_range":
        return {
            "kind": "address_range",
            "start_address": location.start_address,
            "end_address": location.end_address,
        }
    return {
        "kind": "file_range",
        "file": location.file,
        "start_line": location.start_line,
        "end_line": location.end_line,
    }
```

```python
# src/gdb_mcp/client/specs.py

from .builders.breakpoint import build_breakpoint_manage_payload, build_breakpoint_query_payload
from .builders.inspect import build_inspect_query_payload
from .input_parsers import (
    parse_breakpoint_manage_input,
    parse_breakpoint_query_input,
    parse_inspect_query_input,
)
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'breakpoint or inspect' tests/mcp/test_client_cli.py -k 'breakpoint or inspect'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/builders/breakpoint.py src/gdb_mcp/client/builders/inspect.py src/gdb_mcp/client/inputs.py src/gdb_mcp/client/input_parsers.py src/gdb_mcp/client/specs.py tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py
git commit -m "refactor: migrate complex client action builders"
```

### Task 4: Type Workflow And Campaign Inputs End-To-End

**Files:**
- Create: `src/gdb_mcp/client/builders/workflow.py`
- Modify: `src/gdb_mcp/client/inputs.py`
- Modify: `src/gdb_mcp/client/input_parsers.py`
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `src/gdb_mcp/client/parsers.py`
- Modify: `tests/mcp/test_client_builders.py`
- Modify: `tests/mcp/test_client_cli.py`
- Modify: `tests/integration/test_client_streamable_http.py`

**Testing approach:** `TDD`
Reason: Workflow and campaign are the highest-complexity builders and the biggest beneficiaries of typed intermediate objects. They also have the clearest regression seams: grouped step parsing, list coercion, setup-step behavior, and end-to-end HTTP integration.

- [ ] **Step 1: Write failing tests for typed workflow/setup-step parsing and payload building**

```python
# tests/mcp/test_client_builders.py

from gdb_mcp.client.builders.workflow import (
    build_run_until_failure_payload,
    build_workflow_batch_payload,
)
from gdb_mcp.client.inputs import (
    RunUntilFailureInput,
    SessionStepInput,
    WorkflowBatchInput,
)


def test_build_workflow_batch_payload_from_typed_steps() -> None:
    typed_input = WorkflowBatchInput(
        session_id=7,
        steps=[
            SessionStepInput(
                tool="gdb_context_query",
                label="stack",
                arguments={"action": "backtrace", "query": {"max_frames": 20}},
            )
        ],
        fail_fast=False,
        capture_stop_events=True,
    )

    assert build_workflow_batch_payload(typed_input) == {
        "session_id": 7,
        "steps": [
            {
                "tool": "gdb_context_query",
                "label": "stack",
                "arguments": {"action": "backtrace", "query": {"max_frames": 20}},
            }
        ],
        "fail_fast": False,
        "capture_stop_events": True,
    }


def test_build_run_until_failure_payload_from_typed_input() -> None:
    typed_input = RunUntilFailureInput(
        startup_program="/bin/true",
        startup_args=[],
        startup_init_commands=["set pagination off"],
        startup_env=None,
        startup_gdb_path=None,
        startup_working_dir=None,
        startup_core=None,
        setup_steps=[],
        run_args=[],
        run_timeout_sec=15,
        max_iterations=2,
        failure_on_error=True,
        failure_on_timeout=True,
        failure_stop_reasons=["signal-received"],
        failure_execution_states=[],
        failure_exit_codes=[],
        failure_result_text_regex=None,
        capture_enabled=True,
        capture_output_dir=None,
        capture_bundle_name_prefix=None,
        capture_bundle_name=None,
        capture_expressions=["errno"],
        capture_memory_ranges=["&errno:8"],
        capture_max_frames=100,
        capture_include_threads=True,
        capture_include_backtraces=True,
        capture_include_frame=True,
        capture_include_variables=True,
        capture_include_registers=True,
        capture_include_transcript=True,
        capture_include_stop_history=True,
    )

    assert build_run_until_failure_payload(typed_input) == {
        "startup": {
            "program": "/bin/true",
            "init_commands": ["set pagination off"],
        },
        "setup_steps": [],
        "run_timeout_sec": 15,
        "max_iterations": 2,
        "failure": {
            "failure_on_error": True,
            "failure_on_timeout": True,
            "stop_reasons": ["signal-received"],
            "execution_states": [],
            "exit_codes": [],
        },
        "capture": {
            "enabled": True,
            "expressions": ["errno"],
            "memory_ranges": ["&errno:8"],
            "max_frames": 100,
            "include_threads": True,
            "include_backtraces": True,
            "include_frame": True,
            "include_variables": True,
            "include_registers": True,
            "include_transcript": True,
            "include_stop_history": True,
        },
    }
```

```python
# tests/mcp/test_client_cli.py

def test_main_builds_workflow_batch_single_list_step_argument(self, mock_invoke_tool):
    mock_invoke_tool.return_value = ClientToolResponse(
        payload={"status": "success", "count": 1, "error_count": 0},
        is_error=False,
    )

    exit_code = asyncio.run(
        main(
            [
                "--server-url",
                "http://127.0.0.1:8000/mcp",
                "gdb_workflow_batch",
                "--session-id",
                "7",
                "--step",
                "gdb_breakpoint_query",
                "--step-arg",
                "action=list",
                "--step-arg",
                "query.kinds=code",
            ]
        )
    )

    assert exit_code == 0
    mock_invoke_tool.assert_awaited_once_with(
        "http://127.0.0.1:8000/mcp",
        "gdb_workflow_batch",
        {
            "session_id": 7,
            "steps": [
                {
                    "tool": "gdb_breakpoint_query",
                    "arguments": {
                        "action": "list",
                        "query": {"kinds": ["code"]},
                    },
                }
            ],
            "fail_fast": True,
            "capture_stop_events": True,
        },
        http_client=None,
    )


def test_main_builds_run_until_failure_setup_step_single_list_argument(self, mock_invoke_tool):
    mock_invoke_tool.return_value = ClientToolResponse(
        payload={"status": "success", "matched_failure": False, "iterations_completed": 1},
        is_error=False,
    )

    exit_code = asyncio.run(
        main(
            [
                "--server-url",
                "http://127.0.0.1:8000/mcp",
                "gdb_run_until_failure",
                "--setup-step",
                "gdb_capture_bundle",
                "--setup-step-arg",
                "expressions=errno",
            ]
        )
    )

    assert exit_code == 0
    mock_invoke_tool.assert_awaited_once_with(
        "http://127.0.0.1:8000/mcp",
        "gdb_run_until_failure",
        {
            "startup": {},
            "setup_steps": [
                {
                    "tool": "gdb_capture_bundle",
                    "arguments": {
                        "expressions": ["errno"],
                        "memory_ranges": [],
                        "max_frames": 100,
                        "include_threads": True,
                        "include_backtraces": True,
                        "include_frame": True,
                        "include_variables": True,
                        "include_registers": True,
                        "include_transcript": True,
                        "include_stop_history": True,
                    },
                }
            ],
            "run_timeout_sec": 30,
            "max_iterations": 1,
            "failure": {
                "failure_on_error": True,
                "failure_on_timeout": True,
                "stop_reasons": ["signal-received", "exited-signalled"],
                "execution_states": [],
                "exit_codes": [],
            },
            "capture": {
                "enabled": True,
                "expressions": [],
                "memory_ranges": [],
                "max_frames": 100,
                "include_threads": True,
                "include_backtraces": True,
                "include_frame": True,
                "include_variables": True,
                "include_registers": True,
                "include_transcript": True,
                "include_stop_history": True,
            },
        },
        http_client=None,
    )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'workflow or failure' tests/mcp/test_client_cli.py -k 'workflow or run_until_failure' tests/integration/test_client_streamable_http.py`
Expected: FAIL because the workflow/campaign typed layer does not exist yet.

- [ ] **Step 3: Implement typed step models, typed workflow parsers, and pure workflow/campaign builders**

```python
# src/gdb_mcp/client/inputs.py

@dataclass(frozen=True)
class SessionStepInput:
    tool: str
    label: str | None
    arguments: dict[str, object]


@dataclass(frozen=True)
class WorkflowBatchInput:
    session_id: int
    steps: list[SessionStepInput]
    fail_fast: bool | None
    capture_stop_events: bool | None


@dataclass(frozen=True)
class RunUntilFailureInput:
    startup_program: str | None
    startup_args: list[str]
    startup_init_commands: list[str]
    startup_env: dict[str, str] | None
    startup_gdb_path: str | None
    startup_working_dir: str | None
    startup_core: str | None
    setup_steps: list[SessionStepInput]
    run_args: list[str]
    run_timeout_sec: int | None
    max_iterations: int | None
    failure_on_error: bool | None
    failure_on_timeout: bool | None
    failure_stop_reasons: list[str]
    failure_execution_states: list[str]
    failure_exit_codes: list[int]
    failure_result_text_regex: str | None
    capture_enabled: bool | None
    capture_output_dir: str | None
    capture_bundle_name_prefix: str | None
    capture_bundle_name: str | None
    capture_expressions: list[str]
    capture_memory_ranges: list[str]
    capture_max_frames: int | None
    capture_include_threads: bool | None
    capture_include_backtraces: bool | None
    capture_include_frame: bool | None
    capture_include_variables: bool | None
    capture_include_registers: bool | None
    capture_include_transcript: bool | None
    capture_include_stop_history: bool | None
```

```python
# src/gdb_mcp/client/builders/workflow.py

from __future__ import annotations

from gdb_mcp.client.inputs import RunUntilFailureInput, SessionStepInput, WorkflowBatchInput


def _build_step_payload(step: SessionStepInput) -> dict[str, object]:
    payload: dict[str, object] = {
        "tool": step.tool,
        "arguments": step.arguments,
    }
    if step.label is not None:
        payload["label"] = step.label
    return payload
```

```python
# src/gdb_mcp/client/input_parsers.py

def parse_step_inputs(step_events: list[tuple[str, object]] | None) -> list[SessionStepInput]:
    typed_steps: list[SessionStepInput] = []
    raw_steps = _build_step_list(step_events)
    for raw_step in raw_steps:
        typed_steps.append(
            SessionStepInput(
                tool=cast(str, raw_step["tool"]),
                label=cast(str | None, raw_step.get("label")),
                arguments=cast(dict[str, object], raw_step["arguments"]),
            )
        )
    return typed_steps


def parse_workflow_batch_input(namespace: argparse.Namespace) -> WorkflowBatchInput:
    return WorkflowBatchInput(
        session_id=namespace.session_id,
        steps=parse_step_inputs(namespace.step_events),
        fail_fast=namespace.fail_fast,
        capture_stop_events=namespace.capture_stop_events,
    )


def parse_run_until_failure_input(namespace: argparse.Namespace) -> RunUntilFailureInput:
    return RunUntilFailureInput(
        startup_program=getattr(namespace, "startup_program", None),
        startup_args=list(namespace.startup_args),
        startup_init_commands=list(namespace.startup_init_commands),
        startup_env=collapse_key_value_entries(namespace.startup_env),
        startup_gdb_path=getattr(namespace, "startup_gdb_path", None),
        startup_working_dir=getattr(namespace, "startup_working_dir", None),
        startup_core=getattr(namespace, "startup_core", None),
        setup_steps=parse_step_inputs(getattr(namespace, "setup_step_events", None)),
        run_args=list(namespace.run_args),
        run_timeout_sec=getattr(namespace, "run_timeout_sec", None),
        max_iterations=getattr(namespace, "max_iterations", None),
        failure_on_error=getattr(namespace, "failure_on_error", None),
        failure_on_timeout=getattr(namespace, "failure_on_timeout", None),
        failure_stop_reasons=list(getattr(namespace, "failure_stop_reasons", [])),
        failure_execution_states=list(getattr(namespace, "failure_execution_states", [])),
        failure_exit_codes=list(getattr(namespace, "failure_exit_codes", [])),
        failure_result_text_regex=getattr(namespace, "failure_result_text_regex", None),
        capture_enabled=getattr(namespace, "capture_enabled", None),
        capture_output_dir=getattr(namespace, "capture_output_dir", None),
        capture_bundle_name_prefix=getattr(namespace, "capture_bundle_name_prefix", None),
        capture_bundle_name=getattr(namespace, "capture_bundle_name", None),
        capture_expressions=list(namespace.capture_expressions),
        capture_memory_ranges=list(namespace.capture_memory_ranges),
        capture_max_frames=getattr(namespace, "capture_max_frames", None),
        capture_include_threads=getattr(namespace, "capture_include_threads", None),
        capture_include_backtraces=getattr(namespace, "capture_include_backtraces", None),
        capture_include_frame=getattr(namespace, "capture_include_frame", None),
        capture_include_variables=getattr(namespace, "capture_include_variables", None),
        capture_include_registers=getattr(namespace, "capture_include_registers", None),
        capture_include_transcript=getattr(namespace, "capture_include_transcript", None),
        capture_include_stop_history=getattr(namespace, "capture_include_stop_history", None),
    )
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_builders.py -k 'workflow or failure' tests/mcp/test_client_cli.py -k 'workflow or run_until_failure' tests/integration/test_client_streamable_http.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/builders/workflow.py src/gdb_mcp/client/inputs.py src/gdb_mcp/client/input_parsers.py src/gdb_mcp/client/specs.py src/gdb_mcp/client/parsers.py tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py tests/integration/test_client_streamable_http.py
git commit -m "refactor: type workflow and campaign client inputs"
```

### Task 5: Simplify Client Spec Orchestration And Remove Obsolete Namespace Logic

**Files:**
- Modify: `src/gdb_mcp/client/specs.py`
- Modify: `src/gdb_mcp/client/input_parsers.py`
- Modify: `src/gdb_mcp/client/parsers.py`
- Modify: `src/gdb_mcp/client/cli.py`
- Modify: `tests/mcp/test_client_builders.py`
- Modify: `tests/mcp/test_client_cli.py`

**Testing approach:** `existing tests + targeted verification`
Reason: By this stage the main behavior seams are already covered. This task is mostly structural cleanup: deleting obsolete helpers, shrinking `specs.py`, and making the new pattern obvious and consistent.

- [ ] **Step 1: Capture the current size/shape of `specs.py` and the remaining dynamic access patterns**

```bash
wc -l src/gdb_mcp/client/specs.py
rg -n "hasattr\\(|getattr\\(" src/gdb_mcp/client -g '*.py'
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py`
Expected: PASS before the cleanup-only changes start.

- [ ] **Step 3: Remove obsolete namespace-dependent helpers and reduce `specs.py` to orchestration**

```python
# src/gdb_mcp/client/specs.py

CLIENT_TOOL_SPECS: dict[str, ToolCliSpec] = {
    "gdb_session_start": ToolCliSpec(
        name="gdb_session_start",
        configure_parser=_configure_session_start,
        parse_input=parse_session_start_input,
        build_arguments=lambda typed_input: validate_model_payload(
            StartSessionArgs,
            build_session_start_payload(cast(SessionStartInput, typed_input)),
        ),
        render_human=render_session_start,
    ),
    "gdb_session_query": ToolCliSpec(
        name="gdb_session_query",
        configure_parser=_configure_session_query,
        parse_input=parse_session_query_input,
        build_arguments=lambda typed_input: validate_model_payload(
            SessionQueryArgs,
            build_session_query_payload(cast(SessionQueryInput, typed_input)),
        ),
        render_human=render_action_payload,
    ),
}
```

```python
# src/gdb_mcp/client/parsers.py

def ensure_action_fields(
    provided_fields: set[str],
    *,
    action: str,
    allowed_fields: Iterable[str],
) -> None:
    allowed = set(allowed_fields)
    unexpected = [
        format_cli_flag(field_name)
        for field_name in sorted(provided_fields)
        if field_name not in allowed
    ]
    if unexpected:
        joined = ", ".join(unexpected)
        raise CliUsageError(f"{joined} not valid with --action {action}")
```

```python
# src/gdb_mcp/client/input_parsers.py

def provided_fields(namespace: argparse.Namespace, tracked_fields: Iterable[str]) -> set[str]:
    return {
        field_name
        for field_name in tracked_fields
        if hasattr(namespace, field_name)
    }
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py && uv run ruff check src/gdb_mcp/client tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py && uv run mypy src/gdb_mcp/client`
Expected: PASS, with materially fewer `hasattr()` / `getattr()` hits remaining in `src/gdb_mcp/client/`.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/client/specs.py src/gdb_mcp/client/input_parsers.py src/gdb_mcp/client/parsers.py src/gdb_mcp/client/cli.py tests/mcp/test_client_builders.py tests/mcp/test_client_cli.py
git commit -m "refactor: simplify client spec orchestration"
```

### Task 6: Apply The Same Typed-Boundary Pattern To Action Dispatch In MCP Handlers

**Files:**
- Modify: `src/gdb_mcp/mcp/handlers.py`
- Modify: `tests/mcp/test_handlers.py`

**Testing approach:** `existing tests + targeted verification`
Reason: This is the pattern-setting follow-through outside the client package. Handler behavior is already heavily covered; the goal is to replace dynamic `getattr()`-driven union access with explicit typed dispatch while preserving behavior.

- [ ] **Step 1: Add or tighten handler tests around the families being cleaned up**

```python
# tests/mcp/test_handlers.py

def test_breakpoint_query_list_routes_enabled_filter() -> None:
    manager = Mock()
    session = _session_double()
    session.list_breakpoints.return_value = OperationSuccess(
        BreakpointListInfo(
            breakpoints=[
                {"number": "1", "type": "breakpoint", "enabled": "y"},
                {"number": "2", "type": "watchpoint", "enabled": "n"},
            ],
            count=2,
        )
    )
    manager.resolve_session.return_value = session

    result_data = dispatch(
        "gdb_breakpoint_query",
        {
            "session_id": 3,
            "action": "list",
            "query": {"enabled": True},
        },
        manager,
    )

    assert result_data["action"] == "list"
    assert result_data["result"]["count"] == 1
    assert result_data["result"]["breakpoints"][0]["number"] == "1"


def test_breakpoint_manage_create_watch_routes_access_type() -> None:
    manager = Mock()
    session = _session_double()
    session.set_watchpoint.return_value = OperationSuccess(
        BreakpointInfo(breakpoint={"number": "2", "type": "watchpoint"})
    )
    manager.resolve_session.return_value = session

    dispatch(
        "gdb_breakpoint_manage",
        {
            "session_id": 3,
            "action": "create",
            "breakpoint": {
                "kind": "watch",
                "expression": "value",
                "access": "access",
            },
        },
        manager,
    )

    session.set_watchpoint.assert_called_once_with(expression="value", access="access")


def test_inspect_query_source_routes_file_range() -> None:
    manager = Mock()
    session = _session_double()
    session.get_source_context.return_value = OperationSuccess(
        SourceContextInfo(
            scope="file_range",
            file="src/main.c",
            fullname="src/main.c",
            line_start=40,
            line_end=45,
            context_before=1,
            context_after=1,
            lines=[],
        )
    )
    manager.resolve_session.return_value = session

    dispatch(
        "gdb_inspect_query",
        {
            "session_id": 2,
            "action": "source",
            "query": {
                "location": {
                    "kind": "file_range",
                    "file": "src/main.c",
                    "start_line": 40,
                    "end_line": 45,
                },
                "context_before": 1,
                "context_after": 1,
            },
        },
        manager,
    )

    session.get_source_context.assert_called_once_with(
        thread_id=None,
        frame=None,
        function=None,
        address=None,
        file="src/main.c",
        line=None,
        start_line=40,
        end_line=45,
        context_before=1,
        context_after=1,
    )
```

- [ ] **Step 2: Run the focused verification for this step**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'breakpoint_query or breakpoint_manage or inspect_query or inferior_manage or context_query'`
Expected: PASS first; this task is a characterization/cleanup refactor, not a behavior addition.

- [ ] **Step 3: Replace dynamic action/payload access in `handlers.py` with typed `isinstance` dispatch**

```python
# src/gdb_mcp/mcp/handlers.py

def _handle_breakpoint_query(session: SessionService, args: BaseModel) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, BreakpointQueryListAction):
        result = session.list_breakpoints()
        if isinstance(result, OperationError):
            return _wrap_action_result("list", result)

        kinds = set(action_args.query.kinds)
        enabled_filter = action_args.query.enabled
        if not kinds and enabled_filter is None:
            return _wrap_action_result("list", result)

        filtered_breakpoints = []
        for breakpoint_info in result.value.breakpoints:
            breakpoint_type = str(breakpoint_info.get("type", "")).lower()
            breakpoint_kind = "code"
            if "watch" in breakpoint_type:
                breakpoint_kind = "watch"
            elif "catch" in breakpoint_type:
                breakpoint_kind = "catch"

            if kinds and breakpoint_kind not in kinds:
                continue

            enabled_value = breakpoint_info.get("enabled")
            is_enabled = enabled_value in {True, "y", "Y", "1", 1}
            if enabled_filter is not None and is_enabled != enabled_filter:
                continue

            filtered_breakpoints.append(breakpoint_info)

    if isinstance(action_args, BreakpointQueryGetAction):
        return _wrap_action_result(
            "get",
            session.get_breakpoint(action_args.query.number),
        )

    return OperationError(
        message=f"Unsupported breakpoint query action: {type(action_args).__name__}",
        code="validation_error",
    )
```

```python
# src/gdb_mcp/mcp/handlers.py

def _handle_inspect_query(session: SessionService, args: BaseModel) -> ToolResult:
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, InspectQueryEvaluateAction):
        context = action_args.query.context
        return _wrap_action_result(
            "evaluate",
            session.evaluate_expression(
                action_args.query.expression,
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else None,
            ),
        )

    if isinstance(action_args, InspectSourceAction):
        context = action_args.query.context
        location = _location_kwargs(action_args.query.location)
        return _wrap_action_result(
            "source",
            session.get_source_context(
                thread_id=context.thread_id if context is not None else None,
                frame=context.frame if context is not None else None,
                function=cast(str | None, location["function"]),
                address=cast(str | None, location["address"]),
                file=cast(str | None, location["file"]),
                line=cast(int | None, location["line"]),
                start_line=cast(int | None, location["start_line"]),
                end_line=cast(int | None, location["end_line"]),
                context_before=action_args.query.context_before,
                context_after=action_args.query.context_after,
            ),
        )
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run pytest -q tests/mcp/test_handlers.py -k 'breakpoint_query or breakpoint_manage or inspect_query or inferior_manage or context_query' && uv run ruff check src/gdb_mcp/mcp/handlers.py tests/mcp/test_handlers.py && uv run mypy src`
Expected: PASS with significantly reduced `getattr()` usage in `src/gdb_mcp/mcp/handlers.py`.

- [ ] **Step 5: Commit**

```bash
git add src/gdb_mcp/mcp/handlers.py tests/mcp/test_handlers.py
git commit -m "refactor: remove dynamic action access from handlers"
```

### Task 7: Run Full Verification And Final Cleanup Review

**Files:**
- Verify only: full repository

**Testing approach:** `existing tests + targeted verification`
Reason: The earlier tasks already carry the behavior changes. This final task confirms the full repository still passes after the staged refactor and records the final cleanup state.

- [ ] **Step 1: Run the full repository verification**

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
git diff --check
```

- [ ] **Step 2: Inspect the remaining dynamic access hotspots**

```bash
rg -n "hasattr\\(|getattr\\(" src/gdb_mcp/client src/gdb_mcp/mcp/handlers.py -g '*.py'
```

- [ ] **Step 3: Make any final non-behavioral cleanup needed to satisfy the target pattern**

```bash
rg -n "_build_action_arguments|_execution_wait|_validate_workflow_step_payload|_build_location|_build_context_override" src/gdb_mcp/client src/gdb_mcp/mcp/handlers.py
rg -n "from \\.specs import .*parse_|from \\.specs import .*build_" src/gdb_mcp/client -g '*.py'
git diff -- src/gdb_mcp/client src/gdb_mcp/mcp/handlers.py
```

- [ ] **Step 4: Run the post-change verification**

Run: `uv run ruff check src tests && uv run mypy src && uv run pytest -q && git diff --check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "refactor: finalize typed client cleanup"
```
