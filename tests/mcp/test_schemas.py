"""Unit tests for MCP schema models."""

import pytest
from pydantic import ValidationError
from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    BatchArgs,
    BatchStepArgs,
    BreakpointManageArgs,
    CallFunctionArgs,
    CaptureMemoryRangeArgs,
    CaptureBundleArgs,
    ExecutionManageArgs,
    ExecuteCommandArgs,
    InspectQueryArgs,
    InferiorManageArgs,
    SessionQueryArgs,
    RunUntilFailureArgs,
    RunUntilFailureCaptureArgs,
    StartSessionArgs,
    build_tool_definitions,
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


class TestV2ToolDefinitions:
    """Test cases for the clean-break v2 tool inventory."""

    def test_build_tool_definitions_exports_v2_inventory(self):
        """Tool definitions should only publish the approved v2 surface."""

        tool_names = {tool.name for tool in build_tool_definitions()}

        assert tool_names == {
            "gdb_session_start",
            "gdb_session_query",
            "gdb_session_manage",
            "gdb_inferior_query",
            "gdb_inferior_manage",
            "gdb_execution_manage",
            "gdb_breakpoint_query",
            "gdb_breakpoint_manage",
            "gdb_context_query",
            "gdb_context_manage",
            "gdb_inspect_query",
            "gdb_workflow_batch",
            "gdb_capture_bundle",
            "gdb_run_until_failure",
            "gdb_execute_command",
            "gdb_attach_process",
            "gdb_call_function",
        }


class TestV2ActionArgs:
    """Test cases for the consolidated v2 action models."""

    def test_session_query_list_and_status_have_different_shapes(self):
        """Session query should discriminate between global list and per-session status."""

        list_args = SessionQueryArgs.model_validate({"action": "list", "query": {}})
        status_args = SessionQueryArgs.model_validate(
            {"session_id": 3, "action": "status", "query": {}}
        )

        assert list_args.root.action == "list"
        assert status_args.root.action == "status"
        assert status_args.root.session_id == 3

        with pytest.raises(ValidationError):
            SessionQueryArgs.model_validate({"action": "status", "query": {}})

    def test_breakpoint_manage_create_accepts_watchpoint_shape(self):
        """Breakpoint manage create should accept watchpoint payloads."""

        args = BreakpointManageArgs.model_validate(
            {
                "session_id": 4,
                "action": "create",
                "breakpoint": {
                    "kind": "watch",
                    "expression": "state->ready",
                    "access": "read",
                },
            }
        )

        assert args.root.action == "create"
        assert args.root.breakpoint.kind == "watch"
        assert args.root.breakpoint.access == "read"

    def test_breakpoint_manage_update_rejects_missing_changes(self):
        """Breakpoint update should require at least one requested change."""

        with pytest.raises(ValidationError):
            BreakpointManageArgs.model_validate(
                {
                    "session_id": 4,
                    "action": "update",
                    "breakpoint": {
                        "number": 2,
                    },
                    "changes": {},
                }
            )

    def test_inspect_query_source_accepts_file_range_location(self):
        """Inspect source should accept a file-range location selector."""

        args = InspectQueryArgs.model_validate(
            {
                "session_id": 9,
                "action": "source",
                "query": {
                    "location": {
                        "kind": "file_range",
                        "file": "main.c",
                        "start_line": 10,
                        "end_line": 12,
                    },
                    "context_before": 0,
                    "context_after": 0,
                },
            }
        )

        assert args.root.action == "source"
        assert args.root.query.location.kind == "file_range"

    def test_execution_run_accepts_omitted_execution_payload(self):
        """Run should default its flattened execution payload when omitted."""

        args = ExecutionManageArgs.model_validate({"session_id": 4, "action": "run"})

        assert args.root.execution.wait_until == "stop"
        assert args.root.execution.timeout_sec is None
        assert args.root.execution.args is None

    def test_execution_continue_accepts_flattened_wait_fields(self):
        """Continue should accept wait_until and timeout_sec directly."""

        args = ExecutionManageArgs.model_validate(
            {
                "session_id": 4,
                "action": "continue",
                "execution": {"wait_until": "acknowledged", "timeout_sec": 5},
            }
        )

        assert args.root.execution.wait_until == "acknowledged"
        assert args.root.execution.timeout_sec == 5

    def test_inferior_create_accepts_omitted_inferior_payload(self):
        """Inferior creation should default its payload when omitted."""

        args = InferiorManageArgs.model_validate({"session_id": 4, "action": "create"})

        assert args.root.inferior.executable is None
        assert args.root.inferior.make_current is False


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


class TestBatchArgs:
    """Test cases for structured batch workflows."""

    def test_batch_args_minimal(self):
        """Batch requests should accept one valid step."""

        args = BatchArgs(
            session_id=1,
            steps=[
                BatchStepArgs(
                    tool="gdb_breakpoint_manage",
                    arguments={
                        "action": "disable",
                        "breakpoint": {"number": 3},
                    },
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
            BatchStepArgs(tool="gdb_get_status", arguments={})

        assert "gdb_get_status" in str(exc_info.value)

    def test_batch_step_rejects_unknown_field(self):
        """Batch step definitions should reject unexpected keys."""

        with pytest.raises(ValidationError) as exc_info:
            BatchStepArgs(tool="gdb_session_query", arguments={}, unexpected=True)

        assert "unexpected" in str(exc_info.value)

    def test_batch_step_accepts_v2_tool_names(self):
        """Batch steps should accept the consolidated v2 tool inventory."""

        step = BatchStepArgs(
            tool="gdb_breakpoint_manage",
            arguments={
                "action": "disable",
                "breakpoint": {"number": 3},
            },
        )

        assert step.tool == "gdb_breakpoint_manage"
        assert step.arguments == {
            "action": "disable",
            "breakpoint": {"number": 3},
        }

    def test_batch_allows_string_step_shorthand(self):
        """Batch steps should allow shorthand tool-name strings."""

        args = BatchArgs(session_id=1, steps=["gdb_session_query"])
        assert args.steps == ["gdb_session_query"]


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

    def test_capture_memory_range_args(self):
        """Capture memory ranges should accept address, count, offset, and optional name."""

        args = CaptureMemoryRangeArgs(address="&value", count=16, offset=2, name="snapshot")

        assert args.address == "&value"
        assert args.count == 16
        assert args.offset == 2
        assert args.name == "snapshot"


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
            setup_steps=["gdb_session_query"],
            run_args='--mode "fast path"',
            capture={"memory_ranges": ["&value:8"]},
        )
        assert args.setup_steps == ["gdb_session_query"]
        assert args.run_args == '--mode "fast path"'
        assert args.capture.memory_ranges == ["&value:8"]


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
