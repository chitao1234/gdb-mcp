"""MCP tool registry: the single source of truth for tool models and handlers."""

from __future__ import annotations

from ...contracts import (
    BATCH_STEP_TOOL_NAMES,
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
from ..schemas import (
    AttachProcessArgs,
    BatchArgs,
    BreakpointManageArgs,
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
    StartSessionArgs,
)
from .breakpoint import (
    _handle_breakpoint_manage,
    _handle_breakpoint_query,
)
from .common import (
    ToolSpec,
    context_tool_spec,
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
    _handle_session_manage,
    _handle_session_query,
    _handle_start_session,
)
from .workflow import (
    _handle_batch,
    _handle_capture_bundle,
    _handle_run_until_failure,
)

TOOL_SPECS: dict[str, ToolSpec] = {
    # Registry-scoped tools: no session is pre-resolved; the handler owns it.
    TOOL_SESSION_START: registry_tool_spec(StartSessionArgs, _handle_start_session),
    TOOL_SESSION_CONNECT: registry_tool_spec(ConnectSessionArgs, _handle_connect_session),
    TOOL_SESSION_MANAGE: registry_tool_spec(SessionManageArgs, _handle_session_manage),
    TOOL_RUN_UNTIL_FAILURE: registry_tool_spec(RunUntilFailureArgs, _handle_run_until_failure),
    # Session query is action-polymorphic: list is registry-scoped, status needs
    # a session, so it receives the full context.
    TOOL_SESSION_QUERY: context_tool_spec(SessionQueryArgs, _handle_session_query),
    # Session-scoped tools: the dispatcher resolves the session and holds the lock.
    TOOL_EXECUTE_COMMAND: session_tool_spec(ExecuteCommandArgs, _handle_execute_command),
    TOOL_INFERIOR_QUERY: session_tool_spec(InferiorQueryArgs, _handle_inferior_query),
    TOOL_INFERIOR_MANAGE: session_tool_spec(InferiorManageArgs, _handle_inferior_manage),
    TOOL_EXECUTION_MANAGE: session_tool_spec(ExecutionManageArgs, _handle_execution_manage),
    TOOL_BREAKPOINT_QUERY: session_tool_spec(BreakpointQueryArgs, _handle_breakpoint_query),
    TOOL_BREAKPOINT_MANAGE: session_tool_spec(BreakpointManageArgs, _handle_breakpoint_manage),
    TOOL_CONTEXT_QUERY: session_tool_spec(ContextQueryArgs, _handle_context_query),
    TOOL_CONTEXT_MANAGE: session_tool_spec(ContextManageArgs, _handle_context_manage),
    TOOL_INSPECT_QUERY: session_tool_spec(InspectQueryArgs, _handle_inspect_query),
    TOOL_WORKFLOW_BATCH: session_tool_spec(BatchArgs, _handle_batch),
    TOOL_ATTACH_PROCESS: session_tool_spec(AttachProcessArgs, _handle_attach_process),
    TOOL_CAPTURE_BUNDLE: session_tool_spec(CaptureBundleArgs, _handle_capture_bundle),
    TOOL_CALL_FUNCTION: session_tool_spec(CallFunctionArgs, _handle_call_function),
}

# Batch-executable tools: every tool allowed as a workflow step, plus the batch
# tool itself (rejected as a step but still resolved by the batch machinery).
SESSION_TOOL_SPECS: dict[str, ToolSpec] = {
    name: TOOL_SPECS[name] for name in (*BATCH_STEP_TOOL_NAMES, TOOL_WORKFLOW_BATCH)
}
