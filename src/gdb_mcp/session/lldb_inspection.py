"""LLDB backend driven exclusively through the native SB API bridge."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Literal

from ..domain import (
    AddressRange,
    BacktraceInfo,
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    ExpressionValueInfo,
    FrameInfo,
    FrameSelectionInfo,
    InferiorListInfo,
    InferiorRecord,
    InferiorSelectionInfo,
    InspectionContextInfo,
    InspectionLocation,
    MemoryBlockRecord,
    MemoryReadInfo,
    OperationError,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    SourceContextInfo,
    SourceLineRecord,
    SourceRange,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariableRecord,
    VariablesInfo,
)
from ..domain.errors import DebuggerDecodeError
from .lldb_breakpoints import LldbRequest
from .lldb_bridge_types import BridgeError
from .lldb_values import frame as frame_record
from .lldb_values import integer, mappings, operation_error, text
from .lldb_values import thread as thread_record
from .requests import (
    AddressLocation,
    AddressRangeLocation,
    CurrentLocation,
    DisassemblyLocation,
    DisassemblyRequest,
    FileLineLocation,
    FileRangeLocation,
    FunctionLocation,
    SourceContextRequest,
)
from .runtime import SessionRuntime


def _inspection_location(location: DisassemblyLocation) -> InspectionLocation:
    match location:
        case CurrentLocation() | AddressRangeLocation():
            return InspectionLocation(kind=location.kind)
        case FunctionLocation():
            return InspectionLocation(kind=location.kind, function=location.function)
        case AddressLocation():
            return InspectionLocation(kind=location.kind, address=location.address)
        case FileLineLocation():
            return InspectionLocation(
                kind=location.kind, file=location.file, fullname=location.file, line=location.line
            )
        case FileRangeLocation():
            return InspectionLocation(
                kind=location.kind, file=location.file, fullname=location.file
            )


class LldbInspection:
    """LLDB inferior, context, data, and code inspection operations."""

    def __init__(self, runtime: SessionRuntime, bridge: LldbRequest) -> None:
        self.runtime = runtime
        self._bridge = bridge

    def refresh_inferiors(self) -> list[InferiorRecord]:
        response = self._bridge._request("target_list")
        targets = mappings(response.get("targets"))
        records: list[InferiorRecord] = []
        for payload in targets:
            record = InferiorRecord.from_payload(
                {key: value for key, value in payload.items() if key != "is_current"}
            )
            records.append(record)
        current = integer(response, "current_inferior_id")
        self.runtime.apply_inferior_inventory(records, current_inferior_id=current)
        return records

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        try:
            records = self.refresh_inferiors()
            return OperationSuccess(
                InferiorListInfo(
                    inferiors=records,
                    current_inferior_id=self.runtime.current_inferior_id,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        try:
            payload = self._bridge._request("target_select", inferior_id=inferior_id)
            self.runtime.mark_inferior_selected(inferior_id)
            return OperationSuccess(
                InferiorSelectionInfo(
                    inferior_id=inferior_id,
                    display=text(payload, "display"),
                    description=text(payload, "description"),
                    executable=text(payload, "executable"),
                    message=f"Inferior {inferior_id} selected",
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        try:
            payload = self._bridge._request("threads")
            records = [
                record
                for item in mappings(payload.get("threads"))
                if (record := thread_record(item)) is not None
            ]
            current = integer(payload, "current_thread_id")
            self.runtime.mark_thread_selected(current)
            return OperationSuccess(
                ThreadListInfo(
                    threads=records,
                    current_thread_id=str(current) if current is not None else None,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "backtrace",
                thread_id=thread_id if thread_id is not None else -1,
                max_frames=max_frames,
            )
            frames = [
                record
                for item in mappings(payload.get("frames"))
                if (record := frame_record(item)) is not None
            ]
            return OperationSuccess(
                BacktraceInfo(
                    thread_id=integer(payload, "thread_id"),
                    frames=frames,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def get_frame_info(
        self, *, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[FrameInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "frame",
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame if frame is not None else -1,
            )
            return OperationSuccess(FrameInfo(frame=frame_record(payload.get("frame"))))
        except BridgeError as exc:
            return operation_error(exc)

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        try:
            payload = self._bridge._request("select_thread", thread_id=thread_id)
            self.runtime.mark_thread_selected(thread_id)
            self.runtime.mark_frame_selected(0)
            return OperationSuccess(
                ThreadSelectionInfo(
                    thread_id=thread_id,
                    new_thread_id=str(thread_id),
                    frame=frame_record(payload.get("frame")),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        try:
            payload = self._bridge._request("select_frame", frame=frame_number)
            self.runtime.mark_frame_selected(frame_number)
            return OperationSuccess(
                FrameSelectionInfo(
                    frame_number=frame_number,
                    frame=frame_record(payload.get("frame")),
                    message=f"Frame {frame_number} selected",
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "evaluate",
                expression=expression,
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame if frame is not None else -1,
            )
            return OperationSuccess(
                ExpressionValueInfo(
                    expression=expression,
                    value=payload.get("value"),
                    type=text(payload, "type"),
                    summary=text(payload, "summary"),
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "variables",
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame,
            )
            variables = [
                VariableRecord.from_payload(item) for item in mappings(payload.get("variables"))
            ]
            return OperationSuccess(
                VariablesInfo(
                    thread_id=integer(payload, "thread_id"),
                    frame=frame,
                    variables=variables,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "registers",
                thread_id=thread_id if thread_id is not None else -1,
                frame=frame if frame is not None else -1,
                max_registers=max_registers if max_registers is not None else -1,
                value_format=value_format,
            )
            records = [
                RegisterRecord.from_payload(item) for item in mappings(payload.get("registers"))
            ]
            if register_numbers is not None:
                wanted = set(register_numbers)
                records = [record for record in records if record.number in wanted]
            if register_names is not None:
                wanted_names = {name.lower() for name in register_names}
                records = [
                    record for record in records if (record.name or "").lower() in wanted_names
                ]
            if max_registers is not None:
                records = records[:max_registers]
            return OperationSuccess(RegistersInfo(registers=records))
        except BridgeError as exc:
            return operation_error(exc)

    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        try:
            payload = self._bridge._request("memory", address=address, count=count, offset=offset)
            begin = text(payload, "address")
            data = text(payload, "data")
            read = integer(payload, "read")
            if begin is None or data is None or read is None:
                raise DebuggerDecodeError(
                    "LLDB memory response requires address, data, and read count"
                )
            try:
                end_address = hex(int(begin, 16) + read)
            except ValueError as exc:
                raise DebuggerDecodeError(
                    f"LLDB memory address is not hexadecimal: {begin!r}"
                ) from exc
            block = MemoryBlockRecord(
                begin=begin,
                offset="0x0",
                end=end_address,
                contents=data,
            )
            return OperationSuccess(
                MemoryReadInfo(
                    address=address,
                    requested_bytes=count,
                    offset=offset,
                    blocks=[block],
                    captured_bytes=read,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def disassemble(
        self, request: DisassemblyRequest
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "disassemble",
                **asdict(request.location),
                instruction_count=request.instruction_count,
                mode=request.mode,
            )
            instructions = [
                DisassemblyInstructionRecord.from_payload(item)
                for item in mappings(payload.get("instructions"))
            ]
            location = _inspection_location(request.location)
            if isinstance(request.location, FileRangeLocation):
                location = replace(location, kind="file_line")
            if instructions:
                first = instructions[0]
                location = replace(
                    location, file=first.file, fullname=first.fullname, line=first.line
                )
            location = replace(location, function=text(payload, "function") or location.function)
            return OperationSuccess(
                DisassemblyInfo(
                    context=InspectionContextInfo(thread_id=request.thread_id, frame=request.frame),
                    location=location,
                    range=AddressRange(
                        start_address=text(payload, "start_address"),
                        end_address=text(payload, "end_address"),
                    ),
                    mode=request.mode,
                    instructions=instructions,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)

    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        try:
            payload = self._bridge._request(
                "source",
                **asdict(request.location),
                context_before=request.context_before,
                context_after=request.context_after,
            )
            lines = [SourceLineRecord.from_payload(item) for item in mappings(payload.get("lines"))]
            location = _inspection_location(request.location)
            source_range = SourceRange(1, len(lines))
            if isinstance(request.location, FileRangeLocation):
                source_range = SourceRange(request.location.start_line, request.location.end_line)
            return OperationSuccess(
                SourceContextInfo(
                    context=InspectionContextInfo(thread_id=request.thread_id, frame=request.frame),
                    location=replace(
                        location,
                        function=text(payload, "function") or location.function,
                        file=str(payload.get("file") or location.file or ""),
                        fullname=text(payload, "fullname"),
                        line=integer(payload, "line") or location.line,
                    ),
                    range=SourceRange(
                        start_line=integer(payload, "start_line") or source_range.start_line,
                        end_line=integer(payload, "end_line") or source_range.end_line,
                    ),
                    lines=lines,
                )
            )
        except BridgeError as exc:
            return operation_error(exc)
