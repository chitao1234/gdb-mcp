"""Published MCP tool definitions."""

from __future__ import annotations


from mcp.types import Tool

from ...contracts import (
    TOOL_ATTACH_PROCESS,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_CALL_FUNCTION,
    TOOL_CAPTURE_BUNDLE,
    TOOL_CONTEXT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_EXECUTE_COMMAND,
    TOOL_EXECUTION_MANAGE,
    TOOL_INFERIOR_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INSPECT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)

from ..schema_normalizer import public_input_schema
from ..tool_examples import TOOL_EXAMPLES

from .dedicated import (
    AttachProcessArgs,
    CallFunctionArgs,
    ExecuteCommandArgs,
    StartSessionArgs,
)
from .workflow import (
    BatchArgs,
    CaptureBundleArgs,
    RunUntilFailureArgs,
)
from .session import (
    SessionManageArgs,
    SessionQueryArgs,
)
from .inferior import (
    InferiorManageArgs,
    InferiorQueryArgs,
)
from .execution import ExecutionManageArgs
from .breakpoint import (
    BreakpointManageArgs,
    BreakpointQueryArgs,
)
from .context import (
    ContextManageArgs,
    ContextQueryArgs,
)
from .inspect import InspectQueryArgs


def build_tool_definitions() -> list[Tool]:
    """Build the MCP tool definitions exposed by this server."""

    return [
        Tool(
            name=TOOL_SESSION_START,
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
            inputSchema=public_input_schema(StartSessionArgs),
        ),
        Tool(
            name=TOOL_SESSION_QUERY,
            description=(
                "Query session inventory or inspect one live session. "
                "Use action='list' to enumerate active sessions or action='status' to inspect one session."
            ),
            inputSchema=public_input_schema(
                SessionQueryArgs,
                examples=TOOL_EXAMPLES[TOOL_SESSION_QUERY],
            ),
        ),
        Tool(
            name=TOOL_SESSION_MANAGE,
            description="Mutate session lifecycle state, such as stopping one live session.",
            inputSchema=public_input_schema(SessionManageArgs),
        ),
        Tool(
            name=TOOL_INFERIOR_QUERY,
            description=(
                "Query inferior inventory or the currently selected inferior inside one live session."
            ),
            inputSchema=public_input_schema(InferiorQueryArgs),
        ),
        Tool(
            name=TOOL_INFERIOR_MANAGE,
            description=(
                "Create, remove, select, or reconfigure inferiors and fork-follow settings."
            ),
            inputSchema=public_input_schema(
                InferiorManageArgs,
                examples=TOOL_EXAMPLES[TOOL_INFERIOR_MANAGE],
            ),
        ),
        Tool(
            name=TOOL_EXECUTION_MANAGE,
            description=(
                "Run, continue, interrupt, step, next, finish, or wait for stop events "
                "using action-scoped execution payloads."
            ),
            inputSchema=public_input_schema(
                ExecutionManageArgs,
                examples=TOOL_EXAMPLES[TOOL_EXECUTION_MANAGE],
            ),
        ),
        Tool(
            name=TOOL_BREAKPOINT_QUERY,
            description="List breakpoints or fetch one breakpoint record by number.",
            inputSchema=public_input_schema(BreakpointQueryArgs),
        ),
        Tool(
            name=TOOL_BREAKPOINT_MANAGE,
            description=(
                "Create, delete, enable, disable, or update code breakpoints, watchpoints, "
                "and catchpoints through one action-based tool."
            ),
            inputSchema=public_input_schema(
                BreakpointManageArgs,
                examples=TOOL_EXAMPLES[TOOL_BREAKPOINT_MANAGE],
            ),
        ),
        Tool(
            name=TOOL_CONTEXT_QUERY,
            description="List threads or inspect backtraces and frame information.",
            inputSchema=public_input_schema(ContextQueryArgs),
        ),
        Tool(
            name=TOOL_CONTEXT_MANAGE,
            description="Select the current thread or frame in one live session.",
            inputSchema=public_input_schema(ContextManageArgs),
        ),
        Tool(
            name=TOOL_INSPECT_QUERY,
            description=(
                "Evaluate expressions and inspect variables, registers, memory, source context, "
                "or disassembly without using raw debugger commands."
            ),
            inputSchema=public_input_schema(
                InspectQueryArgs,
                examples=TOOL_EXAMPLES[TOOL_INSPECT_QUERY],
            ),
        ),
        Tool(
            name=TOOL_WORKFLOW_BATCH,
            description=(
                "Execute a structured sequence of session-scoped v2 GDB tools atomically within one session. "
                "Each step names an existing tool plus tool-specific arguments excluding "
                "session_id, which is inherited from the enclosing batch request."
            ),
            inputSchema=public_input_schema(
                BatchArgs,
                examples=TOOL_EXAMPLES[TOOL_WORKFLOW_BATCH],
            ),
        ),
        Tool(
            name=TOOL_CAPTURE_BUNDLE,
            description=(
                "Write a structured forensic capture bundle to disk for the current session. "
                "The bundle includes a manifest plus JSON artifacts such as session status, "
                "last stop event, optional stop history, optional command transcript, thread "
                "inventory, thread backtraces, current frame, variables, registers, requested "
                "expression evaluations, and any explicitly requested memory ranges. "
                "Use output_dir and bundle_name when you need deterministic artifact paths."
            ),
            inputSchema=public_input_schema(
                CaptureBundleArgs,
                examples=TOOL_EXAMPLES[TOOL_CAPTURE_BUNDLE],
            ),
        ),
        Tool(
            name=TOOL_RUN_UNTIL_FAILURE,
            description=(
                "Run fresh debugger sessions repeatedly until a failure predicate matches or the "
                "iteration limit is reached. "
                "Each iteration uses the same startup configuration, optional structured setup "
                "steps, and one gdb_execution_manage(action='run') invocation. "
                "When a failure matches, the tool can automatically write a capture bundle to "
                "disk and return the bundle metadata, including any explicitly requested memory "
                "ranges."
            ),
            inputSchema=public_input_schema(
                RunUntilFailureArgs,
                examples=TOOL_EXAMPLES[TOOL_RUN_UNTIL_FAILURE],
            ),
        ),
        Tool(
            name=TOOL_EXECUTE_COMMAND,
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
                "Requires session_id parameter (obtained from gdb_session_start)."
            ),
            inputSchema=public_input_schema(ExecuteCommandArgs),
        ),
        Tool(
            name=TOOL_ATTACH_PROCESS,
            description=(
                "Attach GDB to a running process by PID. "
                "This is a privileged operation that should be separately permissioned from "
                "general command execution when possible. "
                "On success, the attached process is typically paused and inspectable. "
                "Requires session_id parameter (obtained from gdb_session_start)."
            ),
            inputSchema=public_input_schema(AttachProcessArgs),
        ),
        Tool(
            name=TOOL_CALL_FUNCTION,
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
                "Requires session_id parameter (obtained from gdb_session_start)."
            ),
            inputSchema=public_input_schema(CallFunctionArgs),
        ),
    ]
