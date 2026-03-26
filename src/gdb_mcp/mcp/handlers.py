"""Structured MCP tool dispatch for the GDB MCP server."""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, TypeVar, cast

from pydantic import BaseModel

from ..domain import (
    OperationError,
    OperationResult,
    OperationSuccess,
    payload_to_mapping,
)
from ..session.registry import SessionRegistry
from ..session.service import SessionService
from .schemas import (
    BreakpointNumberArgs,
    CallFunctionArgs,
    EvaluateExpressionArgs,
    ExecuteCommandArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetVariablesArgs,
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


def _normalize_arguments(arguments: Any) -> dict[str, Any]:
    """Normalize tool arguments into a dictionary for Pydantic validation."""

    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object")
    return arguments


def _handle_execute_command(
    session: SessionService, args: ExecuteCommandArgs
) -> OperationResult[Any]:
    return session.execute_command(command=args.command)


def _handle_get_status(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.get_status()


def _handle_get_threads(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.get_threads()


def _handle_select_thread(
    session: SessionService, args: ThreadSelectArgs
) -> OperationResult[Any]:
    return session.select_thread(thread_id=args.thread_id)


def _handle_get_backtrace(
    session: SessionService, args: GetBacktraceArgs
) -> OperationResult[Any]:
    return session.get_backtrace(thread_id=args.thread_id, max_frames=args.max_frames)


def _handle_select_frame(
    session: SessionService, args: FrameSelectArgs
) -> OperationResult[Any]:
    return session.select_frame(frame_number=args.frame_number)


def _handle_get_frame_info(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.get_frame_info()


def _handle_set_breakpoint(
    session: SessionService, args: SetBreakpointArgs
) -> OperationResult[Any]:
    return session.set_breakpoint(
        location=args.location,
        condition=args.condition,
        temporary=args.temporary,
    )


def _handle_list_breakpoints(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.list_breakpoints()


def _handle_delete_breakpoint(
    session: SessionService, args: BreakpointNumberArgs
) -> OperationResult[Any]:
    return session.delete_breakpoint(number=args.number)


def _handle_enable_breakpoint(
    session: SessionService, args: BreakpointNumberArgs
) -> OperationResult[Any]:
    return session.enable_breakpoint(number=args.number)


def _handle_disable_breakpoint(
    session: SessionService, args: BreakpointNumberArgs
) -> OperationResult[Any]:
    return session.disable_breakpoint(number=args.number)


def _handle_continue(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.continue_execution()


def _handle_step(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.step()


def _handle_next(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.next()


def _handle_interrupt(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.interrupt()


def _handle_evaluate_expression(
    session: SessionService, args: EvaluateExpressionArgs
) -> OperationResult[Any]:
    return session.evaluate_expression(args.expression)


def _handle_get_variables(
    session: SessionService, args: GetVariablesArgs
) -> OperationResult[Any]:
    return session.get_variables(thread_id=args.thread_id, frame=args.frame)


def _handle_get_registers(session: SessionService, args: SessionIdArgs) -> OperationResult[Any]:
    del args
    return session.get_registers()


def _handle_call_function(
    session: SessionService, args: CallFunctionArgs
) -> OperationResult[Any]:
    return session.call_function(function_call=args.function_call)


def _invalid_session_result(session_id: Any) -> OperationError:
    """Return the standard invalid-session error response."""

    return OperationError(
        message=f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session."
    )


def _handle_start_session(
    arguments: dict[str, Any],
    session_manager: SessionRegistry,
) -> OperationResult[Any]:
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
        payload["session_id"] = session_id
        return OperationSuccess(payload)
    return result


def _dispatch_session_tool(
    arguments: dict[str, Any],
    session_manager: SessionRegistry,
    model: type[BaseModel],
    handler: Callable[[SessionService, SessionArgsT], OperationResult[Any]],
) -> OperationResult[Any]:
    """Validate one session-scoped request and invoke its handler."""

    args = cast(SessionArgsT, model.model_validate(arguments))
    session = session_manager.get_session(args.session_id)
    if session is None:
        return _invalid_session_result(args.session_id)
    return handler(session, args)


async def dispatch_tool_call(
    name: str,
    arguments: Any,
    session_manager: SessionRegistry,
    *,
    logger: logging.Logger,
) -> list:
    """Dispatch one MCP tool call using structured validation and handlers."""

    try:
        normalized_args = _normalize_arguments(arguments)

        if name == "gdb_start_session":
            return serialize_result(_handle_start_session(normalized_args, session_manager))
        if name == "gdb_execute_command":
            return serialize_result(
                _dispatch_session_tool(
                    normalized_args, session_manager, ExecuteCommandArgs, _handle_execute_command
                )
            )
        if name == "gdb_get_status":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_get_status)
            )
        if name == "gdb_get_threads":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_get_threads)
            )
        if name == "gdb_select_thread":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, ThreadSelectArgs, _handle_select_thread)
            )
        if name == "gdb_get_backtrace":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, GetBacktraceArgs, _handle_get_backtrace)
            )
        if name == "gdb_select_frame":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, FrameSelectArgs, _handle_select_frame)
            )
        if name == "gdb_get_frame_info":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_get_frame_info)
            )
        if name == "gdb_set_breakpoint":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SetBreakpointArgs, _handle_set_breakpoint)
            )
        if name == "gdb_list_breakpoints":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_list_breakpoints)
            )
        if name == "gdb_delete_breakpoint":
            return serialize_result(
                _dispatch_session_tool(
                    normalized_args, session_manager, BreakpointNumberArgs, _handle_delete_breakpoint
                )
            )
        if name == "gdb_enable_breakpoint":
            return serialize_result(
                _dispatch_session_tool(
                    normalized_args, session_manager, BreakpointNumberArgs, _handle_enable_breakpoint
                )
            )
        if name == "gdb_disable_breakpoint":
            return serialize_result(
                _dispatch_session_tool(
                    normalized_args, session_manager, BreakpointNumberArgs, _handle_disable_breakpoint
                )
            )
        if name == "gdb_continue":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_continue)
            )
        if name == "gdb_step":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_step)
            )
        if name == "gdb_next":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_next)
            )
        if name == "gdb_interrupt":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_interrupt)
            )
        if name == "gdb_evaluate_expression":
            return serialize_result(
                _dispatch_session_tool(
                    normalized_args, session_manager, EvaluateExpressionArgs, _handle_evaluate_expression
                )
            )
        if name == "gdb_get_variables":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, GetVariablesArgs, _handle_get_variables)
            )
        if name == "gdb_get_registers":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, SessionIdArgs, _handle_get_registers)
            )
        if name == "gdb_stop_session":
            args = SessionIdArgs.model_validate(normalized_args)
            return serialize_result(session_manager.close_session(args.session_id))
        if name == "gdb_call_function":
            return serialize_result(
                _dispatch_session_tool(normalized_args, session_manager, CallFunctionArgs, _handle_call_function)
            )

        return serialize_result(OperationError(message=f"Unknown tool: {name}", code="unknown_tool"))

    except Exception as exc:
        logger.error("Error executing tool %s: %s", name, exc, exc_info=True)
        return serialize_exception(name, exc)
