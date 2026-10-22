"""Inspection handlers and location/context selectors."""

from __future__ import annotations

from ...domain import (
    DisassemblyInfo,
    ExpressionValueInfo,
    MemoryReadInfo,
    OperationResult,
    RegistersInfo,
    SourceContextInfo,
    VariablesInfo,
)
from ...session.requests import (
    AddressLocation,
    AddressRangeLocation,
    CurrentLocation,
    DisassemblyLocation,
    DisassemblyRequest,
    FileLineLocation,
    FileRangeLocation,
    FunctionLocation,
    SourceContextRequest,
    SourceLocation,
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
    LocationFileRangeArgs,
    LocationFunctionArgs,
    ThreadFrameContextArgs,
)
from .common import (
    LocationArgs,
)


def _location_selection(location: LocationArgs) -> DisassemblyLocation | SourceLocation:
    """Translate a location union payload into inspection keyword arguments."""

    match location:
        case LocationCurrentArgs():
            return CurrentLocation()
        case LocationFunctionArgs():
            return FunctionLocation(location.function)
        case LocationAddressArgs():
            return AddressLocation(location.address)
        case LocationAddressRangeArgs():
            return AddressRangeLocation(
                start_address=location.start_address,
                end_address=location.end_address,
            )
        case LocationFileLineArgs():
            return FileLineLocation(
                file=location.file,
                line=location.line,
            )
        case LocationFileRangeArgs():
            return FileRangeLocation(
                file=location.file,
                start_line=location.start_line,
                end_line=location.end_line,
            )


def _context_selector(context: ThreadFrameContextArgs | None) -> tuple[int | None, int | None]:
    """Extract optional thread/frame selectors from one typed context payload."""

    if context is None:
        return None, None
    return context.thread_id, context.frame


def _handle_inspect_query(
    session: SessionService, args: InspectQueryArgs
) -> OperationResult[
    ExpressionValueInfo
    | VariablesInfo
    | RegistersInfo
    | MemoryReadInfo
    | DisassemblyInfo
    | SourceContextInfo
]:
    """Route v2 inspect query actions to the inspection service."""

    action_args = args.root
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
            register_numbers = [int(number) for number in registers_query.register_numbers]
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
                DisassemblyRequest(
                    thread_id=thread_id,
                    frame=frame,
                    location=location,
                    instruction_count=disassembly_query.instruction_count,
                    mode=disassembly_query.mode,
                )
            )
        case InspectSourceAction():
            source_query = action_args.query
            thread_id, frame = _context_selector(source_query.context)
            location = _location_selection(source_query.location)
            if isinstance(location, AddressRangeLocation):
                location = CurrentLocation()
            return session.get_source_context(
                SourceContextRequest(
                    thread_id=thread_id,
                    frame=frame,
                    location=location,
                    context_before=source_query.context_before,
                    context_after=source_query.context_after,
                )
            )
