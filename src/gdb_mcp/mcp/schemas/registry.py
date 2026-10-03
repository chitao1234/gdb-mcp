"""Tool name to request model registries."""

from __future__ import annotations


from pydantic import (
    BaseModel,
)

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
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)

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

TOOL_MODELS: dict[str, type[BaseModel]] = {
    TOOL_SESSION_START: StartSessionArgs,
    TOOL_SESSION_QUERY: SessionQueryArgs,
    TOOL_SESSION_MANAGE: SessionManageArgs,
    TOOL_INFERIOR_QUERY: InferiorQueryArgs,
    TOOL_INFERIOR_MANAGE: InferiorManageArgs,
    TOOL_EXECUTION_MANAGE: ExecutionManageArgs,
    TOOL_BREAKPOINT_QUERY: BreakpointQueryArgs,
    TOOL_BREAKPOINT_MANAGE: BreakpointManageArgs,
    TOOL_CONTEXT_QUERY: ContextQueryArgs,
    TOOL_CONTEXT_MANAGE: ContextManageArgs,
    TOOL_INSPECT_QUERY: InspectQueryArgs,
    TOOL_WORKFLOW_BATCH: BatchArgs,
    TOOL_CAPTURE_BUNDLE: CaptureBundleArgs,
    TOOL_RUN_UNTIL_FAILURE: RunUntilFailureArgs,
    TOOL_EXECUTE_COMMAND: ExecuteCommandArgs,
    TOOL_ATTACH_PROCESS: AttachProcessArgs,
    TOOL_CALL_FUNCTION: CallFunctionArgs,
}


BATCH_STEP_TOOL_MODELS: dict[str, type[BaseModel]] = {
    name: TOOL_MODELS[name] for name in BATCH_STEP_TOOL_NAMES
}
