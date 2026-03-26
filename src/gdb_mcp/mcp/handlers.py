"""Structured MCP tool dispatch for the GDB MCP server."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from ..session.registry import SessionRegistry
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


@dataclass(frozen=True)
class SessionToolSpec:
    """Definition of how to validate and invoke one session-scoped tool."""

    model: type[BaseModel]
    handler: Callable[[Any, BaseModel], dict[str, Any]]


def _normalize_arguments(arguments: Any) -> dict[str, Any]:
    """Normalize tool arguments into a dictionary for Pydantic validation."""

    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object")
    return arguments


def _handle_execute_command(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = ExecuteCommandArgs.model_validate(args.model_dump())
    return session.execute_command(command=typed_args.command)


def _handle_get_status(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.get_status()


def _handle_get_threads(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.get_threads()


def _handle_select_thread(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = ThreadSelectArgs.model_validate(args.model_dump())
    return session.select_thread(thread_id=typed_args.thread_id)


def _handle_get_backtrace(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = GetBacktraceArgs.model_validate(args.model_dump())
    return session.get_backtrace(thread_id=typed_args.thread_id, max_frames=typed_args.max_frames)


def _handle_select_frame(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = FrameSelectArgs.model_validate(args.model_dump())
    return session.select_frame(frame_number=typed_args.frame_number)


def _handle_get_frame_info(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.get_frame_info()


def _handle_set_breakpoint(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = SetBreakpointArgs.model_validate(args.model_dump())
    return session.set_breakpoint(
        location=typed_args.location,
        condition=typed_args.condition,
        temporary=typed_args.temporary,
    )


def _handle_list_breakpoints(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.list_breakpoints()


def _handle_delete_breakpoint(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = BreakpointNumberArgs.model_validate(args.model_dump())
    return session.delete_breakpoint(number=typed_args.number)


def _handle_enable_breakpoint(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = BreakpointNumberArgs.model_validate(args.model_dump())
    return session.enable_breakpoint(number=typed_args.number)


def _handle_disable_breakpoint(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = BreakpointNumberArgs.model_validate(args.model_dump())
    return session.disable_breakpoint(number=typed_args.number)


def _handle_continue(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.continue_execution()


def _handle_step(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.step()


def _handle_next(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.next()


def _handle_interrupt(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.interrupt()


def _handle_evaluate_expression(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = EvaluateExpressionArgs.model_validate(args.model_dump())
    return session.evaluate_expression(typed_args.expression)


def _handle_get_variables(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = GetVariablesArgs.model_validate(args.model_dump())
    return session.get_variables(thread_id=typed_args.thread_id, frame=typed_args.frame)


def _handle_get_registers(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.get_registers()


def _handle_stop_session(session: Any, args: BaseModel) -> dict[str, Any]:
    del args
    return session.stop()


def _handle_call_function(session: Any, args: BaseModel) -> dict[str, Any]:
    typed_args = CallFunctionArgs.model_validate(args.model_dump())
    return session.call_function(function_call=typed_args.function_call)


SESSION_TOOL_SPECS: dict[str, SessionToolSpec] = {
    "gdb_execute_command": SessionToolSpec(ExecuteCommandArgs, _handle_execute_command),
    "gdb_get_status": SessionToolSpec(SessionIdArgs, _handle_get_status),
    "gdb_get_threads": SessionToolSpec(SessionIdArgs, _handle_get_threads),
    "gdb_select_thread": SessionToolSpec(ThreadSelectArgs, _handle_select_thread),
    "gdb_get_backtrace": SessionToolSpec(GetBacktraceArgs, _handle_get_backtrace),
    "gdb_select_frame": SessionToolSpec(FrameSelectArgs, _handle_select_frame),
    "gdb_get_frame_info": SessionToolSpec(SessionIdArgs, _handle_get_frame_info),
    "gdb_set_breakpoint": SessionToolSpec(SetBreakpointArgs, _handle_set_breakpoint),
    "gdb_list_breakpoints": SessionToolSpec(SessionIdArgs, _handle_list_breakpoints),
    "gdb_delete_breakpoint": SessionToolSpec(BreakpointNumberArgs, _handle_delete_breakpoint),
    "gdb_enable_breakpoint": SessionToolSpec(BreakpointNumberArgs, _handle_enable_breakpoint),
    "gdb_disable_breakpoint": SessionToolSpec(BreakpointNumberArgs, _handle_disable_breakpoint),
    "gdb_continue": SessionToolSpec(SessionIdArgs, _handle_continue),
    "gdb_step": SessionToolSpec(SessionIdArgs, _handle_step),
    "gdb_next": SessionToolSpec(SessionIdArgs, _handle_next),
    "gdb_interrupt": SessionToolSpec(SessionIdArgs, _handle_interrupt),
    "gdb_evaluate_expression": SessionToolSpec(EvaluateExpressionArgs, _handle_evaluate_expression),
    "gdb_get_variables": SessionToolSpec(GetVariablesArgs, _handle_get_variables),
    "gdb_get_registers": SessionToolSpec(SessionIdArgs, _handle_get_registers),
    "gdb_stop_session": SessionToolSpec(SessionIdArgs, _handle_stop_session),
    "gdb_call_function": SessionToolSpec(CallFunctionArgs, _handle_call_function),
}


def _invalid_session_result(session_id: Any) -> dict[str, Any]:
    """Return the standard invalid-session error response."""

    return {
        "status": "error",
        "message": f"Invalid session_id: {session_id}. Use gdb_start_session to create a new session.",
    }


def _handle_start_session(
    arguments: dict[str, Any],
    session_manager: SessionRegistry,
) -> dict[str, Any]:
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
    if session_id is not None:
        result["session_id"] = session_id
    return result


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

        tool_spec = SESSION_TOOL_SPECS.get(name)
        if tool_spec is None:
            return serialize_result({"status": "error", "message": f"Unknown tool: {name}"})

        parsed_args = tool_spec.model.model_validate(normalized_args)
        session_id = parsed_args.session_id
        session = session_manager.get_session(session_id)

        if session is None:
            return serialize_result(_invalid_session_result(session_id))

        result = tool_spec.handler(session, parsed_args)
        if name == "gdb_stop_session" and result.get("status") == "success":
            session_manager.remove_session(session_id)

        return serialize_result(result)

    except Exception as exc:
        logger.error("Error executing tool %s: %s", name, exc, exc_info=True)
        return serialize_exception(name, exc)
