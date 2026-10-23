"""Go debugging through Delve's streaming JSON-RPC API v2."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

from ..domain import (
    AddressRange,
    BacktraceInfo,
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    ExpressionValueInfo,
    FrameInfo,
    FrameSelectionInfo,
    InspectionContextInfo,
    InspectionLocation,
    MemoryBlockRecord,
    MemoryReadInfo,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    SourceContextInfo,
    SourceLineRecord,
    SourceRange,
    ThreadListInfo,
    ThreadRecord,
    ThreadSelectionInfo,
    VariablesInfo,
)
from ..domain.errors import DebuggerDecodeError
from ..transport.delve_rpc import DelveRpcError
from .delve_protocols import VARIABLE_LOAD_CONFIG, DelveRequests
from .delve_protocols import operation as _operation
from .delve_values import (
    frame_record,
    integer,
    mapping,
    mappings,
    optional_integer,
    text,
    variable_record,
    variable_value,
)
from .inspection.register_classes import guess_register_family, is_vector_register
from .requests import (
    DEFAULT_THREAD_QUERY,
    AddressLocation,
    AddressRangeLocation,
    DisassemblyLocation,
    DisassemblyRequest,
    FileLineLocation,
    FileRangeLocation,
    FunctionLocation,
    SourceContextRequest,
    ThreadQuery,
)
from .runtime import SessionRuntime


class DelveInspection:
    """Delve goroutine selection and data/code inspection."""

    def __init__(self, runtime: SessionRuntime, bridge: DelveRequests) -> None:
        self.runtime = runtime
        self._bridge = bridge
        self.memory_read_limit = 1000
        self.channel_queries_supported = False
        self.deferred_arguments_supported = False

    def scope(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        *,
        deferred_call: int | None = None,
    ) -> dict[str, object]:
        self._bridge._require_client()
        if self.runtime.execution_state == "running":
            raise DelveRpcError("Pause the target before inspecting it", "inferior_running")
        scope: dict[str, object] = {
            "GoroutineID": (
                thread_id if thread_id is not None else self.runtime.current_thread_id or -1
            ),
            "Frame": frame if frame is not None else self.runtime.current_frame or 0,
        }
        if deferred_call is not None:
            if deferred_call < 1:
                raise DelveRpcError("deferred_call must be positive", "validation_error")
            if not self.deferred_arguments_supported:
                raise DelveRpcError(
                    "Delve cannot reliably inspect deferred arguments in targets built with Go 1.17 or later",
                    "unsupported_operation",
                )
            scope["DeferredCall"] = deferred_call
        return scope

    @_operation
    def get_threads(
        self, query: ThreadQuery = DEFAULT_THREAD_QUERY
    ) -> OperationSuccess[ThreadListInfo]:
        scope = self.scope(query.thread_id, query.frame)
        arguments: dict[str, object] = {}
        if query.waiting_on_channel is not None:
            if not self.channel_queries_supported:
                raise DelveRpcError(
                    "Channel-wait queries require Delve 1.21.1 or later", "unsupported_operation"
                )
            channel = self._variable(query.waiting_on_channel, query.thread_id, query.frame)
            if integer(channel.get("kind"), 0) != 18:  # reflect.Chan
                raise DelveRpcError(
                    "waiting_on_channel must evaluate to a channel", "validation_error"
                )
            arguments = {
                "Filters": [{"Kind": 8, "Arg": query.waiting_on_channel, "Negated": False}],
                "EvalScope": scope,
            }
        threads: list[ThreadRecord] = []
        seen: set[int] = set()
        start = 0
        truncated = False
        while True:
            count = (
                min(1000, query.max_results + 1 - len(threads))
                if query.max_results is not None
                else 1000
            )
            response = self._bridge._request(
                "ListGoroutines", {**arguments, "Start": start, "Count": count}
            )
            page = mappings(response.get("Goroutines"))
            for goroutine in page:
                goroutine_id = integer(goroutine.get("id"))
                if goroutine_id in seen:
                    continue
                seen.add(goroutine_id)
                if query.max_results is not None and len(threads) >= query.max_results:
                    truncated = True
                    break
                location = mapping(goroutine.get("userCurrentLoc")) or mapping(
                    goroutine.get("currentLoc")
                )
                values = dict(goroutine)
                values.update(
                    {
                        "id": str(integer(goroutine.get("id"))),
                        "name": text(mapping(location.get("function")).get("name")),
                        "state": "running" if integer(goroutine.get("threadID"), 0) else "waiting",
                        "target-id": f"goroutine {integer(goroutine.get('id'))}",
                        "native_thread_id": integer(goroutine.get("threadID"), 0),
                        "frame": frame_record(location).to_payload(),
                    }
                )
                threads.append(ThreadRecord.from_payload(values))
            if truncated:
                break
            if query.waiting_on_channel is not None:
                # Channel queues can contain one goroutine more than Count and report Nextg=-1.
                if len(page) < count:
                    break
                start += len(page)
                continue
            next_start = integer(response.get("Nextg"), -1)
            if next_start < 0:
                break
            if next_start <= start:
                raise DelveRpcError("Delve goroutine pagination did not advance", "protocol_error")
            start = next_start
        return OperationSuccess(
            ThreadListInfo(
                threads,
                (
                    str(self.runtime.current_thread_id)
                    if self.runtime.current_thread_id is not None
                    else None
                ),
                truncated=truncated,
            )
        )

    def _stack(
        self, thread_id: int | None, max_frames: int, *, include_defers: bool = False
    ) -> list[dict[str, object]]:
        scope = self.scope(thread_id)
        response = self._bridge._request(
            "Stacktrace",
            {"Id": scope["GoroutineID"], "Depth": max_frames, "Defers": include_defers},
        )
        return mappings(response.get("Locations"))[:max_frames]

    @_operation
    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100, *, include_defers: bool = False
    ) -> OperationSuccess[BacktraceInfo]:
        frames = [
            frame_record(location, index, include_defers=include_defers)
            for index, location in enumerate(
                self._stack(thread_id, max_frames, include_defers=include_defers)
            )
        ]
        return OperationSuccess(
            BacktraceInfo(
                thread_id if thread_id is not None else self.runtime.current_thread_id, frames
            )
        )

    @_operation
    def get_frame_info(
        self, *, thread_id: int | None = None, frame: int | None = None
    ) -> OperationSuccess[FrameInfo]:
        number = frame if frame is not None else self.runtime.current_frame or 0
        locations = self._stack(thread_id, number + 1)
        if number < 0 or number >= len(locations):
            raise DelveRpcError(f"Frame {number} is unavailable", "no_stack_frame")
        return OperationSuccess(FrameInfo(frame_record(locations[number], number)))

    @_operation
    def select_thread(self, thread_id: int) -> OperationSuccess[ThreadSelectionInfo]:
        self.scope(thread_id)
        response = self._bridge._request(
            "Command", {"name": "switchGoroutine", "goroutineID": thread_id}
        )
        self._bridge._client_state(response, record=False)
        self.runtime.mark_frame_selected(0)
        location = mapping(mapping(response.get("State")).get("currentGoroutine")).get("currentLoc")
        return OperationSuccess(
            ThreadSelectionInfo(thread_id, str(thread_id), frame_record(location))
        )

    @_operation
    def select_frame(self, frame_number: int) -> OperationSuccess[FrameSelectionInfo]:
        if frame_number < 0:
            raise DelveRpcError("frame must be nonnegative", "validation_error")
        locations = self._stack(None, frame_number + 1)
        if frame_number >= len(locations):
            raise DelveRpcError(f"Frame {frame_number} is unavailable", "no_stack_frame")
        self.runtime.mark_frame_selected(frame_number)
        return OperationSuccess(
            FrameSelectionInfo(frame_number, frame_record(locations[frame_number], frame_number))
        )

    def _variable(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
        *,
        deferred_call: int | None = None,
    ) -> dict[str, object]:
        response = self._bridge._request(
            "Eval",
            {
                "Scope": self.scope(thread_id, frame, deferred_call=deferred_call),
                "Expr": expression,
                "Cfg": VARIABLE_LOAD_CONFIG,
            },
        )
        variable = mapping(response.get("Variable"))
        if not variable:
            raise DebuggerDecodeError("Delve Eval response has no variable")
        if variable.get("unreadable"):
            raise DelveRpcError(text(variable["unreadable"]), "value_unavailable")
        return variable

    @_operation
    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
        *,
        deferred_call: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo]:
        variable = self._variable(expression, thread_id, frame, deferred_call=deferred_call)
        return OperationSuccess(
            ExpressionValueInfo(
                expression, variable_value(variable), type=text(variable.get("type"))
            )
        )

    @_operation
    def get_variables(
        self, thread_id: int | None = None, frame: int = 0, *, deferred_call: int | None = None
    ) -> OperationSuccess[VariablesInfo]:
        arguments: dict[str, object] = {
            "Scope": self.scope(thread_id, frame, deferred_call=deferred_call),
            "Cfg": VARIABLE_LOAD_CONFIG,
        }
        locals_response = self._bridge._request("ListLocalVars", arguments)
        args_response = self._bridge._request("ListFunctionArgs", arguments)
        variables = [
            variable_record(value, argument=True) for value in mappings(args_response.get("Args"))
        ]
        variables += [
            variable_record(value) for value in mappings(locals_response.get("Variables"))
        ]
        return OperationSuccess(
            VariablesInfo(
                thread_id if thread_id is not None else self.runtime.current_thread_id,
                frame,
                variables,
            )
        )

    @_operation
    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo]:
        scope = self.scope(thread_id, frame)
        response = self._bridge._request(
            "ListRegisters", {"Scope": scope, "IncludeFp": include_vector_registers}
        )
        registers = mappings(response.get("Regs"))
        names = {name.casefold() for name in register_names or ()}
        family = guess_register_family([text(value.get("Name")) for value in registers])
        records: list[RegisterRecord] = []
        for register in registers:
            name = text(register.get("Name"))
            number = integer(register.get("DwarfNumber"))
            if register_numbers is not None and number not in register_numbers:
                continue
            if names and name.casefold() not in names:
                continue
            if not include_vector_registers and is_vector_register(family, name):
                continue
            value = text(register.get("Value"))
            if value_format == "natural":
                try:
                    value = str(int(value, 0))
                except ValueError:
                    pass
            records.append(
                RegisterRecord.from_payload(
                    {**register, "number": number, "name": name, "value": value}
                )
            )
        if max_registers is not None:
            records = records[:max_registers]
        return OperationSuccess(RegistersInfo(records))

    def address(self, expression: str) -> int:
        try:
            return int(expression, 0)
        except ValueError:
            variable = self._variable(expression)
            value = text(variable.get("value"))
            if value:
                try:
                    return int(value, 0)
                except ValueError as exc:
                    raise DebuggerDecodeError(
                        f"Delve address is not an integer: {value!r}"
                    ) from exc
            children = mappings(variable.get("children"))
            if children:
                return integer(children[0].get("addr"))
            return integer(variable.get("addr"))

    @_operation
    def read_memory(
        self, address: str, count: int, *, offset: int = 0
    ) -> OperationSuccess[MemoryReadInfo]:
        self.scope()
        begin = self.address(address) + offset
        if begin < 0 or count < 0:
            raise DelveRpcError("Memory address and count must be nonnegative", "validation_error")
        contents = bytearray()
        while len(contents) < count:
            requested = min(self.memory_read_limit, count - len(contents))
            response = self._bridge._request(
                "ExamineMemory", {"Address": begin + len(contents), "Length": requested}
            )
            raw = response.get("Mem")
            if not isinstance(raw, str):
                raise DelveRpcError("Delve memory response is not base64", "protocol_error")
            try:
                data = base64.b64decode(raw, validate=True)
            except ValueError as exc:
                raise DebuggerDecodeError("Delve memory response is not base64") from exc
            if not data or len(data) > requested:
                raise DelveRpcError("Delve memory response has an invalid length", "protocol_error")
            contents.extend(data)
        blocks = (
            [
                MemoryBlockRecord(
                    begin=hex(begin),
                    end=hex(begin + len(contents)),
                    offset=hex(offset),
                    contents=contents.hex(),
                )
            ]
            if contents
            else []
        )
        return OperationSuccess(MemoryReadInfo(address, count, offset, blocks, len(contents)))

    def locations(
        self, expression: str, thread_id: int | None = None, frame: int | None = None
    ) -> list[dict[str, object]]:
        response = self._bridge._request(
            "FindLocation", {"Scope": self.scope(thread_id, frame), "Loc": expression}
        )
        locations = mappings(response.get("Locations"))
        if not locations:
            raise DelveRpcError(f"No code location for {expression!r}", "not_found")
        return locations

    def _location(
        self,
        *,
        thread_id: int | None,
        frame: int | None,
        selector: DisassemblyLocation,
    ) -> tuple[dict[str, object], InspectionLocation]:
        function: str | None = None
        file: str | None = None
        line: int | None = None
        if isinstance(selector, FunctionLocation):
            function = selector.function
            location = self.locations(function, thread_id, frame)[0]
            kind: Literal["current", "function", "address", "file_line"] = "function"
        elif isinstance(selector, (AddressLocation, AddressRangeLocation)):
            address = (
                selector.address
                if isinstance(selector, AddressLocation)
                else selector.start_address
            )
            location = self.locations(f"*{hex(self.address(address))}", thread_id, frame)[0]
            kind = "address"
        elif isinstance(selector, FileLineLocation):
            file, line = selector.file, selector.line
            location = self.locations(f"{file}:{line}", thread_id, frame)[0]
            kind = "file_line"
        else:
            if isinstance(selector, FileRangeLocation):
                file = selector.file
            number = frame if frame is not None else self.runtime.current_frame or 0
            stack = self._stack(thread_id, number + 1)
            if number < 0 or number >= len(stack):
                raise DelveRpcError("No stack frame available", "no_stack_frame")
            location = stack[number]
            kind = "current"
        normalized = InspectionLocation(
            kind=kind,
            function=text(mapping(location.get("function")).get("name")) or function,
            address=hex(integer(location.get("pc"))),
            file=text(location.get("file")) or file,
            fullname=text(location.get("file")) or file,
            line=optional_integer(location.get("line")) or line,
        )
        return location, normalized

    @_operation
    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo]:
        thread_id, frame = request.thread_id, request.frame
        self.scope(thread_id, frame)
        selector = request.location
        if isinstance(selector, FileRangeLocation):
            location = InspectionLocation(
                kind="file_range",
                file=selector.file,
                fullname=selector.file,
                line=selector.start_line,
            )
            current_line = 0
        else:
            _, location = self._location(
                thread_id=thread_id,
                frame=frame,
                selector=selector,
            )
            current_line = location.line or 1
        path = Path(location.fullname or location.file or "")
        if (
            not path.is_absolute()
            and self.runtime.config is not None
            and self.runtime.config.working_dir
        ):
            path = Path(self.runtime.config.working_dir) / path
        source = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if isinstance(selector, FileRangeLocation):
            first, last = selector.start_line, min(len(source), selector.end_line)
        else:
            first = max(1, current_line - request.context_before)
            last = min(len(source), current_line + request.context_after)
        if first < 1 or last < first:
            raise DelveRpcError("Requested source range is unavailable", "not_found")
        lines = [
            SourceLineRecord(
                line_number=number, text=source[number - 1], is_current=number == current_line
            )
            for number in range(first, last + 1)
        ]
        return OperationSuccess(
            SourceContextInfo(
                InspectionContextInfo(
                    thread_id if thread_id is not None else self.runtime.current_thread_id,
                    frame if frame is not None else self.runtime.current_frame,
                ),
                location,
                SourceRange(first, last),
                lines,
            )
        )

    @_operation
    def disassemble(self, request: DisassemblyRequest) -> OperationSuccess[DisassemblyInfo]:
        thread_id, frame = request.thread_id, request.frame
        scope = self.scope(thread_id, frame)
        raw, location = self._location(
            thread_id=thread_id,
            frame=frame,
            selector=request.location,
        )
        start = integer(raw.get("pc"))
        end = 0
        if isinstance(request.location, AddressRangeLocation):
            end = self.address(request.location.end_address)
        response = self._bridge._request(
            "Disassemble", {"Scope": scope, "StartPC": start, "EndPC": end, "Flavour": 0}
        )
        instructions: list[DisassemblyInstructionRecord] = []
        for item in mappings(response.get("Disassemble")):
            loc = mapping(item.get("Loc"))
            pc = integer(loc.get("pc"))
            if pc < start:
                continue
            encoded = item.get("Bytes")
            try:
                opcodes = base64.b64decode(encoded).hex(" ") if isinstance(encoded, str) else None
            except ValueError as exc:
                raise DebuggerDecodeError("Delve instruction bytes are not base64") from exc
            instructions.append(
                DisassemblyInstructionRecord.from_payload(
                    {
                        **item,
                        "address": hex(pc),
                        "instruction": text(item.get("Text")),
                        "opcodes": opcodes,
                        "function": text(mapping(loc.get("function")).get("name")),
                        "file": text(loc.get("file")) if request.mode == "mixed" else None,
                        "line": (
                            optional_integer(loc.get("line")) if request.mode == "mixed" else None
                        ),
                        "is_current": bool(item.get("AtPC")),
                    }
                )
            )
            if len(instructions) >= request.instruction_count:
                break
        return OperationSuccess(
            DisassemblyInfo(
                InspectionContextInfo(
                    thread_id if thread_id is not None else self.runtime.current_thread_id,
                    frame if frame is not None else self.runtime.current_frame,
                ),
                location,
                AddressRange(hex(start), hex(end) if end else None),
                request.mode,
                instructions,
            )
        )
