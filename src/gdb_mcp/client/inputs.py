"""Typed internal client inputs parsed from CLI namespaces."""

from __future__ import annotations

from dataclasses import dataclass

from gdb_mcp.contracts import (
    BatchStepToolName,
)


@dataclass(frozen=True, slots=True)
class SessionStepInput:
    """Typed workflow/setup step used by batch-oriented commands."""

    tool: BatchStepToolName
    label: str | None
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class WorkflowBatchInput:
    """Parsed input for ``gdb_workflow_batch``."""

    session_id: int | None
    steps: tuple[SessionStepInput, ...]
    fail_fast: bool | None
    capture_stop_events: bool | None


@dataclass(frozen=True, slots=True)
class RunUntilFailureInput:
    """Parsed input for ``gdb_run_until_failure``."""

    startup_program: str | None
    startup_args: tuple[str, ...]
    startup_init_commands: tuple[str, ...]
    startup_env: dict[str, str] | None
    startup_debugger_path: str | None
    startup_working_dir: str | None
    startup_core: str | None
    setup_steps: tuple[SessionStepInput, ...] | None
    run_args: tuple[str, ...]
    run_timeout_sec: int | None
    max_iterations: int | None
    failure_on_error: bool | None
    failure_on_timeout: bool | None
    failure_stop_reasons: tuple[str, ...] | None
    failure_execution_states: tuple[str, ...] | None
    failure_exit_codes: tuple[int, ...] | None
    failure_result_text_regex: str | None
    capture_enabled: bool | None
    capture_output_dir: str | None
    capture_bundle_name_prefix: str | None
    capture_bundle_name: str | None
    capture_expressions: tuple[str, ...]
    capture_memory_ranges: tuple[str, ...]
    capture_max_frames: int | None
    capture_include_threads: bool | None
    capture_include_backtraces: bool | None
    capture_include_frame: bool | None
    capture_include_variables: bool | None
    capture_include_registers: bool | None
    capture_include_transcript: bool | None
    capture_include_stop_history: bool | None
    startup_debugger: str | None = None
