"""Pydantic schemas and tool definitions for the MCP layer."""

from __future__ import annotations

from typing import Literal, Optional, TypeAlias

from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field


class StrictArgsModel(BaseModel):
    """Base model for MCP request validation."""

    model_config = ConfigDict(extra="forbid")


BATCH_STEP_TOOL_NAMES = (
    "gdb_execute_command",
    "gdb_run",
    "gdb_attach_process",
    "gdb_get_status",
    "gdb_get_threads",
    "gdb_capture_bundle",
    "gdb_select_thread",
    "gdb_get_backtrace",
    "gdb_select_frame",
    "gdb_get_frame_info",
    "gdb_set_breakpoint",
    "gdb_list_breakpoints",
    "gdb_delete_breakpoint",
    "gdb_enable_breakpoint",
    "gdb_disable_breakpoint",
    "gdb_continue",
    "gdb_step",
    "gdb_next",
    "gdb_interrupt",
    "gdb_evaluate_expression",
    "gdb_get_variables",
    "gdb_get_registers",
    "gdb_call_function",
)
BatchStepToolName: TypeAlias = Literal[
    "gdb_execute_command",
    "gdb_run",
    "gdb_attach_process",
    "gdb_get_status",
    "gdb_get_threads",
    "gdb_capture_bundle",
    "gdb_select_thread",
    "gdb_get_backtrace",
    "gdb_select_frame",
    "gdb_get_frame_info",
    "gdb_set_breakpoint",
    "gdb_list_breakpoints",
    "gdb_delete_breakpoint",
    "gdb_enable_breakpoint",
    "gdb_disable_breakpoint",
    "gdb_continue",
    "gdb_step",
    "gdb_next",
    "gdb_interrupt",
    "gdb_evaluate_expression",
    "gdb_get_variables",
    "gdb_get_registers",
    "gdb_call_function",
]


class StartSessionArgs(StrictArgsModel):
    program: Optional[str] = Field(None, description="Path to executable to debug")
    args: Optional[list[str]] = Field(
        None,
        description="Command-line arguments for the program. Cannot be combined with core.",
    )
    init_commands: Optional[list[str]] = Field(
        None,
        description=(
            "GDB commands to run on startup after environment variables have been applied "
            "(e.g., 'core-file /path/to/core', 'set sysroot /path')"
        ),
    )
    env: Optional[dict[str, str]] = Field(
        None,
        description=(
            "Environment variables to set for the debugged program before init_commands run "
            "(e.g., {'LD_LIBRARY_PATH': '/custom/libs'})"
        ),
    )
    gdb_path: Optional[str] = Field(
        None,
        description="Path to GDB executable (default: from GDB_PATH env var or 'gdb')",
    )
    working_dir: Optional[str] = Field(
        None,
        description=(
            "Working directory to use when starting GDB. "
            "Use this when debugging programs that need to be run from a specific directory, "
            "or when the program expects to find files (config, data, etc.) relative to its working directory. "
            "GDB will be started in this directory. "
            "Example: If debugging a server that loads config from './config.json', set working_dir to the server's directory."
        ),
    )
    core: Optional[str] = Field(
        None,
        description=(
            "Path to core dump file for post-mortem debugging. "
            "When specified, GDB is started with --core flag which properly initializes symbol resolution. "
            "Cannot be combined with args. "
            "IMPORTANT: When using a sysroot with core dumps, set sysroot AFTER the core is loaded "
            "(either via this parameter or core-file command) for symbols to resolve correctly."
        ),
    )


class ExecuteCommandArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    command: str = Field(..., description="GDB command to execute")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class RunArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    args: Optional[list[str]] = Field(None, description="Override inferior arguments for this run")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class AttachProcessArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    pid: int = Field(..., gt=0, description="PID of the process to attach to")
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class GetBacktraceArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: Optional[int] = Field(None, gt=0, description="Thread ID (None for current thread)")
    max_frames: int = Field(100, gt=0, description="Maximum number of frames to retrieve")


class SetBreakpointArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    location: str = Field(..., description="Breakpoint location (function, file:line, or *address)")
    condition: Optional[str] = Field(None, description="Conditional expression")
    temporary: bool = Field(False, description="Whether breakpoint is temporary")


class EvaluateExpressionArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    expression: str = Field(..., description="C/C++ expression to evaluate")
    thread_id: Optional[int] = Field(None, gt=0, description="Thread ID override")
    frame: Optional[int] = Field(None, ge=0, description="Frame number override")


class GetVariablesArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: Optional[int] = Field(None, gt=0, description="Thread ID (None for current)")
    frame: int = Field(0, ge=0, description="Frame number (0 is current)")


class ThreadSelectArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: int = Field(..., gt=0, description="Thread ID to select")


class BreakpointNumberArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    number: int = Field(..., gt=0, description="Breakpoint number")


class FrameSelectArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    frame_number: int = Field(..., ge=0, description="Frame number (0 is current/innermost frame)")


class CallFunctionArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    function_call: str = Field(
        ...,
        description="Function call expression (e.g., 'printf(\"hello\\n\")' or 'my_func(arg1, arg2)')",
    )
    timeout_sec: int = Field(30, gt=0, description="Timeout in seconds")


class GetRegistersArgs(StrictArgsModel):
    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    thread_id: Optional[int] = Field(None, gt=0, description="Thread ID override")
    frame: Optional[int] = Field(None, ge=0, description="Frame number override")


class SessionIdArgs(StrictArgsModel):
    """Arguments for tools that only need session_id."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")


class ListSessionsArgs(StrictArgsModel):
    """Arguments for tools that do not require any parameters."""


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


class BatchArgs(StrictArgsModel):
    """Arguments for executing a structured batch against one live session."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
    steps: list[BatchStepArgs] = Field(..., min_length=1, description="Ordered step list")
    fail_fast: bool = Field(
        True,
        description="Stop executing later steps after the first error result",
    )
    capture_stop_events: bool = Field(
        True,
        description="Include any new stop event produced by each step in the batch result",
    )


class CaptureBundleArgs(StrictArgsModel):
    """Arguments for writing a structured capture bundle to disk."""

    session_id: int = Field(..., gt=0, description="Session ID from gdb_start_session")
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


class RunUntilFailureCaptureArgs(StrictArgsModel):
    """Capture settings used when a run-until-failure campaign matches."""

    enabled: bool = Field(True, description="Capture a forensic bundle when a failure matches.")
    output_dir: Optional[str] = Field(
        None,
        description="Directory in which to place the capture bundle for the matching iteration.",
    )
    bundle_name_prefix: Optional[str] = Field(
        None,
        description="Deterministic bundle name prefix. The iteration number is appended automatically.",
    )
    expressions: list[str] = Field(
        default_factory=list,
        description="Expressions to evaluate and include in the capture bundle.",
    )
    max_frames: int = Field(100, gt=0, description="Maximum frames per thread backtrace.")
    include_threads: bool = Field(True, description="Capture thread inventory.")
    include_backtraces: bool = Field(True, description="Capture backtraces for all threads.")
    include_frame: bool = Field(True, description="Capture the currently selected frame.")
    include_variables: bool = Field(
        True, description="Capture variables for the current selection."
    )
    include_registers: bool = Field(
        True, description="Capture registers for the current selection."
    )
    include_transcript: bool = Field(True, description="Capture the bounded command transcript.")
    include_stop_history: bool = Field(True, description="Capture the bounded stop-event history.")


class RunUntilFailureArgs(StrictArgsModel):
    """Arguments for repeating fresh-session runs until one failure matches."""

    startup: StartSessionArgs = Field(
        default_factory=lambda: StartSessionArgs.model_validate({}),
        description="Session startup configuration used for every iteration.",
    )
    setup_steps: list[BatchStepArgs] = Field(
        default_factory=list,
        description="Optional structured setup steps run after startup and before gdb_run.",
    )
    run_args: Optional[list[str]] = Field(
        None,
        description="Arguments passed to gdb_run for each iteration.",
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


def build_tool_definitions() -> list[Tool]:
    """Build the MCP tool definitions exposed by this server."""

    return [
        Tool(
            name="gdb_start_session",
            description=(
                "Start a new GDB debugging session. Can load an executable, core dump, "
                "or run custom initialization commands. "
                "Automatically detects and reports important warnings such as: "
                "missing debug symbols (not compiled with -g), file not found, or invalid executable. "
                "Check the 'warnings' field in the response for critical issues that may affect debugging. "
                "Available parameters: program (executable path), args (program arguments), "
                "core (core dump path - uses --core flag for proper symbol resolution), "
                "init_commands (GDB commands to run after environment setup), "
                "env (environment variables applied before init_commands), gdb_path (GDB binary path), "
                "working_dir (directory to run program from). "
                "NOTE: 'args' and 'core' are mutually exclusive in one startup request. "
                "The success response includes 'target_loaded' so callers can distinguish "
                "between 'GDB started' and 'requested target actually loaded'. "
                "IMPORTANT for core dump debugging: Set 'sysroot' and 'solib-search-path' AFTER "
                "loading the core (either via 'core' parameter or 'core-file' init_command) "
                "for symbols to resolve correctly. "
                "Returns a session_id integer that must be passed to all other GDB tools."
            ),
            inputSchema=StartSessionArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_execute_command",
            description=(
                "Execute a GDB command. Supports both CLI and MI commands. "
                "CLI commands (like 'info breakpoints', 'list', 'print x') are automatically "
                "handled and their output is formatted for readability. "
                "MI commands (starting with '-', like '-break-list', '-exec-run') return "
                "structured data. "
                "Supports an optional timeout_sec override. "
                "NOTE: For calling functions in the target process, prefer using the dedicated "
                "gdb_call_function tool instead of 'call' command, as it provides better "
                "structured output and can be separately permissioned. "
                "Common examples: 'info breakpoints', 'info threads', 'run', 'print variable', "
                "'list main', 'disassemble func'. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=ExecuteCommandArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_run",
            description=(
                "Run the currently loaded target in a structured way. "
                "Use this instead of raw 'run' text when you want optional argv overrides "
                "and a dedicated tool for launching execution. "
                "If args are provided, they replace the inferior arguments for this run. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=RunArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_attach_process",
            description=(
                "Attach GDB to a running process by PID. "
                "This is a privileged operation that should be separately permissioned from "
                "general command execution when possible. "
                "On success, the attached process is typically paused and inspectable. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=AttachProcessArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_batch",
            description=(
                "Execute a structured sequence of existing session-scoped GDB tools atomically "
                "within one session. "
                "Each step names an existing tool plus tool-specific arguments excluding "
                "session_id, which is inherited from the enclosing batch request. "
                "Use this to combine setup, execution, and inspection steps into one "
                "workflow without interleaving from other requests on the same session. "
                "Supports optional fail_fast behavior and optional per-step stop-event capture."
            ),
            inputSchema=BatchArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_capture_bundle",
            description=(
                "Write a structured forensic capture bundle to disk for the current session. "
                "The bundle includes a manifest plus JSON artifacts such as session status, "
                "last stop event, optional stop history, optional command transcript, thread "
                "inventory, thread backtraces, current frame, variables, registers, and "
                "requested expression evaluations. "
                "Use output_dir and bundle_name when you need deterministic artifact paths."
            ),
            inputSchema=CaptureBundleArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_run_until_failure",
            description=(
                "Run fresh debugger sessions repeatedly until a failure predicate matches or the "
                "iteration limit is reached. "
                "Each iteration uses the same startup configuration, optional structured setup "
                "steps, and one gdb_run invocation. "
                "When a failure matches, the tool can automatically write a capture bundle to "
                "disk and return the bundle metadata."
            ),
            inputSchema=RunUntilFailureArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_get_status",
            description=(
                "Get the current status of the GDB session. "
                "Reports whether the GDB process is still alive, whether a target was "
                "successfully loaded, whether the session still has an active controller, "
                "and the inferior execution state (for example not_started, running, paused, exited). "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_list_sessions",
            description=(
                "List all currently registered GDB sessions with structured metadata. "
                "This is intended for MCP clients that manage multiple sessions and need "
                "an inventory view with lifecycle state, execution state, target info, "
                "and other summary fields."
            ),
            inputSchema=ListSessionsArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_get_threads",
            description=(
                "Get information about all threads in the debugged process, including "
                "thread IDs, states, and the current thread. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_select_thread",
            description=(
                "Select a specific thread to make it the current thread. "
                "After selecting a thread, subsequent commands like gdb_get_backtrace, "
                "gdb_get_variables, and gdb_evaluate_expression will operate on this thread. "
                "Use gdb_get_threads to see available thread IDs. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=ThreadSelectArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_get_backtrace",
            description=(
                "Get the stack backtrace for a specific thread or the current thread. "
                "Shows function calls, file locations, and line numbers. "
                "If thread_id is provided, the original thread/frame selection is restored after the call. "
                "The max_frames parameter is an upper bound on the number of frames returned. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=GetBacktraceArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_select_frame",
            description=(
                "Select a specific stack frame to make it the current frame. "
                "Frame 0 is the innermost (current) frame, higher numbers are outer frames. "
                "After selecting a frame, commands like gdb_get_variables and gdb_evaluate_expression "
                "will operate in the context of that frame. "
                "Use gdb_get_backtrace to see available frames and their numbers. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=FrameSelectArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_get_frame_info",
            description=(
                "Get information about the current stack frame. "
                "Returns details about the currently selected frame including function name, "
                "file location, line number, and address. "
                "Use gdb_select_frame to change the current frame first if needed. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_set_breakpoint",
            description=(
                "Set a breakpoint at a function, file:line, or address. "
                "Supports conditional breakpoints and temporary breakpoints. "
                "Source file paths containing spaces are supported. "
                "Returns breakpoint details including number, address, and location. "
                "Use gdb_list_breakpoints to verify breakpoints were set correctly. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SetBreakpointArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_list_breakpoints",
            description=(
                "List all breakpoints as structured data with detailed information. "
                "Returns an array of breakpoint objects, each containing: number, type, "
                "enabled status, address, function name, source file, line number, and hit count. "
                "Use this to verify breakpoints were set correctly, check which have been hit "
                "(times field), and inspect their exact locations. "
                "Much easier to filter and analyze than text output. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_delete_breakpoint",
            description=(
                "Delete a breakpoint by its number. "
                "Use gdb_list_breakpoints to see breakpoint numbers. "
                "Once deleted, the breakpoint cannot be recovered. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=BreakpointNumberArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_enable_breakpoint",
            description=(
                "Enable a previously disabled breakpoint by its number. "
                "Enabled breakpoints will pause execution when hit. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=BreakpointNumberArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_disable_breakpoint",
            description=(
                "Disable a breakpoint by its number without deleting it. "
                "Disabled breakpoints are not hit but remain in the breakpoint list. "
                "Use gdb_enable_breakpoint to re-enable it later. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=BreakpointNumberArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_continue",
            description=(
                "Continue execution of the program until next breakpoint or completion. "
                "IMPORTANT: Only use this when the program is PAUSED (e.g., at a breakpoint). "
                "If the program hasn't been started yet, use gdb_execute_command with 'run' instead. "
                "If the program is already running, this will fail - use gdb_interrupt to pause it first. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_step",
            description=(
                "Step into the next instruction (enters function calls). "
                "IMPORTANT: Only works when program is PAUSED at a specific location. "
                "Use this for single-stepping through code to debug line-by-line. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_next",
            description=(
                "Step over to the next line (doesn't enter function calls). "
                "IMPORTANT: Only works when program is PAUSED at a specific location. "
                "Use this to step over function calls without entering them. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_interrupt",
            description=(
                "Interrupt (pause) a running program. Use this when: "
                "1) The program is running and hasn't hit a breakpoint, "
                "2) You want to pause execution to inspect state or set breakpoints, "
                "3) The program appears stuck or you want to see where it is. "
                "After interrupting, you can use other commands like gdb_get_backtrace, "
                "gdb_get_variables, or gdb_continue. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_evaluate_expression",
            description=(
                "Evaluate a C/C++ expression in the current context and return its value. "
                "Can access variables, dereference pointers, call functions, etc. "
                "Optional thread_id and frame parameters let callers inspect a specific context "
                "without permanently changing the selected thread or frame. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=EvaluateExpressionArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_get_variables",
            description=(
                "Get local variables for a specific stack frame in a thread. "
                "This is a read-only inspection call: the original thread/frame selection "
                "is restored after the variables are collected. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=GetVariablesArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_get_registers",
            description=(
                "Get CPU register values for the current frame. "
                "Optional thread_id and frame parameters let callers inspect a specific context "
                "without permanently changing the selected thread or frame. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=GetRegistersArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_stop_session",
            description=(
                "Stop the current GDB session and clean up resources. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=SessionIdArgs.model_json_schema(),
        ),
        Tool(
            name="gdb_call_function",
            description=(
                "Call a function in the target process. "
                "WARNING: This is a privileged operation that executes code in the debugged program. "
                "It can call any function accessible in the current context, including: "
                "- Standard library functions: printf, malloc, free, etc. "
                "- Program functions: any function defined in the program "
                "- System calls via wrappers "
                "The function executes with full privileges of the debugged process. "
                "Use with caution as it may have side effects and modify program state. "
                "Supports an optional timeout_sec override. "
                "Examples: 'printf(\"debug: x=%d\\n\", x)', 'my_cleanup_func()', 'strlen(str)'. "
                "Requires session_id parameter (obtained from gdb_start_session)."
            ),
            inputSchema=CallFunctionArgs.model_json_schema(),
        ),
    ]
