"""Unit tests for MCP schema models."""

import pytest
from pydantic import ValidationError
from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    BatchArgs,
    BatchStepArgs,
    BreakpointNumberArgs,
    CallFunctionArgs,
    CaptureMemoryRangeArgs,
    CaptureBundleArgs,
    DetachOnForkArgs,
    ExecuteCommandArgs,
    FollowForkModeArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    ReadMemoryArgs,
    GetRegistersArgs,
    ThreadSelectArgs,
    InferiorSelectArgs,
    SetCatchpointArgs,
    SetBreakpointArgs,
    SetWatchpointArgs,
    EvaluateExpressionArgs,
    GetVariablesArgs,
    ListSessionsArgs,
    RunUntilFailureArgs,
    RunUntilFailureCaptureArgs,
    RunArgs,
    StartSessionArgs,
    WaitForStopArgs,
)


class TestStartSessionArgs:
    """Test cases for StartSessionArgs model."""

    def test_minimal_args(self):
        """Test creating StartSessionArgs with minimal arguments."""
        args = StartSessionArgs()
        assert args.program is None
        assert args.args is None
        assert args.init_commands is None
        assert args.env is None
        assert (
            args.gdb_path is None
        )  # Default to None, actual default determined by GDB_PATH env var or "gdb"

    def test_full_args(self):
        """Test creating StartSessionArgs with all arguments."""
        args = StartSessionArgs(
            program="/bin/ls",
            args=["-la", "/tmp"],
            init_commands=["set pagination off"],
            env={"DEBUG": "1"},
            gdb_path="/usr/local/bin/gdb",
        )

        assert args.program == "/bin/ls"
        assert args.args == ["-la", "/tmp"]
        assert args.init_commands == ["set pagination off"]
        assert args.env == {"DEBUG": "1"}
        assert args.gdb_path == "/usr/local/bin/gdb"

    def test_start_session_accepts_shell_style_string_args(self):
        """Startup args should accept a shell-style string for parity with gdb_run."""

        args = StartSessionArgs(program="/bin/ls", args='--mode "fast path"')
        assert args.args == '--mode "fast path"'

    def test_env_dict_validation(self):
        """Test that env accepts dictionary of strings."""
        args = StartSessionArgs(program="/bin/ls", env={"VAR1": "value1", "VAR2": "value2"})

        assert args.env == {"VAR1": "value1", "VAR2": "value2"}

    def test_unknown_field_is_rejected(self):
        """Unexpected request keys should fail validation instead of being ignored."""

        with pytest.raises(ValidationError) as exc_info:
            StartSessionArgs(program="/bin/ls", workingdir="/tmp/work")

        assert "workingdir" in str(exc_info.value)


class TestExecuteCommandArgs:
    """Test cases for ExecuteCommandArgs model."""

    def test_command_required(self):
        """Test that command is required."""
        with pytest.raises(ValidationError):
            ExecuteCommandArgs()

    def test_command_arg(self):
        """Test command argument."""
        args = ExecuteCommandArgs(session_id=1, command="info threads", timeout_sec=10)
        assert args.session_id == 1
        assert args.command == "info threads"
        assert args.timeout_sec == 10

    def test_unknown_field_is_rejected(self):
        """Extra command fields should not be silently dropped."""

        with pytest.raises(ValidationError) as exc_info:
            ExecuteCommandArgs(session_id=1, command="info threads", timeout_seconds=10)

        assert "timeout_seconds" in str(exc_info.value)


class TestRunArgs:
    """Test cases for RunArgs model."""

    def test_defaults(self):
        """RunArgs should allow omitted argv with a default timeout."""

        args = RunArgs(session_id=1)
        assert args.session_id == 1
        assert args.args is None
        assert args.timeout_sec == 30

    def test_accepts_shell_style_string_args(self):
        """RunArgs should accept a single shell-style argument string."""

        args = RunArgs(session_id=1, args='--flag "hello world"')
        assert args.args == '--flag "hello world"'


class TestAttachProcessArgs:
    """Test cases for AttachProcessArgs model."""

    def test_pid_required(self):
        """Attach requests should require a positive PID."""

        with pytest.raises(ValidationError):
            AttachProcessArgs(session_id=1)

    def test_attach_args(self):
        """Attach requests should accept pid and timeout."""

        args = AttachProcessArgs(session_id=1, pid=4321, timeout_sec=15)
        assert args.session_id == 1
        assert args.pid == 4321
        assert args.timeout_sec == 15


class TestListSessionsArgs:
    """Test cases for ListSessionsArgs model."""

    def test_no_args(self):
        """Listing sessions should accept an empty payload."""

        args = ListSessionsArgs()
        assert args.model_dump() == {}


class TestBatchArgs:
    """Test cases for structured batch workflows."""

    def test_batch_args_minimal(self):
        """Batch requests should accept one valid step."""

        args = BatchArgs(
            session_id=1,
            steps=[
                BatchStepArgs(
                    tool="gdb_execute_command",
                    arguments={"command": "info threads"},
                )
            ],
        )

        assert args.session_id == 1
        assert len(args.steps) == 1
        assert args.fail_fast is True
        assert args.capture_stop_events is True

    def test_batch_args_reject_empty_steps(self):
        """Batches must include at least one step."""

        with pytest.raises(ValidationError):
            BatchArgs(session_id=1, steps=[])

    def test_batch_step_rejects_unknown_tool(self):
        """Batch step tools should be validated against the supported allowlist."""

        with pytest.raises(ValidationError) as exc_info:
            BatchStepArgs(tool="gdb_stop_session", arguments={})

        assert "gdb_stop_session" in str(exc_info.value)

    def test_batch_step_rejects_unknown_field(self):
        """Batch step definitions should reject unexpected keys."""

        with pytest.raises(ValidationError) as exc_info:
            BatchStepArgs(tool="gdb_get_status", arguments={}, unexpected=True)

        assert "unexpected" in str(exc_info.value)

    def test_batch_step_accepts_inferior_and_fork_tools(self):
        """Batch steps should allow the new inferior and fork workflow tools."""

        step = BatchStepArgs(tool="gdb_select_inferior", arguments={"inferior_id": 2})

        assert step.tool == "gdb_select_inferior"
        assert step.arguments == {"inferior_id": 2}

    def test_batch_step_accepts_phase_6_tools(self):
        """Batch steps should allow watchpoint, memory, and wait workflow tools."""

        step = BatchStepArgs(tool="gdb_wait_for_stop", arguments={"timeout_sec": 5})

        assert step.tool == "gdb_wait_for_stop"
        assert step.arguments == {"timeout_sec": 5}

    def test_batch_allows_string_step_shorthand(self):
        """Batch steps should allow shorthand tool-name strings."""

        args = BatchArgs(session_id=1, steps=["gdb_get_status"])
        assert args.steps == ["gdb_get_status"]


class TestCaptureBundleArgs:
    """Test cases for bundle capture requests."""

    def test_capture_bundle_defaults(self):
        """Capture requests should provide sensible defaults."""

        args = CaptureBundleArgs(session_id=1)

        assert args.session_id == 1
        assert args.output_dir is None
        assert args.bundle_name is None
        assert args.expressions == []
        assert args.memory_ranges == []
        assert args.max_frames == 100
        assert args.include_threads is True
        assert args.include_backtraces is True
        assert args.include_frame is True
        assert args.include_variables is True
        assert args.include_registers is True
        assert args.include_transcript is True
        assert args.include_stop_history is True

    def test_capture_bundle_rejects_unknown_field(self):
        """Capture requests should reject unexpected keys."""

        with pytest.raises(ValidationError) as exc_info:
            CaptureBundleArgs(session_id=1, output="/tmp/out")

        assert "output" in str(exc_info.value)

    def test_capture_bundle_allows_memory_range_shorthand(self):
        """Capture requests should allow shorthand memory-range strings."""

        args = CaptureBundleArgs(session_id=1, memory_ranges=["&value:16@2"])
        assert args.memory_ranges == ["&value:16@2"]


class TestRunUntilFailureArgs:
    """Test cases for repeat-until-failure campaigns."""

    def test_run_until_failure_defaults(self):
        """Campaign requests should default to one iteration and failure capture."""

        args = RunUntilFailureArgs()

        assert args.startup.program is None
        assert args.setup_steps == []
        assert args.run_args is None
        assert args.run_timeout_sec == 30
        assert args.max_iterations == 1
        assert args.failure.failure_on_error is True
        assert args.failure.failure_on_timeout is True
        assert args.failure.stop_reasons == ["signal-received", "exited-signalled"]
        assert args.capture.enabled is True

    def test_run_until_failure_capture_args_defaults(self):
        """Capture settings should expose deterministic defaults."""

        args = RunUntilFailureCaptureArgs()

        assert args.enabled is True
        assert args.output_dir is None
        assert args.bundle_name_prefix is None
        assert args.bundle_name is None
        assert args.expressions == []
        assert args.memory_ranges == []

    def test_run_until_failure_capture_rejects_conflicting_bundle_fields(self):
        """Capture naming should reject bundle_name and bundle_name_prefix together."""

        with pytest.raises(ValidationError) as exc_info:
            RunUntilFailureCaptureArgs(bundle_name="exact", bundle_name_prefix="prefix")

        assert "mutually exclusive" in str(exc_info.value)

    def test_run_until_failure_rejects_unknown_field(self):
        """Campaign requests should reject unexpected top-level keys."""

        with pytest.raises(ValidationError) as exc_info:
            RunUntilFailureArgs(iterations=5)

        assert "iterations" in str(exc_info.value)

    def test_run_until_failure_accepts_shorthand_steps_and_string_run_args(self):
        """Campaign requests should allow step shorthand and shell-style run args."""

        args = RunUntilFailureArgs(
            setup_steps=["gdb_get_status"],
            run_args='--mode "fast path"',
            capture={"memory_ranges": ["&value:8"]},
        )
        assert args.setup_steps == ["gdb_get_status"]
        assert args.run_args == '--mode "fast path"'
        assert args.capture.memory_ranges == ["&value:8"]


class TestInferiorWorkflowArgs:
    """Test cases for multi-inferior and fork workflow schemas."""

    def test_inferior_select_args(self):
        """Inferior selection should require a positive inferior ID."""

        args = InferiorSelectArgs(session_id=1, inferior_id=2)

        assert args.session_id == 1
        assert args.inferior_id == 2

    def test_follow_fork_mode_args(self):
        """Follow-fork-mode should only accept the supported enum values."""

        args = FollowForkModeArgs(session_id=1, mode="child")

        assert args.session_id == 1
        assert args.mode == "child"

    def test_follow_fork_mode_rejects_unknown_value(self):
        """Unexpected follow-fork-mode values should fail validation."""

        with pytest.raises(ValidationError):
            FollowForkModeArgs(session_id=1, mode="both")

    def test_detach_on_fork_args(self):
        """Detach-on-fork should accept an explicit boolean value."""

        args = DetachOnForkArgs(session_id=1, enabled=False)

        assert args.session_id == 1
        assert args.enabled is False


class TestPhaseSixArgs:
    """Test cases for watchpoint, catchpoint, memory, and wait helpers."""

    def test_set_watchpoint_args_defaults(self):
        """Watchpoints should default to write access."""

        args = SetWatchpointArgs(session_id=1, expression="value")

        assert args.session_id == 1
        assert args.expression == "value"
        assert args.access == "write"

    def test_set_catchpoint_args(self):
        """Catchpoints should accept validated kinds plus an optional argument."""

        args = SetCatchpointArgs(session_id=1, kind="syscall", argument="open")

        assert args.session_id == 1
        assert args.kind == "syscall"
        assert args.argument == "open"
        assert args.temporary is False

    def test_read_memory_args(self):
        """Memory reads should require a positive count and default offset zero."""

        args = ReadMemoryArgs(session_id=1, address="&value", count=16)

        assert args.address == "&value"
        assert args.count == 16
        assert args.offset == 0

    def test_wait_for_stop_args(self):
        """Wait-for-stop requests should default to no reason filter."""

        args = WaitForStopArgs(session_id=1)

        assert args.timeout_sec == 30
        assert args.stop_reasons == []

    def test_capture_memory_range_args(self):
        """Capture memory ranges should accept address, count, offset, and optional name."""

        args = CaptureMemoryRangeArgs(address="&value", count=16, offset=2, name="snapshot")

        assert args.address == "&value"
        assert args.count == 16
        assert args.offset == 2
        assert args.name == "snapshot"


class TestGetBacktraceArgs:
    """Test cases for GetBacktraceArgs model."""

    def test_defaults(self):
        """Test default values."""
        args = GetBacktraceArgs(session_id=1)
        assert args.session_id == 1
        assert args.thread_id is None
        assert args.max_frames == 100

    def test_with_thread_id(self):
        """Test with specific thread ID."""
        args = GetBacktraceArgs(session_id=2, thread_id=5, max_frames=50)
        assert args.session_id == 2
        assert args.thread_id == 5
        assert args.max_frames == 50

    def test_with_numeric_string_thread_id(self):
        """Thread ID should accept numeric strings for client compatibility."""

        args = GetBacktraceArgs(session_id=2, thread_id="5", max_frames=50)
        assert args.thread_id == 5


class TestSetBreakpointArgs:
    """Test cases for SetBreakpointArgs model."""

    def test_location_required(self):
        """Test that location is required."""
        with pytest.raises(ValidationError):
            SetBreakpointArgs()

    def test_minimal_breakpoint(self):
        """Test minimal breakpoint (just location)."""
        args = SetBreakpointArgs(session_id=1, location="main")
        assert args.session_id == 1
        assert args.location == "main"
        assert args.condition is None
        assert args.temporary is False

    def test_conditional_breakpoint(self):
        """Test conditional breakpoint."""
        args = SetBreakpointArgs(
            session_id=2, location="foo.c:42", condition="x > 10", temporary=True
        )
        assert args.session_id == 2
        assert args.location == "foo.c:42"
        assert args.condition == "x > 10"
        assert args.temporary is True


class TestEvaluateExpressionArgs:
    """Test cases for EvaluateExpressionArgs model."""

    def test_expression_required(self):
        """Test that expression is required."""
        with pytest.raises(ValidationError):
            EvaluateExpressionArgs()

    def test_expression(self):
        """Test with expression."""
        args = EvaluateExpressionArgs(session_id=1, expression="x + y", thread_id=2, frame=1)
        assert args.session_id == 1
        assert args.expression == "x + y"
        assert args.thread_id == 2
        assert args.frame == 1

    def test_expression_accepts_numeric_string_context_overrides(self):
        """Thread/frame overrides should accept numeric strings."""

        args = EvaluateExpressionArgs(session_id=1, expression="x + y", thread_id="2", frame="1")
        assert args.thread_id == 2
        assert args.frame == 1


class TestGetVariablesArgs:
    """Test cases for GetVariablesArgs model."""

    def test_defaults(self):
        """Test default values."""
        args = GetVariablesArgs(session_id=1)
        assert args.session_id == 1
        assert args.thread_id is None
        assert args.frame == 0

    def test_with_values(self):
        """Test with specific values."""
        args = GetVariablesArgs(session_id=2, thread_id=3, frame=2)
        assert args.session_id == 2
        assert args.thread_id == 3
        assert args.frame == 2

    def test_with_numeric_string_values(self):
        """Thread/frame selectors should accept numeric strings."""

        args = GetVariablesArgs(session_id=2, thread_id="3", frame="2")
        assert args.thread_id == 3
        assert args.frame == 2


class TestCallFunctionArgs:
    """Test cases for CallFunctionArgs model."""

    def test_function_call_required(self):
        """Test that function_call is required."""
        with pytest.raises(ValidationError):
            CallFunctionArgs()

    def test_function_call_arg(self):
        """Test function_call argument."""
        args = CallFunctionArgs(session_id=1, function_call='printf("hello")', timeout_sec=12)
        assert args.session_id == 1
        assert args.function_call == 'printf("hello")'
        assert args.timeout_sec == 12

    def test_function_call_with_args(self):
        """Test function_call with multiple arguments."""
        args = CallFunctionArgs(session_id=2, function_call='snprintf(buf, 100, "%d", x)')
        assert args.session_id == 2
        assert args.function_call == 'snprintf(buf, 100, "%d", x)'


class TestSessionIdRequired:
    """Test that session_id is required in all tool argument models."""

    def test_execute_command_requires_session_id(self):
        """Test ExecuteCommandArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            ExecuteCommandArgs(command="info threads")
        assert "session_id" in str(exc_info.value)

    def test_get_backtrace_requires_session_id(self):
        """Test GetBacktraceArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            GetBacktraceArgs()
        assert "session_id" in str(exc_info.value)

    def test_set_breakpoint_requires_session_id(self):
        """Test SetBreakpointArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            SetBreakpointArgs(location="main")
        assert "session_id" in str(exc_info.value)

    def test_evaluate_expression_requires_session_id(self):
        """Test EvaluateExpressionArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            EvaluateExpressionArgs(expression="x + y")
        assert "session_id" in str(exc_info.value)

    def test_get_variables_requires_session_id(self):
        """Test GetVariablesArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            GetVariablesArgs()
        assert "session_id" in str(exc_info.value)

    def test_thread_select_requires_session_id(self):
        """Test ThreadSelectArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            ThreadSelectArgs(thread_id=1)
        assert "session_id" in str(exc_info.value)

    def test_frame_select_requires_session_id(self):
        """Test FrameSelectArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            FrameSelectArgs(frame_number=0)
        assert "session_id" in str(exc_info.value)

    def test_breakpoint_number_requires_session_id(self):
        """Test BreakpointNumberArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            BreakpointNumberArgs(number=1)
        assert "session_id" in str(exc_info.value)

    def test_call_function_requires_session_id(self):
        """Test CallFunctionArgs requires session_id."""
        with pytest.raises(ValidationError) as exc_info:
            CallFunctionArgs(function_call='printf("hello")')
        assert "session_id" in str(exc_info.value)

    def test_run_requires_session_id(self):
        """Test RunArgs requires session_id."""

        with pytest.raises(ValidationError) as exc_info:
            RunArgs()
        assert "session_id" in str(exc_info.value)

    def test_attach_requires_session_id(self):
        """Test AttachProcessArgs requires session_id."""

        with pytest.raises(ValidationError) as exc_info:
            AttachProcessArgs(pid=1234)
        assert "session_id" in str(exc_info.value)

    def test_session_id_validation_success(self):
        """Test that models accept session_id correctly."""
        # ExecuteCommandArgs
        args1 = ExecuteCommandArgs(session_id=1, command="info threads")
        assert args1.session_id == 1

        # GetBacktraceArgs
        args2 = GetBacktraceArgs(session_id=2)
        assert args2.session_id == 2

        # SetBreakpointArgs
        args3 = SetBreakpointArgs(session_id=3, location="main")
        assert args3.session_id == 3

        # GetRegistersArgs
        args4 = GetRegistersArgs(session_id=4, thread_id=2, frame=1)
        assert args4.session_id == 4
        assert args4.thread_id == 2
        assert args4.frame == 1

    def test_get_registers_accepts_numeric_string_context(self):
        """Register context overrides should accept numeric strings."""

        args = GetRegistersArgs(session_id=4, thread_id="2", frame="1")
        assert args.session_id == 4
        assert args.thread_id == 2
        assert args.frame == 1

    def test_get_registers_accepts_selector_and_format_options(self):
        """Register requests should validate selectors and rendering options."""

        args = GetRegistersArgs(
            session_id=4,
            register_numbers=["0", 7],
            register_names=["rip", "rax"],
            include_vector_registers=False,
            max_registers=5,
            value_format="natural",
        )
        assert args.register_numbers == [0, 7]
        assert args.register_names == ["rip", "rax"]
        assert args.include_vector_registers is False
        assert args.max_registers == 5
        assert args.value_format == "natural"

    def test_get_registers_rejects_empty_register_name(self):
        """Register-name selectors should reject blank entries."""

        with pytest.raises(ValidationError):
            GetRegistersArgs(session_id=4, register_names=["rip", " "])

    def test_get_registers_rejects_negative_register_number(self):
        """Register-number selectors should require non-negative values."""

        with pytest.raises(ValidationError):
            GetRegistersArgs(session_id=4, register_numbers=[-1])


class TestArgumentBounds:
    """Test numeric bounds for MCP tool argument models."""

    def test_session_id_must_be_positive(self):
        """Session-scoped tools should reject non-positive session IDs."""

        with pytest.raises(ValidationError):
            ExecuteCommandArgs(session_id=0, command="info threads")

    def test_thread_id_must_be_positive(self):
        """Thread selectors should reject non-positive thread IDs."""

        with pytest.raises(ValidationError):
            ThreadSelectArgs(session_id=1, thread_id=0)

    def test_frame_number_must_be_non_negative(self):
        """Frame selectors should reject negative frame indices."""

        with pytest.raises(ValidationError):
            FrameSelectArgs(session_id=1, frame_number=-1)

    def test_max_frames_must_be_positive(self):
        """Backtrace requests should reject non-positive max_frames."""

        with pytest.raises(ValidationError):
            GetBacktraceArgs(session_id=1, max_frames=0)

    def test_breakpoint_number_must_be_positive(self):
        """Breakpoint-number operations should reject non-positive numbers."""

        with pytest.raises(ValidationError):
            BreakpointNumberArgs(session_id=1, number=0)

    def test_variable_frame_must_be_non_negative(self):
        """Variable inspection should reject negative frame indices."""

        with pytest.raises(ValidationError):
            GetVariablesArgs(session_id=1, frame=-1)
