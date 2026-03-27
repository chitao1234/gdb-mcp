"""Structured MCP tool dispatch for the GDB MCP server."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Callable
from typing import Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel
from mcp.types import TextContent

from ..domain import (
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    payload_to_mapping,
)
from ..session.registry import SessionRegistry
from ..session.service import SessionService
from .schemas import (
    AttachProcessArgs,
    BreakpointNumberArgs,
    CallFunctionArgs,
    EvaluateExpressionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetRegistersArgs,
    GetVariablesArgs,
    ListSessionsArgs,
    RunArgs,
    SessionIdArgs,
    SetBreakpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
)
from .serializer import serialize_exception, serialize_result


class SessionArgsProtocol(Protocol):
    """Validated MCP argument models that carry a session_id."""

    session_id: int


SessionArgsT = TypeVar("SessionArgsT", bound=SessionArgsProtocol)
ToolArguments: TypeAlias = StructuredPayload
ToolResult: TypeAlias = OperationResult[object]


@dataclass(frozen=True)
class SessionToolSpec:
    """Declarative definition for one session-scoped MCP tool."""

    model: type[BaseModel]
    handler: Callable[[SessionService, BaseModel], ToolResult]


def session_tool_spec(
    model: type[BaseModel],
    handler: Callable[[SessionService, SessionArgsT], ToolResult],
) -> SessionToolSpec:
    """Wrap a typed handler for storage in the session tool registry."""

    def invoke(session: SessionService, args: BaseModel) -> ToolResult:
        return handler(session, cast(SessionArgsT, args))

    return SessionToolSpec(model=model, handler=invoke)


def _normalize_arguments(arguments: object) -> ToolArguments:
    """Normalize tool arguments into a dictionary for Pydantic validation."""

    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object")
    return cast(ToolArguments, arguments)


def _handle_execute_command(session: SessionService, args: ExecuteCommandArgs) -> ToolResult:
    return session.execute_command(command=args.command, timeout_sec=args.timeout_sec)


def _handle_run(session: SessionService, args: RunArgs) -> ToolResult:
    return session.run(args=args.args, timeout_sec=args.timeout_sec)


def _handle_attach_process(session: SessionService, args: AttachProcessArgs) -> ToolResult:
    return session.attach_process(pid=args.pid, timeout_sec=args.timeout_sec)


def _handle_get_status(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.get_status()


def _handle_get_threads(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.get_threads()


def _handle_select_thread(session: SessionService, args: ThreadSelectArgs) -> ToolResult:
    return session.select_thread(thread_id=args.thread_id)


def _handle_get_backtrace(session: SessionService, args: GetBacktraceArgs) -> ToolResult:
    return session.get_backtrace(thread_id=args.thread_id, max_frames=args.max_frames)


def _handle_select_frame(session: SessionService, args: FrameSelectArgs) -> ToolResult:
    return session.select_frame(frame_number=args.frame_number)


def _handle_get_frame_info(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.get_frame_info()


def _handle_set_breakpoint(session: SessionService, args: SetBreakpointArgs) -> ToolResult:
    return session.set_breakpoint(
        location=args.location,
        condition=args.condition,
        temporary=args.temporary,
    )


def _handle_list_breakpoints(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.list_breakpoints()


def _handle_delete_breakpoint(session: SessionService, args: BreakpointNumberArgs) -> ToolResult:
    return session.delete_breakpoint(number=args.number)


def _handle_enable_breakpoint(session: SessionService, args: BreakpointNumberArgs) -> ToolResult:
    return session.enable_breakpoint(number=args.number)


def _handle_disable_breakpoint(session: SessionService, args: BreakpointNumberArgs) -> ToolResult:
    return session.disable_breakpoint(number=args.number)


def _handle_continue(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.continue_execution()


def _handle_step(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.step()


def _handle_next(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.next()


def _handle_interrupt(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.interrupt()


def _handle_evaluate_expression(
    session: SessionService, args: EvaluateExpressionArgs
) -> ToolResult:
    return session.evaluate_expression(
        args.expression,
        thread_id=args.thread_id,
        frame=args.frame,
    )


def _handle_get_variables(session: SessionService, args: GetVariablesArgs) -> ToolResult:
    return session.get_variables(thread_id=args.thread_id, frame=args.frame)


def _handle_get_registers(session: SessionService, args: GetRegistersArgs) -> ToolResult:
    return session.get_registers(thread_id=args.thread_id, frame=args.frame)


def _handle_call_function(session: SessionService, args: CallFunctionArgs) -> ToolResult:
    return session.call_function(function_call=args.function_call, timeout_sec=args.timeout_sec)


def _invalid_session_result(session_id: object) -> OperationError:
    """Return the standard invalid-session error response."""

    return OperationError(
        message=f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session."
    )


def _handle_start_session(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Validate and start a new debugger session."""

    args = StartSessionArgs.model_validate(arguments)
    session_id, result = session_manager.start_session(
        program=args.program,
        args=args.args,
        init_commands=args.init_commands,
        env=args.env,
        gdb_path=args.gdb_path,
        working_dir=args.working_dir,
        core=args.core,
    )
    if session_id is not None and isinstance(result, OperationSuccess):
        payload = payload_to_mapping(result.value)
        if not isinstance(payload, dict):
            return OperationError(message="Internal error: session start payload must be an object")
        payload = dict(payload)
        payload["session_id"] = session_id
        return OperationSuccess(payload)
    return result


def _dispatch_session_tool(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
    tool_spec: SessionToolSpec,
) -> ToolResult:
    """Validate one session-scoped request and invoke its handler."""

    args = cast(SessionArgsProtocol, tool_spec.model.model_validate(arguments))
    session = session_manager.resolve_session(args.session_id)
    if isinstance(session, OperationError):
        return session
    return tool_spec.handler(session, cast(BaseModel, args))


SESSION_TOOL_SPECS: dict[str, SessionToolSpec] = {
    "gdb_execute_command": session_tool_spec(ExecuteCommandArgs, _handle_execute_command),
    "gdb_run": session_tool_spec(RunArgs, _handle_run),
    "gdb_attach_process": session_tool_spec(AttachProcessArgs, _handle_attach_process),
    "gdb_get_status": session_tool_spec(SessionIdArgs, _handle_get_status),
    "gdb_get_threads": session_tool_spec(SessionIdArgs, _handle_get_threads),
    "gdb_select_thread": session_tool_spec(ThreadSelectArgs, _handle_select_thread),
    "gdb_get_backtrace": session_tool_spec(GetBacktraceArgs, _handle_get_backtrace),
    "gdb_select_frame": session_tool_spec(FrameSelectArgs, _handle_select_frame),
    "gdb_get_frame_info": session_tool_spec(SessionIdArgs, _handle_get_frame_info),
    "gdb_set_breakpoint": session_tool_spec(SetBreakpointArgs, _handle_set_breakpoint),
    "gdb_list_breakpoints": session_tool_spec(SessionIdArgs, _handle_list_breakpoints),
    "gdb_delete_breakpoint": session_tool_spec(BreakpointNumberArgs, _handle_delete_breakpoint),
    "gdb_enable_breakpoint": session_tool_spec(BreakpointNumberArgs, _handle_enable_breakpoint),
    "gdb_disable_breakpoint": session_tool_spec(BreakpointNumberArgs, _handle_disable_breakpoint),
    "gdb_continue": session_tool_spec(SessionIdArgs, _handle_continue),
    "gdb_step": session_tool_spec(SessionIdArgs, _handle_step),
    "gdb_next": session_tool_spec(SessionIdArgs, _handle_next),
    "gdb_interrupt": session_tool_spec(SessionIdArgs, _handle_interrupt),
    "gdb_evaluate_expression": session_tool_spec(
        EvaluateExpressionArgs, _handle_evaluate_expression
    ),
    "gdb_get_variables": session_tool_spec(GetVariablesArgs, _handle_get_variables),
    "gdb_get_registers": session_tool_spec(GetRegistersArgs, _handle_get_registers),
    "gdb_call_function": session_tool_spec(CallFunctionArgs, _handle_call_function),
}


async def dispatch_tool_call(
    name: str,
    arguments: object,
    session_manager: SessionRegistry,
    *,
    logger: logging.Logger,
) -> list[TextContent]:
    """Dispatch one MCP tool call using structured validation and handlers."""

    try:
        normalized_args = _normalize_arguments(arguments)

        if name == "gdb_start_session":
            return serialize_result(_handle_start_session(normalized_args, session_manager))
        if name == "gdb_list_sessions":
            ListSessionsArgs.model_validate(normalized_args)
            return serialize_result(session_manager.list_sessions())
        if name == "gdb_stop_session":
            args = SessionIdArgs.model_validate(normalized_args)
            return serialize_result(session_manager.close_session(args.session_id))

        tool_spec = SESSION_TOOL_SPECS.get(name)
        if tool_spec is None:
            return serialize_result(
                OperationError(message=f"Unknown tool: {name}", code="unknown_tool")
            )

        return serialize_result(_dispatch_session_tool(normalized_args, session_manager, tool_spec))

    except Exception as exc:
        logger.error("Error executing tool %s: %s", name, exc, exc_info=True)
        return serialize_exception(name, exc)
