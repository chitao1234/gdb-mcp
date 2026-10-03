"""Batch, capture-bundle, and run-until-failure request models."""

from __future__ import annotations

from typing import Optional, TypeAlias

from pydantic import (
    Field,
    model_validator,
)

from ...contracts import (
    BatchStepToolName,
)

from .common import (
    SessionId,
    StrictArgsModel,
)
from .selectors import CaptureMemoryRangeArgs
from .dedicated import StartSessionArgs


class BatchStepArgs(StrictArgsModel):
    """One validated batch step definition."""

    tool: BatchStepToolName = Field(..., description="Existing session-scoped tool to execute")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        description="Tool-specific arguments excluding session_id, which comes from the batch",
    )
    label: Optional[str] = Field(
        None,
        description="Optional human-readable label to make batch results easier to scan",
    )


BatchStepInput: TypeAlias = BatchStepArgs | BatchStepToolName


class BatchArgs(StrictArgsModel):
    """Arguments for executing a structured batch against one live session."""

    session_id: SessionId
    steps: list[BatchStepInput] = Field(
        ...,
        min_length=1,
        description=(
            "Ordered step list. "
            "Each entry can be either a full object "
            "({'tool': ..., 'arguments': {...}, 'label': ...}) "
            "or a shorthand tool-name string."
        ),
    )
    fail_fast: bool = Field(
        True,
        description="Stop executing later steps after the first error result",
    )
    capture_stop_events: bool = Field(
        True,
        description="Include any new stop event produced by each step in the batch result",
    )


class CaptureOptionsArgs(StrictArgsModel):
    """Shared capture artifact options for bundle and campaign requests."""

    output_dir: Optional[str] = Field(
        None,
        description="Directory in which to create the capture bundle. Defaults to artifact_root or the system temp directory.",
    )
    bundle_name: Optional[str] = Field(
        None,
        description="Optional deterministic subdirectory name for the bundle.",
    )
    expressions: list[str] = Field(
        default_factory=list,
        description="Expressions to evaluate and include in the bundle.",
    )
    memory_ranges: list[CaptureMemoryRangeArgs | str] = Field(
        default_factory=list,
        description=(
            "Explicit memory ranges to capture. "
            "Each entry can be either a structured object or shorthand "
            "string '<address>:<count>' (optional offset: '<address>:<count>@<offset>'). "
            "Each range is opt-in and bounded by server-side size limits."
        ),
    )
    max_frames: int = Field(
        100,
        gt=0,
        description="Maximum number of frames to include per thread backtrace.",
    )
    include_threads: bool = Field(True, description="Capture thread inventory.")
    include_backtraces: bool = Field(
        True, description="Capture backtraces for all enumerated threads."
    )
    include_frame: bool = Field(True, description="Capture the currently selected frame.")
    include_variables: bool = Field(
        True, description="Capture local variables for the current selection."
    )
    include_registers: bool = Field(
        True, description="Capture registers for the current selection."
    )
    include_transcript: bool = Field(True, description="Capture the bounded command transcript.")
    include_stop_history: bool = Field(True, description="Capture the bounded stop-event history.")


class CaptureBundleArgs(CaptureOptionsArgs):
    """Arguments for writing a structured capture bundle to disk."""

    session_id: SessionId


class RunUntilFailureFailureArgs(StrictArgsModel):
    """Failure predicates for repeat-until-failure campaigns."""

    failure_on_error: bool = Field(
        True,
        description="Treat an error result from gdb_run or session startup as a matching failure.",
    )
    failure_on_timeout: bool = Field(
        True,
        description="Treat a timeout from gdb_run as a matching failure.",
    )
    stop_reasons: list[str] = Field(
        default_factory=lambda: ["signal-received", "exited-signalled"],
        description="Stop reasons that should count as a matching failure.",
    )
    execution_states: list[str] = Field(
        default_factory=list,
        description="Inferior execution states that should count as a matching failure.",
    )
    exit_codes: list[int] = Field(
        default_factory=list,
        description="Exit codes that should count as a matching failure.",
    )
    result_text_regex: Optional[str] = Field(
        None,
        description="Regular expression applied to the serialized run result payload.",
    )


class RunUntilFailureCaptureArgs(CaptureOptionsArgs):
    """Capture settings used when a run-until-failure campaign matches."""

    enabled: bool = Field(True, description="Capture a forensic bundle when a failure matches.")
    bundle_name_prefix: Optional[str] = Field(
        None,
        description="Deterministic bundle name prefix. The iteration number is appended automatically.",
    )
    output_dir: Optional[str] = Field(
        None,
        description="Directory in which to place the capture bundle for the matching iteration.",
    )
    bundle_name: Optional[str] = Field(
        None,
        description=(
            "Optional exact bundle name to use for the matching iteration. "
            "Cannot be combined with bundle_name_prefix."
        ),
    )

    @model_validator(mode="after")
    def validate_bundle_naming(self) -> "RunUntilFailureCaptureArgs":
        """Reject ambiguous capture naming configuration."""

        if self.bundle_name is not None and self.bundle_name_prefix is not None:
            raise ValueError(
                "capture.bundle_name and capture.bundle_name_prefix are mutually exclusive"
            )
        return self


class RunUntilFailureArgs(StrictArgsModel):
    """Arguments for repeating fresh-session runs until one failure matches."""

    startup: StartSessionArgs = Field(
        default_factory=lambda: StartSessionArgs.model_validate({}),
        description="Session startup configuration used for every iteration.",
    )
    setup_steps: list[BatchStepInput] = Field(
        default_factory=list,
        description="Optional structured setup steps run after startup and before gdb_run.",
    )
    run_args: Optional[list[str] | str] = Field(
        None,
        description=(
            "Arguments passed to gdb_run for each iteration. "
            "Accepts either an explicit argv list or one shell-style string."
        ),
    )
    run_timeout_sec: int = Field(30, gt=0, description="Timeout for each gdb_run attempt.")
    max_iterations: int = Field(1, gt=0, description="Maximum number of iterations to attempt.")
    failure: RunUntilFailureFailureArgs = Field(
        default_factory=lambda: RunUntilFailureFailureArgs.model_validate({}),
        description="Failure predicates that stop the campaign.",
    )
    capture: RunUntilFailureCaptureArgs = Field(
        default_factory=lambda: RunUntilFailureCaptureArgs.model_validate({}),
        description="Capture settings used when a failure matches.",
    )
