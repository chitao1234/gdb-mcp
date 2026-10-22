"""CLI parser and payload builders for workflow tools."""

from __future__ import annotations

import argparse

from gdb_mcp.contracts import DEBUGGER_NAMES
from gdb_mcp.mcp.schemas import (
    BatchArgs,
    CaptureBundleArgs,
    RunUntilFailureArgs,
)

from ..input_parsers import parse_step_inputs
from ..inputs import SessionStepInput
from ..namespace import (
    NamespaceLike,
    boolean,
    integer,
    key_value_entries,
    optional_integer,
    optional_text,
    snapshot,
    tagged_events,
    text_tuple,
)
from ..parsers import (
    StepLabelEvent,
    StepToolEvent,
    add_boolean_flag,
    collapse_key_value_entries,
    dotted_assignment,
    key_value_entry,
    validate_model,
)


def _step_payload(step: SessionStepInput) -> dict[str, object]:
    payload: dict[str, object] = {"tool": step.tool, "arguments": step.arguments}
    if step.label is not None:
        payload["label"] = step.label
    return payload


def _configure_capture_bundle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--output-dir")
    parser.add_argument("--bundle-name")
    parser.add_argument("--expression", dest="expressions", action="append", default=[])
    parser.add_argument("--memory-range", dest="memory_ranges", action="append", default=[])
    parser.add_argument("--max-frames", type=int, default=100)
    add_boolean_flag(parser, "include_threads", help_text="Capture thread inventory")
    add_boolean_flag(
        parser,
        "include_backtraces",
        help_text="Capture thread backtraces",
    )
    add_boolean_flag(parser, "include_frame", help_text="Capture current frame")
    add_boolean_flag(
        parser,
        "include_variables",
        help_text="Capture variables",
    )
    add_boolean_flag(
        parser,
        "include_registers",
        help_text="Capture registers",
    )
    add_boolean_flag(
        parser,
        "include_transcript",
        help_text="Capture transcript",
    )
    add_boolean_flag(
        parser,
        "include_stop_history",
        help_text="Capture stop history",
    )


def _build_capture_bundle(namespace: NamespaceLike) -> CaptureBundleArgs:
    values = snapshot(namespace)
    return validate_model(
        CaptureBundleArgs,
        {
            "session_id": optional_integer(values, "session_id"),
            "output_dir": optional_text(values, "output_dir"),
            "bundle_name": optional_text(values, "bundle_name"),
            "expressions": list(text_tuple(values, "expressions")),
            "memory_ranges": list(text_tuple(values, "memory_ranges")),
            "max_frames": integer(values, "max_frames"),
            "include_threads": boolean(values, "include_threads"),
            "include_backtraces": boolean(values, "include_backtraces"),
            "include_frame": boolean(values, "include_frame"),
            "include_variables": boolean(values, "include_variables"),
            "include_registers": boolean(values, "include_registers"),
            "include_transcript": boolean(values, "include_transcript"),
            "include_stop_history": boolean(values, "include_stop_history"),
        },
    )


def _configure_workflow_batch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--step", dest="step_events", action="append", type=StepToolEvent, default=[]
    )
    parser.add_argument("--step-label", dest="step_events", action="append", type=StepLabelEvent)
    parser.add_argument(
        "--step-arg",
        dest="step_events",
        action="append",
        type=dotted_assignment,
    )
    add_boolean_flag(
        parser,
        "fail_fast",
        help_text="Stop executing later steps after the first error",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_stop_events",
        help_text="Include new stop events produced by batch steps",
        optional=True,
    )


def _build_workflow_batch(namespace: NamespaceLike) -> BatchArgs:
    values = snapshot(namespace)
    steps = parse_step_inputs(
        tagged_events(values, "step_events"), required=True, step_flag="--step"
    )
    payload: dict[str, object] = {
        "session_id": values.get("session_id"),
        "steps": [_step_payload(step) for step in steps],
    }
    for name in ("fail_fast", "capture_stop_events"):
        value = values.get(name)
        if value is not None:
            payload[name] = value
    return validate_model(BatchArgs, payload)


def _configure_run_until_failure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--startup-debugger", choices=list(DEBUGGER_NAMES), default=None)
    parser.add_argument("--startup-program", default=None)
    parser.add_argument("--startup-arg", dest="startup_args", action="append", default=[])
    parser.add_argument(
        "--startup-init-command",
        dest="startup_init_commands",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--startup-env",
        dest="startup_env",
        action="append",
        type=key_value_entry,
        default=[],
    )
    parser.add_argument("--startup-debugger-path", default=None)
    parser.add_argument("--startup-working-dir", default=None)
    parser.add_argument("--startup-core", default=None)
    parser.add_argument(
        "--setup-step", dest="setup_step_events", action="append", type=StepToolEvent, default=[]
    )
    parser.add_argument(
        "--setup-step-label", dest="setup_step_events", action="append", type=StepLabelEvent
    )
    parser.add_argument(
        "--setup-step-arg",
        dest="setup_step_events",
        action="append",
        type=dotted_assignment,
    )
    parser.add_argument("--run-arg", dest="run_args", action="append", default=[])
    parser.add_argument("--run-timeout-sec", type=int, default=None)
    parser.add_argument("--max-iterations", type=int, default=None)
    add_boolean_flag(
        parser,
        "failure_on_error",
        help_text="Treat startup or run errors as matching failures",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "failure_on_timeout",
        help_text="Treat run timeouts as matching failures",
        optional=True,
    )
    parser.add_argument(
        "--failure-stop-reason",
        dest="failure_stop_reasons",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--failure-execution-state",
        dest="failure_execution_states",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--failure-exit-code",
        dest="failure_exit_codes",
        action="append",
        type=int,
        default=None,
    )
    parser.add_argument("--failure-result-text-regex", default=None)
    add_boolean_flag(
        parser,
        "capture_enabled",
        help_text="Write a capture bundle when a failure matches",
        optional=True,
    )
    parser.add_argument("--capture-output-dir", default=None)
    parser.add_argument("--capture-bundle-name-prefix", default=None)
    parser.add_argument("--capture-bundle-name", default=None)
    parser.add_argument(
        "--capture-expression",
        dest="capture_expressions",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--capture-memory-range",
        dest="capture_memory_ranges",
        action="append",
        default=[],
    )
    parser.add_argument("--capture-max-frames", type=int, default=None)
    add_boolean_flag(
        parser,
        "capture_include_threads",
        help_text="Capture thread inventory",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_backtraces",
        help_text="Capture thread backtraces",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_frame",
        help_text="Capture the selected frame",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_variables",
        help_text="Capture variables for the selected context",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_registers",
        help_text="Capture registers for the selected context",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_transcript",
        help_text="Capture the bounded command transcript",
        optional=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_stop_history",
        help_text="Capture the bounded stop-event history",
        optional=True,
    )


def _build_run_until_failure(namespace: NamespaceLike) -> RunUntilFailureArgs:
    values = snapshot(namespace)
    startup = {
        name: value
        for name in ("debugger", "program", "debugger_path", "working_dir", "core")
        if (value := values.get("startup_" + name)) is not None
    }
    for name in ("args", "init_commands"):
        items = values.get("startup_" + name)
        if items:
            startup[name] = items
    env = collapse_key_value_entries(key_value_entries(values, "startup_env"))
    if env is not None:
        startup["env"] = env
    failure: dict[str, object] = {
        name: value
        for name in ("failure_on_error", "failure_on_timeout")
        if (value := values.get(name)) is not None
    }
    for name in ("stop_reasons", "execution_states", "exit_codes", "result_text_regex"):
        value = values.get("failure_" + name)
        if value is not None:
            failure[name] = value
    capture = {
        name: value
        for name in (
            "enabled",
            "output_dir",
            "bundle_name_prefix",
            "bundle_name",
            "max_frames",
            "include_threads",
            "include_backtraces",
            "include_frame",
            "include_variables",
            "include_registers",
            "include_transcript",
            "include_stop_history",
        )
        if (value := values.get("capture_" + name)) is not None
    }
    for name in ("expressions", "memory_ranges"):
        items = values.get("capture_" + name)
        if items:
            capture[name] = items
    payload: dict[str, object] = {
        name: value
        for name, value in (("startup", startup), ("failure", failure), ("capture", capture))
        if value
    }
    steps = parse_step_inputs(
        tagged_events(values, "setup_step_events"), required=False, step_flag="--setup-step"
    )
    if steps is not None:
        payload["setup_steps"] = [_step_payload(step) for step in steps]
    if values.get("run_args"):
        payload["run_args"] = values.get("run_args")
    for name in ("run_timeout_sec", "max_iterations"):
        value = values.get(name)
        if value is not None:
            payload[name] = value
    return validate_model(RunUntilFailureArgs, payload)
