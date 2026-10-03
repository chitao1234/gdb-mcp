"""Registered CLI spec table."""

from __future__ import annotations


from gdb_mcp.contracts import (
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

from ..input_parsers import (
    parse_breakpoint_manage_input,
    parse_breakpoint_query_input,
    parse_context_manage_input,
    parse_context_query_input,
    parse_execution_manage_input,
    parse_inferior_manage_input,
    parse_inferior_query_input,
    parse_inspect_query_input,
    parse_run_until_failure_input,
    parse_session_query_input,
    parse_session_start_input,
    parse_workflow_batch_input,
)
from ..renderers import render_action_payload, render_mapping, render_session_start
from .common import (
    RegisteredToolCliSpec,
    ToolCliSpec,
    _parse_namespace,
    _register_tool_spec,
)
from .session import (
    _build_session_manage,
    _build_session_query,
    _build_session_start,
    _configure_session_manage,
    _configure_session_query,
    _configure_session_start,
)
from .inferior import (
    _build_inferior_manage,
    _build_inferior_query,
    _configure_inferior_manage,
    _configure_inferior_query,
)
from .execution import (
    _build_execution_manage,
    _configure_execution_manage,
)
from .context import (
    _build_context_manage,
    _build_context_query,
    _configure_context_manage,
    _configure_context_query,
)
from .breakpoint import (
    _build_breakpoint_manage,
    _build_breakpoint_query,
    _configure_breakpoint_manage,
    _configure_breakpoint_query,
)
from .inspect import (
    _build_inspect_query,
    _configure_inspect_query,
)
from .workflow import (
    _build_capture_bundle,
    _build_run_until_failure,
    _build_workflow_batch,
    _configure_capture_bundle,
    _configure_run_until_failure,
    _configure_workflow_batch,
)
from .dedicated import (
    _build_attach_process,
    _build_call_function,
    _build_execute_command,
    _configure_attach_process,
    _configure_call_function,
    _configure_execute_command,
)

CLIENT_TOOL_SPECS: dict[str, RegisteredToolCliSpec] = {
    TOOL_SESSION_START: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_SESSION_START,
            configure_parser=_configure_session_start,
            parse_input=parse_session_start_input,
            build_arguments=_build_session_start,
            render_human=render_session_start,
        )
    ),
    TOOL_SESSION_QUERY: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_SESSION_QUERY,
            configure_parser=_configure_session_query,
            parse_input=parse_session_query_input,
            build_arguments=_build_session_query,
            render_human=render_action_payload,
        )
    ),
    TOOL_SESSION_MANAGE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_SESSION_MANAGE,
            configure_parser=_configure_session_manage,
            parse_input=_parse_namespace,
            build_arguments=_build_session_manage,
            render_human=render_action_payload,
        )
    ),
    TOOL_INFERIOR_QUERY: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_INFERIOR_QUERY,
            configure_parser=_configure_inferior_query,
            parse_input=parse_inferior_query_input,
            build_arguments=_build_inferior_query,
            render_human=render_action_payload,
        )
    ),
    TOOL_INFERIOR_MANAGE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_INFERIOR_MANAGE,
            configure_parser=_configure_inferior_manage,
            parse_input=parse_inferior_manage_input,
            build_arguments=_build_inferior_manage,
            render_human=render_action_payload,
        )
    ),
    TOOL_EXECUTION_MANAGE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_EXECUTION_MANAGE,
            configure_parser=_configure_execution_manage,
            parse_input=parse_execution_manage_input,
            build_arguments=_build_execution_manage,
            render_human=render_action_payload,
        )
    ),
    TOOL_BREAKPOINT_QUERY: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_BREAKPOINT_QUERY,
            configure_parser=_configure_breakpoint_query,
            parse_input=parse_breakpoint_query_input,
            build_arguments=_build_breakpoint_query,
            render_human=render_action_payload,
        )
    ),
    TOOL_BREAKPOINT_MANAGE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_BREAKPOINT_MANAGE,
            configure_parser=_configure_breakpoint_manage,
            parse_input=parse_breakpoint_manage_input,
            build_arguments=_build_breakpoint_manage,
            render_human=render_action_payload,
        )
    ),
    TOOL_EXECUTE_COMMAND: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_EXECUTE_COMMAND,
            configure_parser=_configure_execute_command,
            parse_input=_parse_namespace,
            build_arguments=_build_execute_command,
            render_human=render_mapping,
        )
    ),
    TOOL_ATTACH_PROCESS: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_ATTACH_PROCESS,
            configure_parser=_configure_attach_process,
            parse_input=_parse_namespace,
            build_arguments=_build_attach_process,
            render_human=render_mapping,
        )
    ),
    TOOL_CONTEXT_QUERY: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_CONTEXT_QUERY,
            configure_parser=_configure_context_query,
            parse_input=parse_context_query_input,
            build_arguments=_build_context_query,
            render_human=render_action_payload,
        )
    ),
    TOOL_CONTEXT_MANAGE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_CONTEXT_MANAGE,
            configure_parser=_configure_context_manage,
            parse_input=parse_context_manage_input,
            build_arguments=_build_context_manage,
            render_human=render_action_payload,
        )
    ),
    TOOL_INSPECT_QUERY: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_INSPECT_QUERY,
            configure_parser=_configure_inspect_query,
            parse_input=parse_inspect_query_input,
            build_arguments=_build_inspect_query,
            render_human=render_action_payload,
        )
    ),
    TOOL_WORKFLOW_BATCH: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_WORKFLOW_BATCH,
            configure_parser=_configure_workflow_batch,
            parse_input=parse_workflow_batch_input,
            build_arguments=_build_workflow_batch,
            render_human=render_mapping,
        )
    ),
    TOOL_CALL_FUNCTION: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_CALL_FUNCTION,
            configure_parser=_configure_call_function,
            parse_input=_parse_namespace,
            build_arguments=_build_call_function,
            render_human=render_mapping,
        )
    ),
    TOOL_CAPTURE_BUNDLE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_CAPTURE_BUNDLE,
            configure_parser=_configure_capture_bundle,
            parse_input=_parse_namespace,
            build_arguments=_build_capture_bundle,
            render_human=render_mapping,
        )
    ),
    TOOL_RUN_UNTIL_FAILURE: _register_tool_spec(
        ToolCliSpec(
            name=TOOL_RUN_UNTIL_FAILURE,
            configure_parser=_configure_run_until_failure,
            parse_input=parse_run_until_failure_input,
            build_arguments=_build_run_until_failure,
            render_human=render_mapping,
        )
    ),
}
