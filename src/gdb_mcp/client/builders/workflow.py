"""Pure payload builders for workflow-oriented CLI commands."""

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


def build_workflow_batch_payload(typed_input: WorkflowBatchInput) -> dict[str, object]:
    """Build the raw workflow-batch payload from typed input."""

    payload: dict[str, object] = {
        "session_id": typed_input.session_id,
        "steps": [_build_step_payload(step) for step in typed_input.steps],
    }
    if typed_input.fail_fast is not None:
        payload["fail_fast"] = typed_input.fail_fast
    if typed_input.capture_stop_events is not None:
        payload["capture_stop_events"] = typed_input.capture_stop_events
    return payload


def build_run_until_failure_payload(typed_input: RunUntilFailureInput) -> dict[str, object]:
    """Build the raw run-until-failure payload from typed input."""

    startup: dict[str, object] = {}
    if typed_input.startup_program is not None:
        startup["program"] = typed_input.startup_program
    if typed_input.startup_args:
        startup["args"] = list(typed_input.startup_args)
    if typed_input.startup_init_commands:
        startup["init_commands"] = list(typed_input.startup_init_commands)
    if typed_input.startup_env is not None:
        startup["env"] = typed_input.startup_env
    if typed_input.startup_gdb_path is not None:
        startup["gdb_path"] = typed_input.startup_gdb_path
    if typed_input.startup_working_dir is not None:
        startup["working_dir"] = typed_input.startup_working_dir
    if typed_input.startup_core is not None:
        startup["core"] = typed_input.startup_core

    failure: dict[str, object] = {}
    if typed_input.failure_on_error is not None:
        failure["failure_on_error"] = typed_input.failure_on_error
    if typed_input.failure_on_timeout is not None:
        failure["failure_on_timeout"] = typed_input.failure_on_timeout
    if typed_input.failure_stop_reasons is not None:
        failure["stop_reasons"] = list(typed_input.failure_stop_reasons)
    if typed_input.failure_execution_states is not None:
        failure["execution_states"] = list(typed_input.failure_execution_states)
    if typed_input.failure_exit_codes is not None:
        failure["exit_codes"] = list(typed_input.failure_exit_codes)
    if typed_input.failure_result_text_regex is not None:
        failure["result_text_regex"] = typed_input.failure_result_text_regex

    capture: dict[str, object] = {}
    if typed_input.capture_enabled is not None:
        capture["enabled"] = typed_input.capture_enabled
    if typed_input.capture_output_dir is not None:
        capture["output_dir"] = typed_input.capture_output_dir
    if typed_input.capture_bundle_name_prefix is not None:
        capture["bundle_name_prefix"] = typed_input.capture_bundle_name_prefix
    if typed_input.capture_bundle_name is not None:
        capture["bundle_name"] = typed_input.capture_bundle_name
    if typed_input.capture_expressions:
        capture["expressions"] = list(typed_input.capture_expressions)
    if typed_input.capture_memory_ranges:
        capture["memory_ranges"] = list(typed_input.capture_memory_ranges)
    if typed_input.capture_max_frames is not None:
        capture["max_frames"] = typed_input.capture_max_frames
    if typed_input.capture_include_threads is not None:
        capture["include_threads"] = typed_input.capture_include_threads
    if typed_input.capture_include_backtraces is not None:
        capture["include_backtraces"] = typed_input.capture_include_backtraces
    if typed_input.capture_include_frame is not None:
        capture["include_frame"] = typed_input.capture_include_frame
    if typed_input.capture_include_variables is not None:
        capture["include_variables"] = typed_input.capture_include_variables
    if typed_input.capture_include_registers is not None:
        capture["include_registers"] = typed_input.capture_include_registers
    if typed_input.capture_include_transcript is not None:
        capture["include_transcript"] = typed_input.capture_include_transcript
    if typed_input.capture_include_stop_history is not None:
        capture["include_stop_history"] = typed_input.capture_include_stop_history

    payload: dict[str, object] = {}
    if startup:
        payload["startup"] = startup
    if typed_input.setup_steps is not None:
        payload["setup_steps"] = [_build_step_payload(step) for step in typed_input.setup_steps]
    if typed_input.run_args:
        payload["run_args"] = list(typed_input.run_args)
    if typed_input.run_timeout_sec is not None:
        payload["run_timeout_sec"] = typed_input.run_timeout_sec
    if typed_input.max_iterations is not None:
        payload["max_iterations"] = typed_input.max_iterations
    if failure:
        payload["failure"] = failure
    if capture:
        payload["capture"] = capture
    return payload
