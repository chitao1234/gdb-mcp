"""Shared public contract values for the CLI and MCP schema layers."""

from __future__ import annotations

from typing import Literal, TypeAlias

TOOL_SESSION_START = "gdb_session_start"
TOOL_SESSION_QUERY = "gdb_session_query"
TOOL_SESSION_MANAGE = "gdb_session_manage"
TOOL_INFERIOR_QUERY = "gdb_inferior_query"
TOOL_INFERIOR_MANAGE = "gdb_inferior_manage"
TOOL_EXECUTION_MANAGE = "gdb_execution_manage"
TOOL_BREAKPOINT_QUERY = "gdb_breakpoint_query"
TOOL_BREAKPOINT_MANAGE = "gdb_breakpoint_manage"
TOOL_CONTEXT_QUERY = "gdb_context_query"
TOOL_CONTEXT_MANAGE = "gdb_context_manage"
TOOL_INSPECT_QUERY = "gdb_inspect_query"
TOOL_WORKFLOW_BATCH = "gdb_workflow_batch"
TOOL_CAPTURE_BUNDLE = "gdb_capture_bundle"
TOOL_RUN_UNTIL_FAILURE = "gdb_run_until_failure"
TOOL_EXECUTE_COMMAND = "gdb_execute_command"
TOOL_ATTACH_PROCESS = "gdb_attach_process"
TOOL_CALL_FUNCTION = "gdb_call_function"

PUBLIC_TOOL_NAMES = (
    TOOL_SESSION_START,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INFERIOR_MANAGE,
    TOOL_EXECUTION_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_CONTEXT_MANAGE,
    TOOL_INSPECT_QUERY,
    TOOL_WORKFLOW_BATCH,
    TOOL_CAPTURE_BUNDLE,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_EXECUTE_COMMAND,
    TOOL_ATTACH_PROCESS,
    TOOL_CALL_FUNCTION,
)

BATCH_STEP_TOOL_NAMES = (
    TOOL_SESSION_QUERY,
    TOOL_INFERIOR_QUERY,
    TOOL_INFERIOR_MANAGE,
    TOOL_EXECUTION_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_CONTEXT_MANAGE,
    TOOL_INSPECT_QUERY,
    TOOL_CAPTURE_BUNDLE,
    TOOL_EXECUTE_COMMAND,
    TOOL_ATTACH_PROCESS,
    TOOL_CALL_FUNCTION,
)
BatchStepToolName: TypeAlias = Literal[
    "gdb_session_query",
    "gdb_inferior_query",
    "gdb_inferior_manage",
    "gdb_execution_manage",
    "gdb_breakpoint_query",
    "gdb_breakpoint_manage",
    "gdb_context_query",
    "gdb_context_manage",
    "gdb_inspect_query",
    "gdb_capture_bundle",
    "gdb_execute_command",
    "gdb_attach_process",
    "gdb_call_function",
]

SESSION_QUERY_ACTIONS = ("list", "status")
SessionQueryAction: TypeAlias = Literal["list", "status"]
SESSION_MANAGE_ACTIONS = ("stop",)

INFERIOR_QUERY_ACTIONS = ("list", "current")
InferiorQueryAction: TypeAlias = Literal["list", "current"]
INFERIOR_MANAGE_ACTIONS = (
    "create",
    "remove",
    "select",
    "set_follow_fork_mode",
    "set_detach_on_fork",
)
InferiorManageAction: TypeAlias = Literal[
    "create",
    "remove",
    "select",
    "set_follow_fork_mode",
    "set_detach_on_fork",
]
INFERIOR_FOLLOW_FORK_MODES = ("parent", "child")
InferiorFollowForkMode: TypeAlias = Literal["parent", "child"]

EXECUTION_WAIT_UNTIL_VALUES = ("acknowledged", "stop")
ExecutionWaitUntil: TypeAlias = Literal["acknowledged", "stop"]
EXECUTION_MANAGE_ACTIONS = (
    "run",
    "continue",
    "interrupt",
    "step",
    "next",
    "finish",
    "wait_for_stop",
)
ExecutionManageAction: TypeAlias = Literal[
    "run",
    "continue",
    "interrupt",
    "step",
    "next",
    "finish",
    "wait_for_stop",
]

CONTEXT_QUERY_ACTIONS = ("threads", "backtrace", "frame")
ContextQueryAction: TypeAlias = Literal["threads", "backtrace", "frame"]
CONTEXT_MANAGE_ACTIONS = ("select_thread", "select_frame")
ContextManageAction: TypeAlias = Literal["select_thread", "select_frame"]

BREAKPOINT_KINDS = ("code", "watch", "catch")
BreakpointKind: TypeAlias = Literal["code", "watch", "catch"]
BREAKPOINT_ACCESS_VALUES = ("write", "read", "access")
BreakpointAccess: TypeAlias = Literal["write", "read", "access"]
BREAKPOINT_EVENTS = (
    "throw",
    "rethrow",
    "catch",
    "exec",
    "fork",
    "vfork",
    "load",
    "unload",
    "signal",
    "syscall",
)
BreakpointEvent: TypeAlias = Literal[
    "throw",
    "rethrow",
    "catch",
    "exec",
    "fork",
    "vfork",
    "load",
    "unload",
    "signal",
    "syscall",
]
BREAKPOINT_QUERY_ACTIONS = ("list", "get")
BreakpointQueryAction: TypeAlias = Literal["list", "get"]
BREAKPOINT_MANAGE_ACTIONS = ("create", "update", "delete", "enable", "disable")
BreakpointManageAction: TypeAlias = Literal["create", "update", "delete", "enable", "disable"]
BREAKPOINT_MANAGE_NUMBER_ACTIONS = ("delete", "enable", "disable")
BreakpointManageNumberActionName: TypeAlias = Literal["delete", "enable", "disable"]

LOCATION_KINDS = (
    "current",
    "function",
    "address",
    "address_range",
    "file_line",
    "file_range",
)
LocationKind: TypeAlias = Literal[
    "current",
    "function",
    "address",
    "address_range",
    "file_line",
    "file_range",
]
CLI_LOCATION_KIND_CHOICES = (
    "current",
    "function",
    "address",
    "address-range",
    "file-line",
    "file-range",
)

INSPECT_QUERY_ACTIONS = (
    "evaluate",
    "variables",
    "registers",
    "memory",
    "disassembly",
    "source",
)
InspectQueryAction: TypeAlias = Literal[
    "evaluate",
    "variables",
    "registers",
    "memory",
    "disassembly",
    "source",
]
REGISTER_VALUE_FORMATS = ("hex", "natural")
RegisterValueFormat: TypeAlias = Literal["hex", "natural"]
DISASSEMBLY_MODES = ("assembly", "mixed")
DisassemblyMode: TypeAlias = Literal["assembly", "mixed"]
