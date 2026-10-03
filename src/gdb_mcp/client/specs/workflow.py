"""CLI parser and payload builders for workflow tools."""

from __future__ import annotations

import argparse


from gdb_mcp.mcp.schemas import (
    BatchArgs,
    CaptureBundleArgs,
    RunUntilFailureArgs,
)

from ..builders.workflow import build_run_until_failure_payload, build_workflow_batch_payload
from ..inputs import (
    RunUntilFailureInput,
    WorkflowBatchInput,
)
from ..parsers import (
    AppendTaggedValue,
    add_boolean_flag,
    dotted_assignment,
    key_value_entry,
    validate_model_payload,
)


def _configure_capture_bundle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=None)
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


def _build_capture_bundle(namespace: argparse.Namespace) -> dict[str, object]:
    return validate_model_payload(
        CaptureBundleArgs,
        {
            "session_id": namespace.session_id,
            "output_dir": namespace.output_dir,
            "bundle_name": namespace.bundle_name,
            "expressions": namespace.expressions,
            "memory_ranges": namespace.memory_ranges,
            "max_frames": namespace.max_frames,
            "include_threads": namespace.include_threads,
            "include_backtraces": namespace.include_backtraces,
            "include_frame": namespace.include_frame,
            "include_variables": namespace.include_variables,
            "include_registers": namespace.include_registers,
            "include_transcript": namespace.include_transcript,
            "include_stop_history": namespace.include_stop_history,
        },
    )


def _configure_workflow_batch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=None)
    parser.add_argument("--step", dest="step_events", action=AppendTaggedValue)
    parser.add_argument("--step-label", dest="step_events", action=AppendTaggedValue)
    parser.add_argument(
        "--step-arg",
        dest="step_events",
        action=AppendTaggedValue,
        type=dotted_assignment,
    )
    add_boolean_flag(
        parser,
        "fail_fast",
        help_text="Stop executing later steps after the first error",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_stop_events",
        help_text="Include new stop events produced by batch steps",
        suppress_default=True,
    )


def _build_workflow_batch(typed_input: WorkflowBatchInput) -> dict[str, object]:
    payload = build_workflow_batch_payload(typed_input)
    return validate_model_payload(BatchArgs, payload)


def _configure_run_until_failure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--startup-program", default=argparse.SUPPRESS)
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
    parser.add_argument("--startup-gdb-path", default=argparse.SUPPRESS)
    parser.add_argument("--startup-working-dir", default=argparse.SUPPRESS)
    parser.add_argument("--startup-core", default=argparse.SUPPRESS)
    parser.add_argument("--setup-step", dest="setup_step_events", action=AppendTaggedValue)
    parser.add_argument("--setup-step-label", dest="setup_step_events", action=AppendTaggedValue)
    parser.add_argument(
        "--setup-step-arg",
        dest="setup_step_events",
        action=AppendTaggedValue,
        type=dotted_assignment,
    )
    parser.add_argument("--run-arg", dest="run_args", action="append", default=[])
    parser.add_argument("--run-timeout-sec", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--max-iterations", type=int, default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "failure_on_error",
        help_text="Treat startup or run errors as matching failures",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "failure_on_timeout",
        help_text="Treat run timeouts as matching failures",
        suppress_default=True,
    )
    parser.add_argument(
        "--failure-stop-reason",
        dest="failure_stop_reasons",
        action="append",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--failure-execution-state",
        dest="failure_execution_states",
        action="append",
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--failure-exit-code",
        dest="failure_exit_codes",
        action="append",
        type=int,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--failure-result-text-regex", default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "capture_enabled",
        help_text="Write a capture bundle when a failure matches",
        suppress_default=True,
    )
    parser.add_argument("--capture-output-dir", default=argparse.SUPPRESS)
    parser.add_argument("--capture-bundle-name-prefix", default=argparse.SUPPRESS)
    parser.add_argument("--capture-bundle-name", default=argparse.SUPPRESS)
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
    parser.add_argument("--capture-max-frames", type=int, default=argparse.SUPPRESS)
    add_boolean_flag(
        parser,
        "capture_include_threads",
        help_text="Capture thread inventory",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_backtraces",
        help_text="Capture thread backtraces",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_frame",
        help_text="Capture the selected frame",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_variables",
        help_text="Capture variables for the selected context",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_registers",
        help_text="Capture registers for the selected context",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_transcript",
        help_text="Capture the bounded command transcript",
        suppress_default=True,
    )
    add_boolean_flag(
        parser,
        "capture_include_stop_history",
        help_text="Capture the bounded stop-event history",
        suppress_default=True,
    )


def _build_run_until_failure(typed_input: RunUntilFailureInput) -> dict[str, object]:
    payload = build_run_until_failure_payload(typed_input)
    return validate_model_payload(RunUntilFailureArgs, payload)
