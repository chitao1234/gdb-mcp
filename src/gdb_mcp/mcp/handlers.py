"""Structured MCP tool dispatch for the GDB MCP server."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import shlex
from collections.abc import Callable, Sequence
from typing import Literal, Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel
from mcp.types import TextContent

from ..domain import (
    CatchpointType,
    FollowForkMode,
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    WatchpointAccessType,
    payload_to_mapping,
)
from ..session.campaign import (
    RunUntilFailureCaptureRequest,
    RunUntilFailureCriteria,
    RunUntilFailureRequest,
    RunUntilFailureService,
)
from ..session.constants import DEFAULT_TIMEOUT_SEC
from ..session.locking import session_workflow_context
from ..session.registry import SessionRegistry
from ..session.service import SessionService
from ..session.workflow import BatchStepTemplate
from .schemas import (
    AddInferiorArgs,
    AttachProcessArgs,
    BatchArgs,
    BatchStepArgs,
    BreakpointManageArgs,
    BreakpointNumberArgs,
    BreakpointQueryArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ContextManageArgs,
    ContextQueryArgs,
    DisassembleArgs,
    DetachOnForkArgs,
    EvaluateExpressionArgs,
    ExecutionContinueAction,
    ExecutionFinishAction,
    ExecutionInterruptAction,
    ExecutionManageArgs,
    ExecutionNextAction,
    ExecutionRunAction,
    ExecutionStepAction,
    ExecutionWaitArgs,
    ExecutionWaitForStopAction,
    ExecuteCommandArgs,
    FinishArgs,
    FollowForkModeArgs,
    FrameSelectArgs,
    GetBacktraceArgs,
    GetSourceContextArgs,
    GetRegistersArgs,
    GetVariablesArgs,
    InferiorManageArgs,
    InferiorQueryArgs,
    InferiorSelectArgs,
    InspectQueryArgs,
    ReadMemoryArgs,
    RemoveInferiorArgs,
    RunUntilFailureArgs,
    RunArgs,
    SessionManageArgs,
    SessionManageStopAction,
    SessionQueryArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    SessionIdArgs,
    SetCatchpointArgs,
    SetBreakpointArgs,
    SetWatchpointArgs,
    StartSessionArgs,
    ThreadSelectArgs,
    WaitForStopArgs,
)
from .serializer import serialize_exception, serialize_result


class SessionArgsProtocol(Protocol):
    """Validated MCP argument models that carry a session_id."""

    session_id: int


class MemoryRangeArgsProtocol(Protocol):
    """Validated MCP range models used for bundle memory capture."""

    address: str
    count: int
    offset: int
    name: str | None


SessionToolArgsT = TypeVar("SessionToolArgsT", bound=BaseModel)
ToolArguments: TypeAlias = StructuredPayload
ToolResult: TypeAlias = OperationResult[object]
_MEMORY_RANGE_SHORTHAND_RE = re.compile(r"^(?P<address>.+):(?P<count>\d+)(?:@(?P<offset>\d+))?$")


@dataclass(frozen=True)
class SessionToolSpec:
    """Declarative definition for one session-scoped MCP tool."""

    model: type[BaseModel]
    handler: Callable[[SessionService, BaseModel], ToolResult]


def session_tool_spec(
    model: type[SessionToolArgsT],
    handler: Callable[[SessionService, SessionToolArgsT], ToolResult],
) -> SessionToolSpec:
    """Wrap a typed handler for storage in the session tool registry."""

    def invoke(session: SessionService, args: BaseModel) -> ToolResult:
        return handler(session, cast(SessionToolArgsT, args))

    return SessionToolSpec(model=model, handler=invoke)


def _normalize_arguments(arguments: object) -> ToolArguments:
    """Normalize tool arguments into a dictionary for Pydantic validation."""

    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object")
    return cast(ToolArguments, arguments)


def _unwrap_action_args(args: BaseModel) -> BaseModel:
    """Return the discriminated action payload for root-model tool schemas."""

    root = getattr(args, "root", None)
    return cast(BaseModel, root if root is not None else args)


def _wrap_action_result(action: str, result: ToolResult) -> ToolResult:
    """Wrap a tool result in the v2 action envelope."""

    if isinstance(result, OperationError):
        details_payload = payload_to_mapping(result.details)
        details: StructuredPayload = dict(details_payload) if isinstance(details_payload, dict) else {}
        details.setdefault("action", action)
        return OperationError(
            message=result.message,
            code=result.code,
            fatal=result.fatal,
            details=details,
        )

    return OperationSuccess(
        {
            "action": action,
            "result": payload_to_mapping(result.value),
        },
        warnings=result.warnings,
    )


def _execution_wait_policy(wait: ExecutionWaitArgs | None) -> tuple[int, bool]:
    """Translate an execution wait payload into service-layer arguments."""

    timeout_sec = DEFAULT_TIMEOUT_SEC
    if wait is not None and wait.timeout_sec is not None:
        timeout_sec = wait.timeout_sec
    wait_for_stop = wait is None or wait.until == "stop"
    return timeout_sec, wait_for_stop


def _handle_execute_command(session: SessionService, args: ExecuteCommandArgs) -> ToolResult:
    return session.execute_command(command=args.command, timeout_sec=args.timeout_sec)


def _handle_run(session: SessionService, args: RunArgs) -> ToolResult:
    run_args = _normalize_run_args(args.args)
    if isinstance(run_args, OperationError):
        return run_args
    return session.run(
        args=run_args,
        timeout_sec=args.timeout_sec,
        wait_for_stop=args.wait_for_stop,
    )


def _handle_execution_manage(session: SessionService, args: ExecutionManageArgs) -> ToolResult:
    """Route v2 execution actions to the session execution API."""

    action_args = _unwrap_action_args(args)

    if isinstance(action_args, ExecutionRunAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        run_args = _normalize_run_args(action_args.execution.args)
        if isinstance(run_args, OperationError):
            return _wrap_action_result("run", run_args)
        return _wrap_action_result(
            "run",
            session.run(
                args=run_args,
                timeout_sec=timeout_sec,
                wait_for_stop=wait_for_stop,
            ),
        )

    if isinstance(action_args, ExecutionContinueAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            "continue",
            session.continue_execution(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
            ),
        )

    if isinstance(action_args, ExecutionInterruptAction):
        return _wrap_action_result("interrupt", session.interrupt())

    if isinstance(action_args, ExecutionStepAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            "step",
            session.step(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
            ),
        )

    if isinstance(action_args, ExecutionNextAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            "next",
            session.next(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
            ),
        )

    if isinstance(action_args, ExecutionFinishAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            "finish",
            session.finish(
                timeout_sec=timeout_sec,
                wait_for_stop=wait_for_stop,
            ),
        )

    if isinstance(action_args, ExecutionWaitForStopAction):
        return _wrap_action_result(
            "wait_for_stop",
            session.wait_for_stop(
                timeout_sec=action_args.execution.timeout_sec,
                stop_reasons=tuple(action_args.execution.stop_reasons),
            ),
        )

    return OperationError(
        message=f"Unsupported execution action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inferior_query(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 inferior query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))

    if action == "list":
        return _wrap_action_result("list", session.list_inferiors())

    if action == "current":
        result = session.list_inferiors()
        if isinstance(result, OperationError):
            return _wrap_action_result("current", result)

        current_inferior_id = result.value.current_inferior_id
        current_inferior = next(
            (
                inferior
                for inferior in result.value.inferiors
                if inferior.get("inferior_id") == current_inferior_id
            ),
            None,
        )
        if current_inferior is None:
            return _wrap_action_result(
                "current",
                OperationError(
                    message="Current inferior could not be determined",
                    code="not_found",
                    details={"current_inferior_id": current_inferior_id},
                ),
            )
        return _wrap_action_result("current", OperationSuccess({"inferior": current_inferior}))

    return OperationError(
        message=f"Unsupported inferior query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inferior_manage(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 inferior mutation actions to the inspection/execution services."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))

    if action == "create":
        payload = getattr(action_args, "inferior")
        return _wrap_action_result(
            "create",
            session.add_inferior(
                executable=cast(str | None, getattr(payload, "executable")),
                make_current=cast(bool, getattr(payload, "make_current")),
            ),
        )

    if action == "remove":
        payload = getattr(action_args, "inferior")
        return _wrap_action_result(
            "remove",
            session.remove_inferior(inferior_id=cast(int, getattr(payload, "inferior_id"))),
        )

    if action == "select":
        payload = getattr(action_args, "inferior")
        return _wrap_action_result(
            "select",
            session.select_inferior(inferior_id=cast(int, getattr(payload, "inferior_id"))),
        )

    if action == "set_follow_fork_mode":
        payload = getattr(action_args, "inferior")
        return _wrap_action_result(
            "set_follow_fork_mode",
            session.set_follow_fork_mode(mode=cast(FollowForkMode, getattr(payload, "mode"))),
        )

    if action == "set_detach_on_fork":
        payload = getattr(action_args, "inferior")
        return _wrap_action_result(
            "set_detach_on_fork",
            session.set_detach_on_fork(enabled=cast(bool, getattr(payload, "enabled"))),
        )

    return OperationError(
        message=f"Unsupported inferior manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_breakpoint_query(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 breakpoint query actions to the breakpoint service."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))

    if action == "list":
        result = session.list_breakpoints()
        if isinstance(result, OperationError):
            return _wrap_action_result("list", result)

        query = getattr(action_args, "query")
        kinds = set(cast(list[str], getattr(query, "kinds")))
        enabled_filter = cast(bool | None, getattr(query, "enabled"))
        if not kinds and enabled_filter is None:
            return _wrap_action_result("list", result)

        filtered_breakpoints = []
        for breakpoint_info in result.value.breakpoints:
            breakpoint_type = str(breakpoint_info.get("type", "")).lower()
            breakpoint_kind = "code"
            if "watch" in breakpoint_type:
                breakpoint_kind = "watch"
            elif "catch" in breakpoint_type:
                breakpoint_kind = "catch"

            if kinds and breakpoint_kind not in kinds:
                continue

            enabled_value = breakpoint_info.get("enabled")
            is_enabled = enabled_value in {True, "y", "Y", "1", 1}
            if enabled_filter is not None and is_enabled != enabled_filter:
                continue

            filtered_breakpoints.append(breakpoint_info)

        return _wrap_action_result(
            "list",
            OperationSuccess(
                {
                    "breakpoints": filtered_breakpoints,
                    "count": len(filtered_breakpoints),
                }
            ),
        )

    if action == "get":
        query = getattr(action_args, "query")
        return _wrap_action_result(
            "get",
            session.get_breakpoint(cast(int, getattr(query, "number"))),
        )

    return OperationError(
        message=f"Unsupported breakpoint query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_breakpoint_manage(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 breakpoint mutation actions to the breakpoint service."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))

    if action == "create":
        payload = getattr(action_args, "breakpoint")
        kind = cast(str, getattr(payload, "kind"))
        if kind == "code":
            return _wrap_action_result(
                "create",
                session.set_breakpoint(
                    location=cast(str, getattr(payload, "location")),
                    condition=cast(str | None, getattr(payload, "condition")),
                    temporary=cast(bool, getattr(payload, "temporary")),
                ),
            )
        if kind == "watch":
            return _wrap_action_result(
                "create",
                session.set_watchpoint(
                    expression=cast(str, getattr(payload, "expression")),
                    access=cast(WatchpointAccessType, getattr(payload, "access")),
                ),
            )
        return _wrap_action_result(
            "create",
            session.set_catchpoint(
                cast(CatchpointType, getattr(payload, "event")),
                argument=cast(str | None, getattr(payload, "argument")),
                temporary=cast(bool, getattr(payload, "temporary")),
            ),
        )

    if action == "update":
        selector = getattr(action_args, "breakpoint")
        changes = getattr(action_args, "changes")
        return _wrap_action_result(
            "update",
            session.update_breakpoint(
                cast(int, getattr(selector, "number")),
                condition=cast(str | None, getattr(changes, "condition")),
                clear_condition=cast(bool, getattr(changes, "clear_condition")),
            ),
        )

    selector = getattr(action_args, "breakpoint")
    number = cast(int, getattr(selector, "number"))
    if action == "delete":
        return _wrap_action_result("delete", session.delete_breakpoint(number=number))
    if action == "enable":
        return _wrap_action_result("enable", session.enable_breakpoint(number=number))
    if action == "disable":
        return _wrap_action_result("disable", session.disable_breakpoint(number=number))

    return OperationError(
        message=f"Unsupported breakpoint manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _location_kwargs(location: BaseModel) -> dict[str, object]:
    """Translate a location union payload into inspection keyword arguments."""

    kind = cast(str, getattr(location, "kind"))
    if kind == "current":
        return {
            "function": None,
            "address": None,
            "start_address": None,
            "end_address": None,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if kind == "function":
        return {
            "function": getattr(location, "function"),
            "address": None,
            "start_address": None,
            "end_address": None,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if kind == "address":
        return {
            "function": None,
            "address": getattr(location, "address"),
            "start_address": None,
            "end_address": None,
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if kind == "address_range":
        return {
            "function": None,
            "address": None,
            "start_address": getattr(location, "start_address"),
            "end_address": getattr(location, "end_address"),
            "file": None,
            "line": None,
            "start_line": None,
            "end_line": None,
        }
    if kind == "file_line":
        return {
            "function": None,
            "address": None,
            "start_address": None,
            "end_address": None,
            "file": getattr(location, "file"),
            "line": getattr(location, "line"),
            "start_line": None,
            "end_line": None,
        }
    return {
        "function": None,
        "address": None,
        "start_address": None,
        "end_address": None,
        "file": getattr(location, "file"),
        "line": None,
        "start_line": getattr(location, "start_line"),
        "end_line": getattr(location, "end_line"),
    }


def _handle_context_query(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 context query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))

    if action == "threads":
        return _wrap_action_result("threads", session.get_threads())

    if action == "backtrace":
        query = getattr(action_args, "query")
        return _wrap_action_result(
            "backtrace",
            session.get_backtrace(
                thread_id=cast(int | None, getattr(query, "thread_id")),
                max_frames=cast(int, getattr(query, "max_frames")),
            ),
        )

    if action == "frame":
        query = getattr(action_args, "query")
        return _wrap_action_result(
            "frame",
            session.get_frame_info(
                thread_id=cast(int | None, getattr(query, "thread_id")),
                frame=cast(int | None, getattr(query, "frame")),
            ),
        )

    return OperationError(
        message=f"Unsupported context query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_context_manage(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 context mutation actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))
    context = getattr(action_args, "context")

    if action == "select_thread":
        return _wrap_action_result(
            "select_thread",
            session.select_thread(thread_id=cast(int, getattr(context, "thread_id"))),
        )

    if action == "select_frame":
        return _wrap_action_result(
            "select_frame",
            session.select_frame(frame_number=cast(int, getattr(context, "frame"))),
        )

    return OperationError(
        message=f"Unsupported context manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inspect_query(session: SessionService, args: BaseModel) -> ToolResult:
    """Route v2 inspect query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    action = cast(str, getattr(action_args, "action"))
    query = getattr(action_args, "query")

    if action == "evaluate":
        context = cast(BaseModel | None, getattr(query, "context"))
        return _wrap_action_result(
            "evaluate",
            session.evaluate_expression(
                cast(str, getattr(query, "expression")),
                thread_id=cast(int | None, getattr(context, "thread_id")) if context is not None else None,
                frame=cast(int | None, getattr(context, "frame")) if context is not None else None,
            ),
        )

    if action == "variables":
        context = cast(BaseModel | None, getattr(query, "context"))
        frame = 0
        if context is not None and getattr(context, "frame") is not None:
            frame = cast(int, getattr(context, "frame"))
        return _wrap_action_result(
            "variables",
            session.get_variables(
                thread_id=cast(int | None, getattr(context, "thread_id")) if context is not None else None,
                frame=frame,
            ),
        )

    if action == "registers":
        context = cast(BaseModel | None, getattr(query, "context"))
        register_numbers = list(cast(list[int], getattr(query, "register_numbers")))
        register_names = list(cast(list[str], getattr(query, "register_names")))
        return _wrap_action_result(
            "registers",
            session.get_registers(
                thread_id=cast(int | None, getattr(context, "thread_id")) if context is not None else None,
                frame=cast(int | None, getattr(context, "frame")) if context is not None else None,
                register_numbers=register_numbers or None,
                register_names=register_names or None,
                include_vector_registers=cast(bool, getattr(query, "include_vector_registers")),
                max_registers=cast(int | None, getattr(query, "max_registers")),
                value_format=cast(Literal["hex", "natural"], getattr(query, "value_format")),
            ),
        )

    if action == "memory":
        return _wrap_action_result(
            "memory",
            session.read_memory(
                address=cast(str, getattr(query, "address")),
                count=cast(int, getattr(query, "count")),
                offset=cast(int, getattr(query, "offset")),
            ),
        )

    if action == "disassembly":
        context = cast(BaseModel | None, getattr(query, "context"))
        location = _location_kwargs(cast(BaseModel, getattr(query, "location")))
        return _wrap_action_result(
            "disassembly",
            session.disassemble(
                thread_id=cast(int | None, getattr(context, "thread_id")) if context is not None else None,
                frame=cast(int | None, getattr(context, "frame")) if context is not None else None,
                function=cast(str | None, location["function"]),
                address=cast(str | None, location["address"]),
                start_address=cast(str | None, location["start_address"]),
                end_address=cast(str | None, location["end_address"]),
                file=cast(str | None, location["file"]),
                line=cast(int | None, location["line"]),
                instruction_count=cast(int, getattr(query, "instruction_count")),
                mode=cast(Literal["assembly", "mixed"], getattr(query, "mode")),
            ),
        )

    if action == "source":
        context = cast(BaseModel | None, getattr(query, "context"))
        location = _location_kwargs(cast(BaseModel, getattr(query, "location")))
        return _wrap_action_result(
            "source",
            session.get_source_context(
                thread_id=cast(int | None, getattr(context, "thread_id")) if context is not None else None,
                frame=cast(int | None, getattr(context, "frame")) if context is not None else None,
                function=cast(str | None, location["function"]),
                address=cast(str | None, location["address"]),
                file=cast(str | None, location["file"]),
                line=cast(int | None, location["line"]),
                start_line=cast(int | None, location["start_line"]),
                end_line=cast(int | None, location["end_line"]),
                context_before=cast(int, getattr(query, "context_before")),
                context_after=cast(int, getattr(query, "context_after")),
            ),
        )

    return OperationError(
        message=f"Unsupported inspect query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_add_inferior(session: SessionService, args: AddInferiorArgs) -> ToolResult:
    return session.add_inferior(
        executable=args.executable,
        make_current=args.make_current,
    )


def _handle_remove_inferior(session: SessionService, args: RemoveInferiorArgs) -> ToolResult:
    return session.remove_inferior(inferior_id=args.inferior_id)


def _handle_attach_process(session: SessionService, args: AttachProcessArgs) -> ToolResult:
    return session.attach_process(pid=args.pid, timeout_sec=args.timeout_sec)


def _handle_list_inferiors(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.list_inferiors()


def _handle_select_inferior(session: SessionService, args: InferiorSelectArgs) -> ToolResult:
    return session.select_inferior(inferior_id=args.inferior_id)


def _handle_set_follow_fork_mode(
    session: SessionService, args: FollowForkModeArgs
) -> ToolResult:
    return session.set_follow_fork_mode(mode=args.mode)


def _handle_set_detach_on_fork(session: SessionService, args: DetachOnForkArgs) -> ToolResult:
    return session.set_detach_on_fork(enabled=args.enabled)


def _handle_get_status(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.get_status()


def _handle_get_threads(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.get_threads()


def _handle_select_thread(session: SessionService, args: ThreadSelectArgs) -> ToolResult:
    return session.select_thread(thread_id=args.thread_id)


def _handle_get_backtrace(session: SessionService, args: GetBacktraceArgs) -> ToolResult:
    thread_id = _normalize_int_argument(args.thread_id, field_name="thread_id", minimum=1)
    if isinstance(thread_id, OperationError):
        return thread_id
    return session.get_backtrace(thread_id=thread_id, max_frames=args.max_frames)


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


def _handle_set_watchpoint(session: SessionService, args: SetWatchpointArgs) -> ToolResult:
    return session.set_watchpoint(expression=args.expression, access=args.access)


def _handle_delete_watchpoint(session: SessionService, args: BreakpointNumberArgs) -> ToolResult:
    return session.delete_watchpoint(number=args.number)


def _handle_set_catchpoint(session: SessionService, args: SetCatchpointArgs) -> ToolResult:
    return session.set_catchpoint(
        args.kind,
        argument=args.argument,
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


def _handle_wait_for_stop(session: SessionService, args: WaitForStopArgs) -> ToolResult:
    return session.wait_for_stop(
        timeout_sec=args.timeout_sec,
        stop_reasons=tuple(args.stop_reasons),
    )


def _handle_step(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.step()


def _handle_next(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.next()


def _handle_finish(session: SessionService, args: FinishArgs) -> ToolResult:
    return session.finish(timeout_sec=args.timeout_sec)


def _handle_interrupt(session: SessionService, args: SessionIdArgs) -> ToolResult:
    del args
    return session.interrupt()


def _handle_evaluate_expression(
    session: SessionService, args: EvaluateExpressionArgs
) -> ToolResult:
    thread_id = _normalize_int_argument(args.thread_id, field_name="thread_id", minimum=1)
    if isinstance(thread_id, OperationError):
        return thread_id

    frame = _normalize_int_argument(args.frame, field_name="frame", minimum=0)
    if isinstance(frame, OperationError):
        return frame

    return session.evaluate_expression(
        args.expression,
        thread_id=thread_id,
        frame=frame,
    )


def _handle_read_memory(session: SessionService, args: ReadMemoryArgs) -> ToolResult:
    return session.read_memory(
        address=args.address,
        count=args.count,
        offset=args.offset,
    )


def _handle_disassemble(session: SessionService, args: DisassembleArgs) -> ToolResult:
    thread_id = _normalize_int_argument(args.thread_id, field_name="thread_id", minimum=1)
    if isinstance(thread_id, OperationError):
        return thread_id

    frame = _normalize_int_argument(args.frame, field_name="frame", minimum=0)
    if isinstance(frame, OperationError):
        return frame

    line = _normalize_int_argument(args.line, field_name="line", minimum=1)
    if isinstance(line, OperationError):
        return line

    return session.disassemble(
        thread_id=thread_id,
        frame=frame,
        function=args.function,
        address=args.address,
        start_address=args.start_address,
        end_address=args.end_address,
        file=args.file,
        line=line,
        instruction_count=args.instruction_count,
        mode=args.mode,
    )


def _handle_get_variables(session: SessionService, args: GetVariablesArgs) -> ToolResult:
    thread_id = _normalize_int_argument(args.thread_id, field_name="thread_id", minimum=1)
    if isinstance(thread_id, OperationError):
        return thread_id

    frame_value = _normalize_int_argument(args.frame, field_name="frame", minimum=0)
    if isinstance(frame_value, OperationError):
        return frame_value
    if frame_value is None:
        return OperationError(message="Invalid frame: value is required", code="validation_error")

    return session.get_variables(thread_id=thread_id, frame=frame_value)


def _handle_get_source_context(session: SessionService, args: GetSourceContextArgs) -> ToolResult:
    thread_id = _normalize_int_argument(args.thread_id, field_name="thread_id", minimum=1)
    if isinstance(thread_id, OperationError):
        return thread_id

    frame = _normalize_int_argument(args.frame, field_name="frame", minimum=0)
    if isinstance(frame, OperationError):
        return frame

    line = _normalize_int_argument(args.line, field_name="line", minimum=1)
    if isinstance(line, OperationError):
        return line

    start_line = _normalize_int_argument(args.start_line, field_name="start_line", minimum=1)
    if isinstance(start_line, OperationError):
        return start_line

    end_line = _normalize_int_argument(args.end_line, field_name="end_line", minimum=1)
    if isinstance(end_line, OperationError):
        return end_line

    return session.get_source_context(
        thread_id=thread_id,
        frame=frame,
        function=args.function,
        address=args.address,
        file=args.file,
        line=line,
        start_line=start_line,
        end_line=end_line,
        context_before=args.context_before,
        context_after=args.context_after,
    )


def _handle_get_registers(session: SessionService, args: GetRegistersArgs) -> ToolResult:
    thread_id = _normalize_int_argument(args.thread_id, field_name="thread_id", minimum=1)
    if isinstance(thread_id, OperationError):
        return thread_id

    frame = _normalize_int_argument(args.frame, field_name="frame", minimum=0)
    if isinstance(frame, OperationError):
        return frame

    register_numbers = [int(number) for number in args.register_numbers]
    register_names = [str(name) for name in args.register_names]

    return session.get_registers(
        thread_id=thread_id,
        frame=frame,
        register_numbers=register_numbers or None,
        register_names=register_names or None,
        include_vector_registers=args.include_vector_registers,
        max_registers=args.max_registers,
        value_format=args.value_format,
    )


def _handle_call_function(session: SessionService, args: CallFunctionArgs) -> ToolResult:
    return session.call_function(function_call=args.function_call, timeout_sec=args.timeout_sec)


def _handle_batch(session: SessionService, args: BatchArgs) -> ToolResult:
    """Validate one batch request and execute it under one workflow lock."""

    step_templates = _build_batch_step_templates(args.session_id, args.steps)
    if isinstance(step_templates, OperationError):
        return step_templates

    return session.execute_batch_templates(
        step_templates.value,
        fail_fast=args.fail_fast,
        capture_stop_events=args.capture_stop_events,
    )


def _handle_capture_bundle(session: SessionService, args: CaptureBundleArgs) -> ToolResult:
    """Write a file-oriented forensic bundle for the current session."""

    memory_ranges = _memory_capture_ranges(args.memory_ranges)
    if isinstance(memory_ranges, OperationError):
        return memory_ranges

    return session.capture_bundle(
        output_dir=args.output_dir,
        bundle_name=args.bundle_name,
        expressions=args.expressions,
        memory_ranges=memory_ranges,
        max_frames=args.max_frames,
        include_threads=args.include_threads,
        include_backtraces=args.include_backtraces,
        include_frame=args.include_frame,
        include_variables=args.include_variables,
        include_registers=args.include_registers,
        include_transcript=args.include_transcript,
        include_stop_history=args.include_stop_history,
    )


def _handle_run_until_failure(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Run repeated fresh sessions until one failure predicate matches."""

    args = RunUntilFailureArgs.model_validate(arguments)
    step_templates = _build_batch_step_templates(1, args.setup_steps)
    if isinstance(step_templates, OperationError):
        return step_templates

    run_args = _normalize_run_args(args.run_args)
    if isinstance(run_args, OperationError):
        return run_args

    capture_memory_ranges = _memory_capture_ranges(args.capture.memory_ranges)
    if isinstance(capture_memory_ranges, OperationError):
        return capture_memory_ranges

    runner = RunUntilFailureService(session_manager.create_untracked_session)
    return runner.run_until_failure(
        RunUntilFailureRequest(
            program=args.startup.program,
            args=tuple(args.startup.args or ()),
            init_commands=tuple(args.startup.init_commands or ()),
            env=dict(args.startup.env or {}),
            gdb_path=args.startup.gdb_path,
            working_dir=args.startup.working_dir,
            core=args.startup.core,
            setup_steps=tuple(step_templates.value),
            run_args=tuple(run_args or ()),
            run_timeout_sec=args.run_timeout_sec,
            max_iterations=args.max_iterations,
            failure=RunUntilFailureCriteria(
                failure_on_error=args.failure.failure_on_error,
                failure_on_timeout=args.failure.failure_on_timeout,
                stop_reasons=tuple(args.failure.stop_reasons),
                execution_states=tuple(args.failure.execution_states),
                exit_codes=tuple(args.failure.exit_codes),
                result_text_regex=args.failure.result_text_regex,
            ),
            capture=RunUntilFailureCaptureRequest(
                enabled=args.capture.enabled,
                output_dir=args.capture.output_dir,
                bundle_name_prefix=args.capture.bundle_name_prefix,
                bundle_name=args.capture.bundle_name,
                expressions=tuple(args.capture.expressions),
                memory_ranges=tuple(capture_memory_ranges),
                max_frames=args.capture.max_frames,
                include_threads=args.capture.include_threads,
                include_backtraces=args.capture.include_backtraces,
                include_frame=args.capture.include_frame,
                include_variables=args.capture.include_variables,
                include_registers=args.capture.include_registers,
                include_transcript=args.capture.include_transcript,
                include_stop_history=args.capture.include_stop_history,
            ),
        )
    )


def _memory_capture_ranges(
    memory_ranges: Sequence[MemoryRangeArgsProtocol | str],
) -> list[MemoryCaptureRange] | OperationError:
    """Convert validated memory-range models into typed internal requests."""

    normalized: list[MemoryCaptureRange] = []
    for index, memory_range in enumerate(memory_ranges):
        if isinstance(memory_range, str):
            parsed = _parse_memory_range_shorthand(memory_range, index=index)
            if isinstance(parsed, OperationError):
                return parsed
            normalized.append(parsed)
            continue

        normalized.append(
            MemoryCaptureRange(
                address=str(memory_range.address),
                count=int(memory_range.count),
                offset=int(memory_range.offset),
                name=str(memory_range.name) if memory_range.name is not None else None,
            )
        )

    return normalized


def _parse_memory_range_shorthand(value: str, *, index: int) -> MemoryCaptureRange | OperationError:
    """Parse '<address>:<count>' (optional '@<offset>') memory-range shorthand."""

    text = value.strip()
    if not text:
        return OperationError(
            message=f"Invalid memory_ranges[{index}]: empty shorthand string",
            code="validation_error",
        )

    match = _MEMORY_RANGE_SHORTHAND_RE.match(text)
    if match is None:
        return OperationError(
            message=(
                f"Invalid memory_ranges[{index}] shorthand: {value!r}. "
                "Expected '<address>:<count>' or '<address>:<count>@<offset>'."
            ),
            code="validation_error",
        )

    address = match.group("address").strip()
    if not address:
        return OperationError(
            message=f"Invalid memory_ranges[{index}] shorthand: missing address expression",
            code="validation_error",
        )

    count = int(match.group("count"))
    offset_text = match.group("offset")
    offset = int(offset_text) if offset_text is not None else 0
    return MemoryCaptureRange(address=address, count=count, offset=offset)


def _normalize_run_args(args: list[str] | str | None) -> list[str] | None | OperationError:
    """Normalize run-argument input into argv list form."""

    if args is None:
        return None
    if isinstance(args, str):
        try:
            return shlex.split(args)
        except ValueError as exc:
            return OperationError(
                message=f"Invalid args string: {exc}",
                code="validation_error",
            )
    return list(args)


def _normalize_int_argument(
    value: int | str | None,
    *,
    field_name: str,
    minimum: int,
) -> int | None | OperationError:
    """Normalize an integer-like argument from int or numeric-string input."""

    if value is None:
        return None

    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return OperationError(
                message=f"Invalid {field_name}: expected an integer value",
                code="validation_error",
            )
        if text.startswith(("+", "-")):
            sign = text[0]
            digits = text[1:]
            if not digits.isdigit():
                return OperationError(
                    message=f"Invalid {field_name}: expected an integer value, got {value!r}",
                    code="validation_error",
                )
            parsed = int(f"{sign}{digits}", 10)
        elif text.isdigit():
            parsed = int(text, 10)
        else:
            return OperationError(
                message=f"Invalid {field_name}: expected an integer value, got {value!r}",
                code="validation_error",
            )
    else:
        return OperationError(
            message=f"Invalid {field_name}: expected an integer value",
            code="validation_error",
        )

    if parsed < minimum:
        qualifier = "positive integer" if minimum == 1 else f"integer >= {minimum}"
        return OperationError(
            message=f"Invalid {field_name}: expected {qualifier}, got {parsed}",
            code="validation_error",
        )
    return parsed


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
    normalized_args = _normalize_run_args(args.args)
    if isinstance(normalized_args, OperationError):
        return normalized_args

    session_id, result = session_manager.start_session(
        program=args.program,
        args=normalized_args,
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


def _handle_session_query(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Validate and route one v2 session query action."""

    args = SessionQueryArgs.model_validate(arguments)
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, SessionQueryListAction):
        return _wrap_action_result("list", session_manager.list_sessions())

    if isinstance(action_args, SessionQueryStatusAction):
        session = session_manager.resolve_session(action_args.session_id)
        if isinstance(session, OperationError):
            return _wrap_action_result("status", session)
        with session_workflow_context(session):
            return _wrap_action_result("status", session.get_status())

    return OperationError(
        message=f"Unsupported session query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_session_manage(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
) -> ToolResult:
    """Validate and route one v2 session lifecycle mutation."""

    args = SessionManageArgs.model_validate(arguments)
    action_args = _unwrap_action_args(args)

    if isinstance(action_args, SessionManageStopAction):
        return _wrap_action_result(
            "stop",
            session_manager.close_session(action_args.session_id),
        )

    return OperationError(
        message=f"Unsupported session manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_session_query_for_session(session: SessionService, args: SessionQueryArgs) -> ToolResult:
    """Route batch-safe session query actions against an already resolved session."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, SessionQueryStatusAction):
        return _wrap_action_result("status", session.get_status())
    return OperationError(
        message="gdb_session_query(action=list) is not valid inside gdb_workflow_batch",
        code="unsupported_combination",
    )


def _dispatch_session_tool(
    arguments: ToolArguments,
    session_manager: SessionRegistry,
    tool_spec: SessionToolSpec,
) -> ToolResult:
    """Validate one session-scoped request and invoke its handler."""

    validated_args = tool_spec.model.model_validate(arguments)
    session_args = cast(SessionArgsProtocol, _unwrap_action_args(validated_args))
    session = session_manager.resolve_session(session_args.session_id)
    if isinstance(session, OperationError):
        return session
    with session_workflow_context(session):
        return tool_spec.handler(session, validated_args)


def _build_batch_step_templates(
    session_id: int,
    steps: Sequence[BatchStepArgs | str],
) -> OperationSuccess[list[BatchStepTemplate]] | OperationError:
    """Validate batch-like step definitions into reusable execution templates."""

    templates: list[BatchStepTemplate] = []

    for index, raw_step in enumerate(steps):
        step = (
            BatchStepArgs.model_validate({"tool": raw_step})
            if isinstance(raw_step, str)
            else raw_step
        )
        if "session_id" in step.arguments:
            return OperationError(
                message=(
                    f"Batch step {index} ({step.tool}) must not include session_id. "
                    "It is inherited from gdb_workflow_batch."
                ),
                code="validation_error",
            )

        if step.tool == "gdb_session_query" and step.arguments.get("action") == "list":
            return OperationError(
                message="gdb_session_query(action=list) is not valid inside gdb_workflow_batch",
                code="unsupported_combination",
            )

        if step.tool == "gdb_session_manage":
            return OperationError(
                message="gdb_session_manage is not valid inside gdb_workflow_batch",
                code="unsupported_combination",
            )

        tool_spec = SESSION_TOOL_SPECS.get(step.tool)
        if tool_spec is None or step.tool in {"gdb_workflow_batch", "gdb_run_until_failure"}:
            return OperationError(
                message=f"Unsupported batch step tool: {step.tool}",
                code="unknown_tool",
            )
        resolved_tool_spec = tool_spec

        step_arguments = cast(ToolArguments, {"session_id": session_id, **step.arguments})

        try:
            validated_args = resolved_tool_spec.model.model_validate(step_arguments)
        except Exception as exc:
            return OperationError(
                message=f"Invalid batch step {index} ({step.tool}): {exc}",
                code="validation_error",
            )

        def execute_step(
            session: SessionService,
            tool_spec: SessionToolSpec = resolved_tool_spec,
            validated_args: BaseModel = validated_args,
        ) -> ToolResult:
            return tool_spec.handler(session, validated_args)

        templates.append(
            BatchStepTemplate(
                tool=step.tool,
                label=step.label,
                execute=execute_step,
            )
        )

    return OperationSuccess(templates)


SESSION_TOOL_SPECS: dict[str, SessionToolSpec] = {
    "gdb_execute_command": session_tool_spec(ExecuteCommandArgs, _handle_execute_command),
    "gdb_session_query": session_tool_spec(SessionQueryArgs, _handle_session_query_for_session),
    "gdb_inferior_query": session_tool_spec(InferiorQueryArgs, _handle_inferior_query),
    "gdb_inferior_manage": session_tool_spec(InferiorManageArgs, _handle_inferior_manage),
    "gdb_execution_manage": session_tool_spec(ExecutionManageArgs, _handle_execution_manage),
    "gdb_breakpoint_query": session_tool_spec(BreakpointQueryArgs, _handle_breakpoint_query),
    "gdb_breakpoint_manage": session_tool_spec(BreakpointManageArgs, _handle_breakpoint_manage),
    "gdb_context_query": session_tool_spec(ContextQueryArgs, _handle_context_query),
    "gdb_context_manage": session_tool_spec(ContextManageArgs, _handle_context_manage),
    "gdb_inspect_query": session_tool_spec(InspectQueryArgs, _handle_inspect_query),
    "gdb_workflow_batch": session_tool_spec(BatchArgs, _handle_batch),
    "gdb_attach_process": session_tool_spec(AttachProcessArgs, _handle_attach_process),
    "gdb_capture_bundle": session_tool_spec(CaptureBundleArgs, _handle_capture_bundle),
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

        if name == "gdb_session_start":
            return serialize_result(_handle_start_session(normalized_args, session_manager))
        if name == "gdb_session_query":
            return serialize_result(_handle_session_query(normalized_args, session_manager))
        if name == "gdb_session_manage":
            return serialize_result(_handle_session_manage(normalized_args, session_manager))
        if name == "gdb_run_until_failure":
            return serialize_result(_handle_run_until_failure(normalized_args, session_manager))

        tool_spec = SESSION_TOOL_SPECS.get(name)
        if tool_spec is None:
            return serialize_result(
                OperationError(message=f"Unknown tool: {name}", code="unknown_tool")
            )

        return serialize_result(_dispatch_session_tool(normalized_args, session_manager, tool_spec))

    except Exception as exc:
        logger.error("Error executing tool %s: %s", name, exc, exc_info=True)
        return serialize_exception(name, exc)
