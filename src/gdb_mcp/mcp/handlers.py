"""Structured MCP tool dispatch for the GDB MCP server."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import shlex
from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel, RootModel
from mcp.types import TextContent

from .. import contracts as shared_contracts
from ..contracts import (
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
from ..domain import (
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
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
    AttachProcessArgs,
    BatchArgs,
    BatchStepArgs,
    BreakpointCatchCreateArgs,
    BreakpointManageArgs,
    BreakpointManageCreateAction,
    BreakpointManageNumberAction,
    BreakpointManageUpdateAction,
    BreakpointCodeCreateArgs,
    BreakpointQueryArgs,
    BreakpointQueryGetAction,
    BreakpointQueryListAction,
    BreakpointWatchCreateArgs,
    CallFunctionArgs,
    CaptureBundleArgs,
    ContextManageArgs,
    ContextManageSelectFrameAction,
    ContextManageSelectThreadAction,
    ContextQueryArgs,
    ContextQueryBacktraceAction,
    ContextQueryFrameAction,
    ContextQueryThreadsAction,
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
    InferiorManageArgs,
    InferiorManageCreateAction,
    InferiorManageDetachOnForkAction,
    InferiorManageFollowForkAction,
    InferiorManageRemoveAction,
    InferiorManageSelectAction,
    InferiorQueryArgs,
    InferiorQueryCurrentAction,
    InferiorQueryListAction,
    InspectDisassemblyAction,
    InspectEvaluateAction,
    InspectMemoryAction,
    InspectQueryArgs,
    InspectRegistersAction,
    InspectSourceAction,
    InspectVariablesAction,
    LocationAddressArgs,
    LocationAddressRangeArgs,
    LocationCurrentArgs,
    LocationFileLineArgs,
    LocationFileRangeArgs,
    LocationFunctionArgs,
    RunUntilFailureArgs,
    SessionManageArgs,
    SessionManageStopAction,
    SessionQueryArgs,
    SessionQueryListAction,
    SessionQueryStatusAction,
    StartSessionArgs,
    ThreadFrameContextArgs,
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
LocationArgs: TypeAlias = (
    LocationCurrentArgs
    | LocationFunctionArgs
    | LocationAddressArgs
    | LocationAddressRangeArgs
    | LocationFileLineArgs
    | LocationFileRangeArgs
)
_MEMORY_RANGE_SHORTHAND_RE = re.compile(r"^(?P<address>.+):(?P<count>\d+)(?:@(?P<offset>\d+))?$")


@dataclass(frozen=True)
class SessionToolSpec:
    """Declarative definition for one session-scoped MCP tool."""

    model: type[BaseModel]
    handler: Callable[[SessionService, BaseModel], ToolResult]


@dataclass(frozen=True, slots=True)
class LocationSelection:
    """Typed inspection location resolved from one schema-discriminated selector."""

    function: str | None = None
    address: str | None = None
    start_address: str | None = None
    end_address: str | None = None
    file: str | None = None
    line: int | None = None
    start_line: int | None = None
    end_line: int | None = None


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

    if isinstance(args, RootModel):
        return cast(BaseModel, args.root)
    return args


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


def _workflow_step_validation_error(
    tool_name: str,
    issue: shared_contracts.WorkflowStepValidationIssue,
    *,
    index: int,
) -> OperationError:
    if issue.kind == "session_id_not_allowed":
        return OperationError(
            message=(
                f"Batch step {index} ({tool_name}) must not include session_id. "
                f"It is inherited from {TOOL_WORKFLOW_BATCH}."
            ),
            code="validation_error",
        )
    if issue.kind == "session_query_list_not_allowed":
        return OperationError(
            message=(
                f"{TOOL_SESSION_QUERY}(action={shared_contracts.ACTION_LIST}) "
                f"is not valid inside {TOOL_WORKFLOW_BATCH}"
            ),
            code="unsupported_combination",
        )
    if issue.kind == "session_manage_not_allowed":
        return OperationError(
            message=f"{TOOL_SESSION_MANAGE} is not valid inside {TOOL_WORKFLOW_BATCH}",
            code="unsupported_combination",
        )
    return OperationError(
        message=f"Unsupported batch step tool: {tool_name}",
        code="unknown_tool",
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

def _handle_execution_manage(session: SessionService, args: ExecutionManageArgs) -> ToolResult:
    """Route v2 execution actions to the session execution API."""

    action_args = _unwrap_action_args(args)

    if isinstance(action_args, ExecutionRunAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        run_args = _normalize_run_args(action_args.execution.args)
        if isinstance(run_args, OperationError):
            return _wrap_action_result(action_args.action, run_args)
        return _wrap_action_result(
            action_args.action,
            session.run(
                args=run_args,
                timeout_sec=timeout_sec,
                wait_for_stop=wait_for_stop,
            ),
        )

    if isinstance(action_args, ExecutionContinueAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            action_args.action,
            session.continue_execution(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
            ),
        )

    if isinstance(action_args, ExecutionInterruptAction):
        return _wrap_action_result(action_args.action, session.interrupt())

    if isinstance(action_args, ExecutionStepAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            action_args.action,
            session.step(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
            ),
        )

    if isinstance(action_args, ExecutionNextAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            action_args.action,
            session.next(
                wait_for_stop=wait_for_stop,
                timeout_sec=timeout_sec,
            ),
        )

    if isinstance(action_args, ExecutionFinishAction):
        timeout_sec, wait_for_stop = _execution_wait_policy(action_args.execution.wait)
        return _wrap_action_result(
            action_args.action,
            session.finish(
                timeout_sec=timeout_sec,
                wait_for_stop=wait_for_stop,
            ),
        )

    if isinstance(action_args, ExecutionWaitForStopAction):
        return _wrap_action_result(
            action_args.action,
            session.wait_for_stop(
                timeout_sec=action_args.execution.timeout_sec,
                stop_reasons=tuple(action_args.execution.stop_reasons),
            ),
        )

    return OperationError(
        message=f"Unsupported execution action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inferior_query(session: SessionService, args: InferiorQueryArgs) -> ToolResult:
    """Route v2 inferior query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, InferiorQueryListAction):
        return _wrap_action_result(action_args.action, session.list_inferiors())

    if isinstance(action_args, InferiorQueryCurrentAction):
        result = session.list_inferiors()
        if isinstance(result, OperationError):
            return _wrap_action_result(action_args.action, result)

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
                action_args.action,
                OperationError(
                    message="Current inferior could not be determined",
                    code="not_found",
                    details={"current_inferior_id": current_inferior_id},
                ),
            )
        return _wrap_action_result(action_args.action, OperationSuccess({"inferior": current_inferior}))

    return OperationError(
        message=f"Unsupported inferior query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inferior_manage(session: SessionService, args: InferiorManageArgs) -> ToolResult:
    """Route v2 inferior mutation actions to the inspection/execution services."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, InferiorManageCreateAction):
        create_payload = action_args.inferior
        return _wrap_action_result(
            action_args.action,
            session.add_inferior(
                executable=create_payload.executable,
                make_current=create_payload.make_current,
            ),
        )

    if isinstance(action_args, InferiorManageRemoveAction):
        remove_payload = action_args.inferior
        return _wrap_action_result(
            action_args.action,
            session.remove_inferior(inferior_id=remove_payload.inferior_id),
        )

    if isinstance(action_args, InferiorManageSelectAction):
        select_payload = action_args.inferior
        return _wrap_action_result(
            action_args.action,
            session.select_inferior(inferior_id=select_payload.inferior_id),
        )

    if isinstance(action_args, InferiorManageFollowForkAction):
        follow_payload = action_args.inferior
        return _wrap_action_result(
            action_args.action,
            session.set_follow_fork_mode(mode=follow_payload.mode),
        )

    if isinstance(action_args, InferiorManageDetachOnForkAction):
        detach_payload = action_args.inferior
        return _wrap_action_result(
            action_args.action,
            session.set_detach_on_fork(enabled=detach_payload.enabled),
        )

    return OperationError(
        message=f"Unsupported inferior manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_breakpoint_query(session: SessionService, args: BreakpointQueryArgs) -> ToolResult:
    """Route v2 breakpoint query actions to the breakpoint service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, BreakpointQueryListAction):
        result = session.list_breakpoints()
        if isinstance(result, OperationError):
            return _wrap_action_result(action_args.action, result)

        list_query = action_args.query
        kinds = set(list_query.kinds)
        enabled_filter = list_query.enabled
        if not kinds and enabled_filter is None:
            return _wrap_action_result(action_args.action, result)

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
            action_args.action,
            OperationSuccess(
                {
                    "breakpoints": filtered_breakpoints,
                    "count": len(filtered_breakpoints),
                }
            ),
        )

    if isinstance(action_args, BreakpointQueryGetAction):
        get_query = action_args.query
        return _wrap_action_result(
            action_args.action,
            session.get_breakpoint(get_query.number),
        )

    return OperationError(
        message=f"Unsupported breakpoint query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_breakpoint_manage(session: SessionService, args: BreakpointManageArgs) -> ToolResult:
    """Route v2 breakpoint mutation actions to the breakpoint service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, BreakpointManageCreateAction):
        payload = action_args.breakpoint
        if isinstance(payload, BreakpointCodeCreateArgs):
            return _wrap_action_result(
                action_args.action,
                session.set_breakpoint(
                    location=payload.location,
                    condition=payload.condition,
                    temporary=payload.temporary,
                ),
            )
        if isinstance(payload, BreakpointWatchCreateArgs):
            return _wrap_action_result(
                action_args.action,
                session.set_watchpoint(
                    expression=payload.expression,
                    access=payload.access,
                ),
            )
        if isinstance(payload, BreakpointCatchCreateArgs):
            return _wrap_action_result(
                action_args.action,
                session.set_catchpoint(
                    payload.event,
                    argument=payload.argument,
                    temporary=payload.temporary,
                ),
            )
        return OperationError(
            message=f"Unsupported breakpoint create payload: {type(payload).__name__}",
            code="validation_error",
        )

    if isinstance(action_args, BreakpointManageUpdateAction):
        selector = action_args.breakpoint
        changes = action_args.changes
        return _wrap_action_result(
            action_args.action,
            session.update_breakpoint(
                selector.number,
                condition=changes.condition,
                clear_condition=changes.clear_condition,
            ),
        )

    if not isinstance(action_args, BreakpointManageNumberAction):
        return OperationError(
            message=f"Unsupported breakpoint manage action: {type(action_args).__name__}",
            code="validation_error",
        )

    number = action_args.breakpoint.number
    if action_args.action == shared_contracts.ACTION_DELETE:
        return _wrap_action_result(action_args.action, session.delete_breakpoint(number=number))
    if action_args.action == shared_contracts.ACTION_ENABLE:
        return _wrap_action_result(action_args.action, session.enable_breakpoint(number=number))
    if action_args.action == shared_contracts.ACTION_DISABLE:
        return _wrap_action_result(action_args.action, session.disable_breakpoint(number=number))

    return OperationError(
        message=f"Unsupported breakpoint manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _location_selection(location: LocationArgs) -> LocationSelection:
    """Translate a location union payload into inspection keyword arguments."""

    if isinstance(location, LocationCurrentArgs):
        return LocationSelection()
    if isinstance(location, LocationFunctionArgs):
        return LocationSelection(function=location.function)
    if isinstance(location, LocationAddressArgs):
        return LocationSelection(address=location.address)
    if isinstance(location, LocationAddressRangeArgs):
        return LocationSelection(
            start_address=location.start_address,
            end_address=location.end_address,
        )
    if isinstance(location, LocationFileLineArgs):
        return LocationSelection(
            file=location.file,
            line=location.line,
        )
    return LocationSelection(
        file=location.file,
        start_line=location.start_line,
        end_line=location.end_line,
    )


def _context_selector(context: ThreadFrameContextArgs | None) -> tuple[int | None, int | None]:
    """Extract optional thread/frame selectors from one typed context payload."""

    if context is None:
        return None, None
    return context.thread_id, context.frame


def _handle_context_query(session: SessionService, args: ContextQueryArgs) -> ToolResult:
    """Route v2 context query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, ContextQueryThreadsAction):
        return _wrap_action_result(action_args.action, session.get_threads())

    if isinstance(action_args, ContextQueryBacktraceAction):
        backtrace_query = action_args.query
        return _wrap_action_result(
            action_args.action,
            session.get_backtrace(
                thread_id=backtrace_query.thread_id,
                max_frames=backtrace_query.max_frames,
            ),
        )

    if isinstance(action_args, ContextQueryFrameAction):
        frame_query = action_args.query
        return _wrap_action_result(
            action_args.action,
            session.get_frame_info(
                thread_id=frame_query.thread_id,
                frame=frame_query.frame,
            ),
        )

    return OperationError(
        message=f"Unsupported context query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_context_manage(session: SessionService, args: ContextManageArgs) -> ToolResult:
    """Route v2 context mutation actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, ContextManageSelectThreadAction):
        return _wrap_action_result(
            action_args.action,
            session.select_thread(thread_id=action_args.context.thread_id),
        )

    if isinstance(action_args, ContextManageSelectFrameAction):
        return _wrap_action_result(
            action_args.action,
            session.select_frame(frame_number=action_args.context.frame),
        )

    return OperationError(
        message=f"Unsupported context manage action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_inspect_query(session: SessionService, args: InspectQueryArgs) -> ToolResult:
    """Route v2 inspect query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, InspectEvaluateAction):
        evaluate_query = action_args.query
        thread_id, frame = _context_selector(evaluate_query.context)
        return _wrap_action_result(
            action_args.action,
            session.evaluate_expression(
                evaluate_query.expression,
                thread_id=thread_id,
                frame=frame,
            ),
        )

    if isinstance(action_args, InspectVariablesAction):
        variables_query = action_args.query
        thread_id, frame = _context_selector(variables_query.context)
        return _wrap_action_result(
            action_args.action,
            session.get_variables(
                thread_id=thread_id,
                frame=0 if frame is None else frame,
            ),
        )

    if isinstance(action_args, InspectRegistersAction):
        registers_query = action_args.query
        thread_id, frame = _context_selector(registers_query.context)
        register_numbers = cast(list[int], list(registers_query.register_numbers))
        register_names = list(registers_query.register_names)
        return _wrap_action_result(
            action_args.action,
            session.get_registers(
                thread_id=thread_id,
                frame=frame,
                register_numbers=register_numbers or None,
                register_names=register_names or None,
                include_vector_registers=registers_query.include_vector_registers,
                max_registers=registers_query.max_registers,
                value_format=registers_query.value_format,
            ),
        )

    if isinstance(action_args, InspectMemoryAction):
        memory_query = action_args.query
        return _wrap_action_result(
            action_args.action,
            session.read_memory(
                address=memory_query.address,
                count=memory_query.count,
                offset=memory_query.offset,
            ),
        )

    if isinstance(action_args, InspectDisassemblyAction):
        disassembly_query = action_args.query
        thread_id, frame = _context_selector(disassembly_query.context)
        location = _location_selection(disassembly_query.location)
        return _wrap_action_result(
            action_args.action,
            session.disassemble(
                thread_id=thread_id,
                frame=frame,
                function=location.function,
                address=location.address,
                start_address=location.start_address,
                end_address=location.end_address,
                file=location.file,
                line=location.line,
                instruction_count=disassembly_query.instruction_count,
                mode=disassembly_query.mode,
            ),
        )

    if isinstance(action_args, InspectSourceAction):
        source_query = action_args.query
        thread_id, frame = _context_selector(source_query.context)
        location = _location_selection(source_query.location)
        return _wrap_action_result(
            action_args.action,
            session.get_source_context(
                thread_id=thread_id,
                frame=frame,
                function=location.function,
                address=location.address,
                file=location.file,
                line=location.line,
                start_line=location.start_line,
                end_line=location.end_line,
                context_before=source_query.context_before,
                context_after=source_query.context_after,
            ),
        )

    return OperationError(
        message=f"Unsupported inspect query action: {type(action_args).__name__}",
        code="validation_error",
    )

def _handle_attach_process(session: SessionService, args: AttachProcessArgs) -> ToolResult:
    return session.attach_process(pid=args.pid, timeout_sec=args.timeout_sec)

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
        return _wrap_action_result(action_args.action, session_manager.list_sessions())

    if isinstance(action_args, SessionQueryStatusAction):
        session = session_manager.resolve_session(action_args.session_id)
        if isinstance(session, OperationError):
            return _wrap_action_result(action_args.action, session)
        with session_workflow_context(session):
            return _wrap_action_result(action_args.action, session.get_status())

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
            action_args.action,
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
        return _wrap_action_result(action_args.action, session.get_status())
    return OperationError(
        message=(
            f"{TOOL_SESSION_QUERY}(action={shared_contracts.ACTION_LIST}) "
            f"is not valid inside {TOOL_WORKFLOW_BATCH}"
        ),
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
        issue = shared_contracts.validate_workflow_step_contract(step.tool, step.arguments)
        if issue is not None:
            return _workflow_step_validation_error(step.tool, issue, index=index)

        tool_spec = SESSION_TOOL_SPECS.get(step.tool)
        if tool_spec is None:
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

        if name == TOOL_SESSION_START:
            return serialize_result(_handle_start_session(normalized_args, session_manager))
        if name == TOOL_SESSION_QUERY:
            return serialize_result(_handle_session_query(normalized_args, session_manager))
        if name == TOOL_SESSION_MANAGE:
            return serialize_result(_handle_session_manage(normalized_args, session_manager))
        if name == TOOL_RUN_UNTIL_FAILURE:
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
