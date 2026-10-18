"""Unit tests for MCP schema models."""

import pytest
from pydantic import ValidationError

from gdb_mcp.mcp.schemas import (
    AttachProcessArgs,
    BatchArgs,
    BatchStepArgs,
    BreakpointCatchCreateArgs,
    BreakpointManageArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    CaptureMemoryRangeArgs,
    ExecuteCommandArgs,
    ExecutionControlPayload,
    ExecutionManageArgs,
    ExecutionRunPayload,
    InferiorCreatePayload,
    InferiorManageArgs,
    InspectQueryArgs,
    RunUntilFailureArgs,
    RunUntilFailureCaptureArgs,
    SessionQueryArgs,
    StartSessionArgs,
    build_tool_definitions,
)


class TestStartSessionArgs:
    """Test cases for StartSessionArgs model."""

    def test_minimal_args(self) -> None:
        """Test creating StartSessionArgs with minimal arguments."""
        args = StartSessionArgs()
        assert args.program is None
        assert args.args is None
        assert args.init_commands is None
        assert args.env is None
        assert (
            args.gdb_path is None
        )  # Default to None, actual default determined by GDB_PATH env var or "gdb"

    def test_full_args(self) -> None:
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

    def test_start_session_accepts_shell_style_string_args(self) -> None:
        """Startup args should accept a shell-style string for parity with gdb_run."""

        args = StartSessionArgs(program="/bin/ls", args='--mode "fast path"')
        assert args.args == '--mode "fast path"'

    def test_rr_replay_is_accepted(self) -> None:
        """Startup should accept an rr trace to replay instead of a program."""

        args = StartSessionArgs(rr_replay="/traces/app-0")

        assert args.rr_replay == "/traces/app-0"
        assert args.program is None

    def test_gdb_args_are_accepted(self) -> None:
        """Extra GDB command-line arguments pass through verbatim."""

        args = StartSessionArgs(
            program="/bin/ls",
            gdb_args=["--directory=/tmp/pp", "-iex", "add-auto-load-safe-path /tmp/pp"],
        )

        assert args.gdb_args == [
            "--directory=/tmp/pp",
            "-iex",
            "add-auto-load-safe-path /tmp/pp",
        ]

    def test_gdb_args_reject_replacing_the_interpreter(self) -> None:
        """An argument that would break the MI protocol is rejected."""

        with pytest.raises(ValidationError) as excinfo:
            StartSessionArgs(gdb_args=["--interpreter=console"])

        assert "MI interpreter" in str(excinfo.value)

    def test_gdb_args_reject_a_second_target(self) -> None:
        """Arguments that duplicate this server's own target wiring are rejected."""

        with pytest.raises(ValidationError) as excinfo:
            StartSessionArgs(program="/bin/ls", gdb_args=["--args", "/bin/cat"])

        assert "conflicts with the 'program' and 'args' parameters" in str(excinfo.value)

    def test_gdb_args_reject_bare_arguments(self) -> None:
        """A bare word must be the operand of an option, not a stray program."""

        with pytest.raises(ValidationError) as excinfo:
            StartSessionArgs(gdb_args=["/bin/ls"])

        assert "is a bare argument" in str(excinfo.value)

    def test_env_dict_validation(self) -> None:
        """Test that env accepts dictionary of strings."""
        args = StartSessionArgs(program="/bin/ls", env={"VAR1": "value1", "VAR2": "value2"})

        assert args.env == {"VAR1": "value1", "VAR2": "value2"}

    def test_unknown_field_is_rejected(self) -> None:
        """Unexpected request keys should fail validation instead of being ignored."""

        with pytest.raises(ValidationError) as exc_info:
            # Invalid on purpose: only the raw-payload path can express this.
            StartSessionArgs.model_validate({"program": "/bin/ls", "workingdir": "/tmp/work"})

        assert "workingdir" in str(exc_info.value)


class TestExecuteCommandArgs:
    """Test cases for ExecuteCommandArgs model."""

    def test_command_required(self) -> None:
        """Test that command is required."""
        with pytest.raises(ValidationError):
            # Invalid on purpose: only the raw-payload path can express this.
            ExecuteCommandArgs.model_validate({})

    def test_command_arg(self) -> None:
        """Test command argument."""
        args = ExecuteCommandArgs(session_id=1, command="info threads", timeout_sec=10)
        assert args.session_id == 1
        assert args.command == "info threads"
        assert args.timeout_sec == 10

    def test_unknown_field_is_rejected(self) -> None:
        """Extra command fields should not be silently dropped."""

        with pytest.raises(ValidationError) as exc_info:
            # Invalid on purpose: only the raw-payload path can express this.
            ExecuteCommandArgs.model_validate(
                {"session_id": 1, "command": "info threads", "timeout_seconds": 10}
            )

        assert "timeout_seconds" in str(exc_info.value)


class TestV2ToolDefinitions:
    """Test cases for the clean-break v2 tool inventory."""

    def test_build_tool_definitions_exports_v2_inventory(self) -> None:
        """Tool definitions should only publish the approved v2 surface."""

        tool_names = {tool.name for tool in build_tool_definitions()}

        assert tool_names == {
            "gdb_session_start",
            "gdb_session_connect",
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

    def test_session_query_list_and_status_have_different_shapes(self) -> None:
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

    def test_breakpoint_manage_create_accepts_watchpoint_shape(self) -> None:
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

    def test_breakpoint_manage_update_rejects_missing_changes(self) -> None:
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

    def test_breakpoint_catch_rejects_command_separator_argument(self) -> None:
        """Catch arguments are passed unquoted to GDB, so separators must be rejected."""

        with pytest.raises(ValidationError):
            BreakpointManageArgs.model_validate(
                {
                    "session_id": 4,
                    "action": "create",
                    "breakpoint": {
                        "kind": "catch",
                        "event": "syscall",
                        "argument": "read; shell echo pwned",
                    },
                }
            )

    def test_breakpoint_catch_accepts_multiple_syscall_names(self) -> None:
        """Unless unsafe, argument text may hold several space-separated names."""

        args = BreakpointManageArgs.model_validate(
            {
                "session_id": 4,
                "action": "create",
                "breakpoint": {
                    "kind": "catch",
                    "event": "syscall",
                    "argument": "read write openat",
                },
            }
        )

        breakpoint = args.root.breakpoint
        assert isinstance(breakpoint, BreakpointCatchCreateArgs)
        assert breakpoint.argument == "read write openat"

    def test_inspect_query_source_accepts_file_range_location(self) -> None:
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

    def test_execution_run_accepts_omitted_execution_payload(self) -> None:
        """Run should default its flattened execution payload when omitted."""

        args = ExecutionManageArgs.model_validate({"session_id": 4, "action": "run"})

        execution = args.root.execution
        assert isinstance(execution, ExecutionRunPayload)
        assert execution.wait_until == "stop"
        assert execution.timeout_sec is None
        assert execution.args is None

    def test_execution_continue_accepts_reverse_direction(self) -> None:
        """Continue should accept a reverse direction flag."""

        args = ExecutionManageArgs.model_validate(
            {"session_id": 4, "action": "continue", "execution": {"reverse": True}}
        )

        execution = args.root.execution
        assert isinstance(execution, ExecutionControlPayload)
        assert execution.reverse is True

    def test_execution_run_rejects_reverse_direction(self) -> None:
        """Run has no reverse direction, so the flag is not part of its payload."""

        with pytest.raises(ValidationError):
            ExecutionManageArgs.model_validate(
                {"session_id": 4, "action": "run", "execution": {"reverse": True}}
            )

    def test_execution_continue_accepts_flattened_wait_fields(self) -> None:
        """Continue should accept wait_until and timeout_sec directly."""

        args = ExecutionManageArgs.model_validate(
            {
                "session_id": 4,
                "action": "continue",
                "execution": {"wait_until": "acknowledged", "timeout_sec": 5},
            }
        )

        execution = args.root.execution
        assert isinstance(execution, ExecutionControlPayload)
        assert execution.wait_until == "acknowledged"
        assert execution.timeout_sec == 5

    def test_inferior_create_accepts_omitted_inferior_payload(self) -> None:
        """Inferior creation should default its payload when omitted."""

        args = InferiorManageArgs.model_validate({"session_id": 4, "action": "create"})

        inferior = args.root.inferior
        assert isinstance(inferior, InferiorCreatePayload)
        assert inferior.executable is None
        assert inferior.make_current is False


class TestAttachProcessArgs:
    """Test cases for AttachProcessArgs model."""

    def test_pid_required(self) -> None:
        """Attach requests should require a positive PID."""

        with pytest.raises(ValidationError):
            # Invalid on purpose: only the raw-payload path can express this.
            AttachProcessArgs.model_validate({"session_id": 1})

    def test_attach_args(self) -> None:
        """Attach requests should accept pid and timeout."""

        args = AttachProcessArgs(session_id=1, pid=4321, timeout_sec=15)
        assert args.session_id == 1
        assert args.pid == 4321
        assert args.timeout_sec == 15


class TestBatchArgs:
    """Test cases for structured batch workflows."""

    def test_batch_args_minimal(self) -> None:
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

    def test_batch_args_reject_empty_steps(self) -> None:
        """Batches must include at least one step."""

        with pytest.raises(ValidationError):
            BatchArgs(session_id=1, steps=[])

    def test_batch_step_rejects_unknown_tool(self) -> None:
        """Batch step tools should be validated against the supported allowlist."""

        with pytest.raises(ValidationError) as exc_info:
            BatchStepArgs(tool="gdb_get_status", arguments={})

        assert "gdb_get_status" in str(exc_info.value)

    def test_batch_step_rejects_unknown_field(self) -> None:
        """Batch step definitions should reject unexpected keys."""

        with pytest.raises(ValidationError) as exc_info:
            # Invalid on purpose: only the raw-payload path can express this.
            BatchStepArgs.model_validate(
                {"tool": "gdb_session_query", "arguments": {}, "unexpected": True}
            )

        assert "unexpected" in str(exc_info.value)

    def test_batch_step_accepts_v2_tool_names(self) -> None:
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

    def test_batch_allows_string_step_shorthand(self) -> None:
        """Batch steps should allow shorthand tool-name strings."""

        args = BatchArgs(session_id=1, steps=["gdb_session_query"])
        assert args.steps == ["gdb_session_query"]


class TestCaptureBundleArgs:
    """Test cases for bundle capture requests."""

    def test_capture_bundle_defaults(self) -> None:
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

    def test_capture_bundle_rejects_unknown_field(self) -> None:
        """Capture requests should reject unexpected keys."""

        with pytest.raises(ValidationError) as exc_info:
            # Invalid on purpose: only the raw-payload path can express this.
            CaptureBundleArgs.model_validate({"session_id": 1, "output": "/tmp/out"})

        assert "output" in str(exc_info.value)

    def test_capture_bundle_allows_memory_range_shorthand(self) -> None:
        """Capture requests should allow shorthand memory-range strings."""

        args = CaptureBundleArgs(session_id=1, memory_ranges=["&value:16@2"])
        assert args.memory_ranges == ["&value:16@2"]

    def test_capture_memory_range_args(self) -> None:
        """Capture memory ranges should accept address, count, offset, and optional name."""

        args = CaptureMemoryRangeArgs(address="&value", count=16, offset=2, name="snapshot")

        assert args.address == "&value"
        assert args.count == 16
        assert args.offset == 2
        assert args.name == "snapshot"


class TestRunUntilFailureArgs:
    """Test cases for repeat-until-failure campaigns."""

    def test_run_until_failure_defaults(self) -> None:
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

    def test_run_until_failure_capture_args_defaults(self) -> None:
        """Capture settings should expose deterministic defaults."""

        args = RunUntilFailureCaptureArgs()

        assert args.enabled is True
        assert args.output_dir is None
        assert args.bundle_name_prefix is None
        assert args.bundle_name is None
        assert args.expressions == []
        assert args.memory_ranges == []

    def test_run_until_failure_capture_rejects_conflicting_bundle_fields(self) -> None:
        """Capture naming should reject bundle_name and bundle_name_prefix together."""

        with pytest.raises(ValidationError) as exc_info:
            RunUntilFailureCaptureArgs(bundle_name="exact", bundle_name_prefix="prefix")

        assert "mutually exclusive" in str(exc_info.value)

    def test_run_until_failure_rejects_unknown_field(self) -> None:
        """Campaign requests should reject unexpected top-level keys."""

        with pytest.raises(ValidationError) as exc_info:
            # Invalid on purpose: only the raw-payload path can express this.
            RunUntilFailureArgs.model_validate({"iterations": 5})

        assert "iterations" in str(exc_info.value)

    def test_run_until_failure_accepts_shorthand_steps_and_string_run_args(self) -> None:
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

    def test_function_call_required(self) -> None:
        """Test that function_call is required."""
        with pytest.raises(ValidationError):
            # Invalid on purpose: only the raw-payload path can express this.
            CallFunctionArgs.model_validate({})

    def test_function_call_arg(self) -> None:
        """Test function_call argument."""
        args = CallFunctionArgs(session_id=1, function_call='printf("hello")', timeout_sec=12)
        assert args.session_id == 1
        assert args.function_call == 'printf("hello")'
        assert args.timeout_sec == 12

    def test_function_call_with_args(self) -> None:
        """Test function_call with multiple arguments."""
        args = CallFunctionArgs(session_id=2, function_call='snprintf(buf, 100, "%d", x)')
        assert args.session_id == 2
        assert args.function_call == 'snprintf(buf, 100, "%d", x)'
