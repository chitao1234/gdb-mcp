"""MCP tool registry: the single source of truth for tool models and handlers."""

from __future__ import annotations

from typing import cast

from ... import contracts as c
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
    TOOL_SESSION_CONNECT,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)
from ...session.backends import BackendOperation
from ..schemas import (
    AttachProcessArgs,
    BatchArgs,
    BreakpointManageArgs,
    BreakpointManageCreateAction,
    BreakpointQueryArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ConnectSessionArgs,
    ContextManageArgs,
    ContextQueryArgs,
    ExecuteCommandArgs,
    ExecutionManageArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    InspectQueryArgs,
    RunUntilFailureArgs,
    SessionManageArgs,
    SessionQueryArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    StartSessionArgs,
)
from .breakpoint import (
    _handle_breakpoint_manage,
    _handle_breakpoint_query,
)
from .common import (
    RegisteredToolSpec,
    SessionToolInvocation,
    ToolInvocation,
    registry_tool_spec,
    session_tool_spec,
)
from .context import (
    _handle_context_manage,
    _handle_context_query,
)
from .dedicated import (
    _handle_attach_process,
    _handle_call_function,
    _handle_execute_command,
)
from .execution import _handle_execution_manage
from .inferior import (
    _handle_inferior_manage,
    _handle_inferior_query,
)
from .inspect import _handle_inspect_query
from .session import (
    _handle_connect_session,
    _handle_session_list,
    _handle_session_manage,
    _handle_session_status,
    _handle_start_session,
)
from .workflow import (
    _handle_batch,
    _handle_capture_bundle,
    _handle_run_until_failure,
)


def _breakpoint_operation(args: BreakpointManageArgs) -> BackendOperation:
    request = args.root
    if isinstance(request, BreakpointManageCreateAction):
        creations: dict[str, BackendOperation] = {
            c.BREAKPOINT_KIND_CODE: "set_breakpoint",
            c.BREAKPOINT_KIND_WATCH: "set_watchpoint",
            c.BREAKPOINT_KIND_CATCH: "set_catchpoint",
        }
        return creations[request.breakpoint.kind]
    mutations: dict[str, BackendOperation] = {
        c.ACTION_UPDATE: "update_breakpoint",
        c.ACTION_DELETE: "delete_breakpoint",
        c.ACTION_ENABLE: "enable_breakpoint",
        c.ACTION_DISABLE: "disable_breakpoint",
    }
    return mutations[request.action]


_INFERIOR_OPERATIONS: dict[str, BackendOperation] = {
    c.ACTION_CREATE: "add_inferior",
    c.ACTION_REMOVE: "remove_inferior",
    c.ACTION_SELECT: "select_inferior",
    c.ACTION_SET_FOLLOW_FORK_MODE: "set_follow_fork_mode",
    c.ACTION_SET_DETACH_ON_FORK: "set_detach_on_fork",
}

# Batch-executable tools: every tool allowed as a workflow step, plus the batch
# tool itself (rejected as a step but still resolved by the batch machinery).
SESSION_TOOL_SPECS: dict[str, RegisteredToolSpec[SessionToolInvocation]] = {
    TOOL_SESSION_QUERY: session_tool_spec(
        SessionQueryStatusAction,
        _handle_session_status,
        session_id=lambda args: args.session_id,
        action=lambda args: args.action,
        operation=lambda args: "status",
    ),
    TOOL_EXECUTE_COMMAND: session_tool_spec(
        ExecuteCommandArgs,
        _handle_execute_command,
        session_id=lambda args: args.session_id,
        operation=lambda args: "command",
    ),
    TOOL_INFERIOR_QUERY: session_tool_spec(
        InferiorQueryArgs,
        _handle_inferior_query,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: "list_inferiors",
    ),
    TOOL_INFERIOR_MANAGE: session_tool_spec(
        InferiorManageArgs,
        _handle_inferior_manage,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: _INFERIOR_OPERATIONS[args.root.action],
    ),
    TOOL_EXECUTION_MANAGE: session_tool_spec(
        ExecutionManageArgs,
        _handle_execution_manage,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: (
            "return_from_frame"
            if args.root.action == c.ACTION_RETURN
            else cast(BackendOperation, args.root.action)
        ),
    ),
    TOOL_BREAKPOINT_QUERY: session_tool_spec(
        BreakpointQueryArgs,
        _handle_breakpoint_query,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: (
            "list_breakpoints" if args.root.action == c.ACTION_LIST else "get_breakpoint"
        ),
    ),
    TOOL_BREAKPOINT_MANAGE: session_tool_spec(
        BreakpointManageArgs,
        _handle_breakpoint_manage,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=_breakpoint_operation,
    ),
    TOOL_CONTEXT_QUERY: session_tool_spec(
        ContextQueryArgs,
        _handle_context_query,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: (
            "frame_info"
            if args.root.action == c.ACTION_FRAME
            else cast(BackendOperation, args.root.action)
        ),
    ),
    TOOL_CONTEXT_MANAGE: session_tool_spec(
        ContextManageArgs,
        _handle_context_manage,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: cast(BackendOperation, args.root.action),
    ),
    TOOL_INSPECT_QUERY: session_tool_spec(
        InspectQueryArgs,
        _handle_inspect_query,
        session_id=lambda args: args.root.session_id,
        action=lambda args: args.root.action,
        operation=lambda args: (
            "read_memory"
            if args.root.action == c.ACTION_MEMORY
            else (
                "disassemble"
                if args.root.action == c.ACTION_DISASSEMBLY
                else cast(BackendOperation, args.root.action)
            )
        ),
    ),
    TOOL_WORKFLOW_BATCH: session_tool_spec(
        BatchArgs, _handle_batch, session_id=lambda args: args.session_id
    ),
    TOOL_ATTACH_PROCESS: session_tool_spec(
        AttachProcessArgs,
        _handle_attach_process,
        session_id=lambda args: args.session_id,
        operation=lambda args: "attach",
    ),
    TOOL_CAPTURE_BUNDLE: session_tool_spec(
        CaptureBundleArgs, _handle_capture_bundle, session_id=lambda args: args.session_id
    ),
    TOOL_CALL_FUNCTION: session_tool_spec(
        CallFunctionArgs,
        _handle_call_function,
        session_id=lambda args: args.session_id,
        operation=lambda args: "call_function",
    ),
}

_SESSION_LIST_SPEC = registry_tool_spec(
    SessionQueryListAction, _handle_session_list, action=lambda args: args.action
)


def _prepare_session_query(arguments: object) -> ToolInvocation:
    """Select the invocation scope using the validated query variant."""

    request = SessionQueryArgs.model_validate(arguments).root
    match request:
        case SessionQueryListAction():
            return _SESSION_LIST_SPEC.prepare(request)
        case SessionQueryStatusAction():
            return SESSION_TOOL_SPECS[TOOL_SESSION_QUERY].prepare(request)


TOOL_SPECS: dict[str, RegisteredToolSpec[ToolInvocation]] = {
    **SESSION_TOOL_SPECS,
    TOOL_SESSION_START: registry_tool_spec(StartSessionArgs, _handle_start_session),
    TOOL_SESSION_CONNECT: registry_tool_spec(ConnectSessionArgs, _handle_connect_session),
    TOOL_SESSION_MANAGE: registry_tool_spec(
        SessionManageArgs, _handle_session_manage, action=lambda args: args.root.action
    ),
    TOOL_RUN_UNTIL_FAILURE: registry_tool_spec(RunUntilFailureArgs, _handle_run_until_failure),
    TOOL_SESSION_QUERY: RegisteredToolSpec(
        model=SessionQueryArgs, scope="registry", prepare=_prepare_session_query
    ),
}
