"""Inspection handlers and location/context selectors."""

from __future__ import annotations

from typing import cast


from ...domain import (
    OperationError,
)
from ...session.service import SessionService
from ..schemas import (
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
    LocationFunctionArgs,
    ThreadFrameContextArgs,
)
from .common import (
    LocationArgs,
    LocationSelection,
    ToolResult,
    _unwrap_action_args,
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


def _handle_inspect_query(session: SessionService, args: InspectQueryArgs) -> ToolResult:
    """Route v2 inspect query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, InspectEvaluateAction):
        evaluate_query = action_args.query
        thread_id, frame = _context_selector(evaluate_query.context)
        return session.evaluate_expression(
            evaluate_query.expression,
            thread_id=thread_id,
            frame=frame,
        )

    if isinstance(action_args, InspectVariablesAction):
        variables_query = action_args.query
        thread_id, frame = _context_selector(variables_query.context)
        return session.get_variables(
            thread_id=thread_id,
            frame=0 if frame is None else frame,
        )

    if isinstance(action_args, InspectRegistersAction):
        registers_query = action_args.query
        thread_id, frame = _context_selector(registers_query.context)
        register_numbers = cast(list[int], list(registers_query.register_numbers))
        register_names = list(registers_query.register_names)
        return session.get_registers(
            thread_id=thread_id,
            frame=frame,
            register_numbers=register_numbers or None,
            register_names=register_names or None,
            include_vector_registers=registers_query.include_vector_registers,
            max_registers=registers_query.max_registers,
            value_format=registers_query.value_format,
        )

    if isinstance(action_args, InspectMemoryAction):
        memory_query = action_args.query
        return session.read_memory(
            address=memory_query.address,
            count=memory_query.count,
            offset=memory_query.offset,
        )

    if isinstance(action_args, InspectDisassemblyAction):
        disassembly_query = action_args.query
        thread_id, frame = _context_selector(disassembly_query.context)
        location = _location_selection(disassembly_query.location)
        return session.disassemble(
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
        )

    if isinstance(action_args, InspectSourceAction):
        source_query = action_args.query
        thread_id, frame = _context_selector(source_query.context)
        location = _location_selection(source_query.location)
        return session.get_source_context(
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
        )

    return OperationError(
        message=f"Unsupported inspect query action: {type(action_args).__name__}",
        code="validation_error",
    )
