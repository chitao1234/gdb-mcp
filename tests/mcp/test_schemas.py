"""Unit tests for MCP schema models."""

import pytest
from pydantic import ValidationError
from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    BatchArgs,
    BatchStepArgs,
    BreakpointNumberArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    StartSessionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetRegistersArgs,
    ThreadSelectArgs,
    SetBreakpointArgs,
    EvaluateExpressionArgs,
    GetVariablesArgs,
    ListSessionsArgs,
    RunArgs,
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


class TestCaptureBundleArgs:
    """Test cases for bundle capture requests."""

    def test_capture_bundle_defaults(self):
        """Capture requests should provide sensible defaults."""

        args = CaptureBundleArgs(session_id=1)

        assert args.session_id == 1
        assert args.output_dir is None
        assert args.bundle_name is None
        assert args.expressions == []
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
