"""Session-scoped tool registry."""

from __future__ import annotations


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
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
)
from ..schemas import (
    AttachProcessArgs,
    BatchArgs,
    BreakpointManageArgs,
    BreakpointQueryArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ContextManageArgs,
    ContextQueryArgs,
    ExecutionManageArgs,
    ExecuteCommandArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    InspectQueryArgs,
    SessionQueryArgs,
)
from .common import (
    SessionToolSpec,
    session_tool_spec,
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
from .breakpoint import (
    _handle_breakpoint_manage,
    _handle_breakpoint_query,
)
from .context import (
    _handle_context_manage,
    _handle_context_query,
)
from .inspect import _handle_inspect_query
from .session import _handle_session_query_for_session
from .workflow import (
    _handle_batch,
    _handle_capture_bundle,
)

SESSION_TOOL_SPECS: dict[str, SessionToolSpec] = {
    TOOL_EXECUTE_COMMAND: session_tool_spec(ExecuteCommandArgs, _handle_execute_command),
    TOOL_SESSION_QUERY: session_tool_spec(SessionQueryArgs, _handle_session_query_for_session),
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
