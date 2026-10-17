"""Inspection handlers and location/context selectors."""

from __future__ import annotations

from typing import cast

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
    LocationFileRangeArgs,
    LocationFunctionArgs,
    ThreadFrameContextArgs,
)
from .common import (
    LocationArgs,
    LocationSelection,
    ToolResult,
    _unsupported_action,
    _unwrap_action_args,
)


def _location_selection(location: LocationArgs) -> LocationSelection:
    """Translate a location union payload into inspection keyword arguments."""

    match location:
        case LocationCurrentArgs():
            return LocationSelection()
        case LocationFunctionArgs():
            return LocationSelection(function=location.function)
        case LocationAddressArgs():
            return LocationSelection(address=location.address)
        case LocationAddressRangeArgs():
            return LocationSelection(
                start_address=location.start_address,
                end_address=location.end_address,
            )
        case LocationFileLineArgs():
            return LocationSelection(
                file=location.file,
                line=location.line,
            )
        case LocationFileRangeArgs():
            return LocationSelection(
                file=location.file,
                start_line=location.start_line,
                end_line=location.end_line,
            )
    return LocationSelection()  # pragma: no cover - exhaustive over LocationArgs


def _context_selector(context: ThreadFrameContextArgs | None) -> tuple[int | None, int | None]:
    """Extract optional thread/frame selectors from one typed context payload."""

    if context is None:
        return None, None
    return context.thread_id, context.frame


def _handle_inspect_query(session: SessionService, args: InspectQueryArgs) -> ToolResult:
    """Route v2 inspect query actions to the inspection service."""

    action_args = _unwrap_action_args(args)
    match action_args:
        case InspectEvaluateAction():
            evaluate_query = action_args.query
            thread_id, frame = _context_selector(evaluate_query.context)
            return session.evaluate_expression(
                evaluate_query.expression,
                thread_id=thread_id,
                frame=frame,
            )
        case InspectVariablesAction():
            variables_query = action_args.query
            thread_id, frame = _context_selector(variables_query.context)
            return session.get_variables(
                thread_id=thread_id,
                frame=0 if frame is None else frame,
            )
        case InspectRegistersAction():
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
        case InspectMemoryAction():
            memory_query = action_args.query
            return session.read_memory(
                address=memory_query.address,
                count=memory_query.count,
                offset=memory_query.offset,
            )
        case InspectDisassemblyAction():
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
        case InspectSourceAction():
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
    return _unsupported_action("inspect query action", action_args)
