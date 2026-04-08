"""Shared public contract values for the CLI and MCP schema layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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

ACTION_LIST = "list"
ActionListName: TypeAlias = Literal["list"]
ACTION_STATUS = "status"
ActionStatusName: TypeAlias = Literal["status"]
ACTION_STOP = "stop"
ActionStopName: TypeAlias = Literal["stop"]
ACTION_CURRENT = "current"
ActionCurrentName: TypeAlias = Literal["current"]
ACTION_CREATE = "create"
ActionCreateName: TypeAlias = Literal["create"]
ACTION_REMOVE = "remove"
ActionRemoveName: TypeAlias = Literal["remove"]
ACTION_SELECT = "select"
ActionSelectName: TypeAlias = Literal["select"]
ACTION_SET_FOLLOW_FORK_MODE = "set_follow_fork_mode"
ActionSetFollowForkModeName: TypeAlias = Literal["set_follow_fork_mode"]
ACTION_SET_DETACH_ON_FORK = "set_detach_on_fork"
ActionSetDetachOnForkName: TypeAlias = Literal["set_detach_on_fork"]
ACTION_RUN = "run"
ActionRunName: TypeAlias = Literal["run"]
ACTION_CONTINUE = "continue"
ActionContinueName: TypeAlias = Literal["continue"]
ACTION_INTERRUPT = "interrupt"
ActionInterruptName: TypeAlias = Literal["interrupt"]
ACTION_STEP = "step"
ActionStepName: TypeAlias = Literal["step"]
ACTION_NEXT = "next"
ActionNextName: TypeAlias = Literal["next"]
ACTION_FINISH = "finish"
ActionFinishName: TypeAlias = Literal["finish"]
ACTION_WAIT_FOR_STOP = "wait_for_stop"
ActionWaitForStopName: TypeAlias = Literal["wait_for_stop"]
ACTION_UPDATE = "update"
ActionUpdateName: TypeAlias = Literal["update"]
ACTION_DELETE = "delete"
ActionDeleteName: TypeAlias = Literal["delete"]
ACTION_ENABLE = "enable"
ActionEnableName: TypeAlias = Literal["enable"]
ACTION_DISABLE = "disable"
ActionDisableName: TypeAlias = Literal["disable"]
ACTION_GET = "get"
ActionGetName: TypeAlias = Literal["get"]
ACTION_THREADS = "threads"
ActionThreadsName: TypeAlias = Literal["threads"]
ACTION_BACKTRACE = "backtrace"
ActionBacktraceName: TypeAlias = Literal["backtrace"]
ACTION_FRAME = "frame"
ActionFrameName: TypeAlias = Literal["frame"]
ACTION_SELECT_THREAD = "select_thread"
ActionSelectThreadName: TypeAlias = Literal["select_thread"]
ACTION_SELECT_FRAME = "select_frame"
ActionSelectFrameName: TypeAlias = Literal["select_frame"]
ACTION_EVALUATE = "evaluate"
ActionEvaluateName: TypeAlias = Literal["evaluate"]
ACTION_VARIABLES = "variables"
ActionVariablesName: TypeAlias = Literal["variables"]
ACTION_REGISTERS = "registers"
ActionRegistersName: TypeAlias = Literal["registers"]
ACTION_MEMORY = "memory"
ActionMemoryName: TypeAlias = Literal["memory"]
ACTION_DISASSEMBLY = "disassembly"
ActionDisassemblyName: TypeAlias = Literal["disassembly"]
ACTION_SOURCE = "source"
ActionSourceName: TypeAlias = Literal["source"]

BREAKPOINT_KIND_CODE = "code"
BreakpointKindCodeName: TypeAlias = Literal["code"]
BREAKPOINT_KIND_WATCH = "watch"
BreakpointKindWatchName: TypeAlias = Literal["watch"]
BREAKPOINT_KIND_CATCH = "catch"
BreakpointKindCatchName: TypeAlias = Literal["catch"]

LOCATION_KIND_CURRENT = "current"
LocationKindCurrentName: TypeAlias = Literal["current"]
LOCATION_KIND_FUNCTION = "function"
LocationKindFunctionName: TypeAlias = Literal["function"]
LOCATION_KIND_ADDRESS = "address"
LocationKindAddressName: TypeAlias = Literal["address"]
LOCATION_KIND_ADDRESS_RANGE = "address_range"
LocationKindAddressRangeName: TypeAlias = Literal["address_range"]
LOCATION_KIND_FILE_LINE = "file_line"
LocationKindFileLineName: TypeAlias = Literal["file_line"]
LOCATION_KIND_FILE_RANGE = "file_range"
LocationKindFileRangeName: TypeAlias = Literal["file_range"]

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

SESSION_QUERY_ACTIONS = (ACTION_LIST, ACTION_STATUS)
SessionQueryAction: TypeAlias = Literal["list", "status"]
SESSION_MANAGE_ACTIONS = (ACTION_STOP,)

INFERIOR_QUERY_ACTIONS = (ACTION_LIST, ACTION_CURRENT)
InferiorQueryAction: TypeAlias = Literal["list", "current"]
INFERIOR_MANAGE_ACTIONS = (
    ACTION_CREATE,
    ACTION_REMOVE,
    ACTION_SELECT,
    ACTION_SET_FOLLOW_FORK_MODE,
    ACTION_SET_DETACH_ON_FORK,
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
    ACTION_RUN,
    ACTION_CONTINUE,
    ACTION_INTERRUPT,
    ACTION_STEP,
    ACTION_NEXT,
    ACTION_FINISH,
    ACTION_WAIT_FOR_STOP,
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

CONTEXT_QUERY_ACTIONS = (ACTION_THREADS, ACTION_BACKTRACE, ACTION_FRAME)
ContextQueryAction: TypeAlias = Literal["threads", "backtrace", "frame"]
CONTEXT_MANAGE_ACTIONS = (ACTION_SELECT_THREAD, ACTION_SELECT_FRAME)
ContextManageAction: TypeAlias = Literal["select_thread", "select_frame"]

BREAKPOINT_KINDS = (BREAKPOINT_KIND_CODE, BREAKPOINT_KIND_WATCH, BREAKPOINT_KIND_CATCH)
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
BREAKPOINT_QUERY_ACTIONS = (ACTION_LIST, ACTION_GET)
BreakpointQueryAction: TypeAlias = Literal["list", "get"]
BREAKPOINT_MANAGE_ACTIONS = (
    ACTION_CREATE,
    ACTION_UPDATE,
    ACTION_DELETE,
    ACTION_ENABLE,
    ACTION_DISABLE,
)
BreakpointManageAction: TypeAlias = Literal["create", "update", "delete", "enable", "disable"]
BREAKPOINT_MANAGE_NUMBER_ACTIONS = (ACTION_DELETE, ACTION_ENABLE, ACTION_DISABLE)
BreakpointManageNumberActionName: TypeAlias = Literal["delete", "enable", "disable"]

LOCATION_KINDS = (
    LOCATION_KIND_CURRENT,
    LOCATION_KIND_FUNCTION,
    LOCATION_KIND_ADDRESS,
    LOCATION_KIND_ADDRESS_RANGE,
    LOCATION_KIND_FILE_LINE,
    LOCATION_KIND_FILE_RANGE,
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
    ACTION_EVALUATE,
    ACTION_VARIABLES,
    ACTION_REGISTERS,
    ACTION_MEMORY,
    ACTION_DISASSEMBLY,
    ACTION_SOURCE,
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

WorkflowStepValidationKind: TypeAlias = Literal[
    "session_id_not_allowed",
    "session_query_list_not_allowed",
    "session_manage_not_allowed",
    "nested_workflow_tool_not_allowed",
    "unknown_tool",
]


@dataclass(frozen=True, slots=True)
class WorkflowStepValidationIssue:
    kind: WorkflowStepValidationKind


def validate_workflow_step_contract(
    tool_name: str,
    arguments: Mapping[str, object],
) -> WorkflowStepValidationIssue | None:
    if "session_id" in arguments:
        return WorkflowStepValidationIssue(
            kind="session_id_not_allowed",
        )
    if tool_name == TOOL_SESSION_QUERY and arguments.get("action") == ACTION_LIST:
        return WorkflowStepValidationIssue(
            kind="session_query_list_not_allowed",
        )
    if tool_name == TOOL_SESSION_MANAGE:
        return WorkflowStepValidationIssue(
            kind="session_manage_not_allowed",
        )
    if tool_name in {TOOL_WORKFLOW_BATCH, TOOL_RUN_UNTIL_FAILURE}:
        return WorkflowStepValidationIssue(
            kind="nested_workflow_tool_not_allowed",
        )
    if tool_name not in BATCH_STEP_TOOL_NAMES:
        return WorkflowStepValidationIssue(
            kind="unknown_tool",
        )
    return None
