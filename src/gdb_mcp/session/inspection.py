"""Inspection and navigation operations for a composed SessionService."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Literal, Optional, cast

from ..domain import (
    BacktraceInfo,
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    ExpressionValueInfo,
    FrameInfo,
    FrameSelectionInfo,
    InferiorListInfo,
    InferiorRecord,
    InferiorSelectionInfo,
    MemoryReadInfo,
    OperationError,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    SourceContextInfo,
    SourceLineRecord,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    backtrace_info_from_payload,
    frame_info_from_payload,
    frame_selection_info_from_payload,
    memory_block_records,
    registers_info_from_payload,
    thread_list_info_from_payload,
    thread_selection_info_from_payload,
    variables_info_from_payload,
)
from ..transport import (
    build_evaluate_expression_command,
    build_read_memory_command,
    extract_mi_result_payload,
    quote_mi_string,
)
from .command_runner import SessionCommandRunner
from .constants import DEFAULT_MAX_BACKTRACE_FRAMES, DEFAULT_TIMEOUT_SEC
from .inferiors import inferior_ids, looks_like_connection, parse_inferiors_output
from .result_utils import command_result_payload
from .runtime import SessionRuntime

logger = logging.getLogger(__name__)
_INFO_LINE_RE = re.compile(
    r'^Line (?P<line>\d+) of "(?P<file>.+)" starts at address '
    r'(?P<start>0x[0-9a-fA-F]+)(?: <[^>]+>)? and ends at (?P<end>0x[0-9a-fA-F]+)',
    re.MULTILINE,
)
_VECTOR_REGISTER_NAME_RE = re.compile(
    r"^(?:xmm[0-9]+|ymm[0-9]+|zmm[0-9]+|mm[0-9]+|st(?:\([0-9]+\)|[0-9]+)?|k[0-9]+|v[0-9]+|q[0-9]+|d[0-9]+|s[0-9]+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _SelectionSnapshot:
    """Current thread/frame selection captured for temporary inspection changes."""

    thread_id: int | None
    frame_number: int | None


@dataclass(frozen=True)
class _ResolvedCodeLocation:
    """One normalized code location used by disassembly and source lookup helpers."""

    scope: str
    thread_id: int | None = None
    frame: int | None = None
    function: str | None = None
    address: str | None = None
    file: str | None = None
    fullname: str | None = None
    line: int | None = None
    start_address: str | None = None
    end_address: str | None = None


@dataclass(frozen=True)
class _ResolvedSourceWindow:
    """A concrete source-file line window ready for serialization."""

    file: str
    fullname: str | None
    start_line: int
    end_line: int
    lines: list[SourceLineRecord]


class SessionInspectionService:
    """Inspection and navigation helpers."""

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        self._runtime = runtime
        self._command_runner = command_runner

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Get information about all threads in the debugged process."""
        logger.debug("get_threads() called")
        result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        logger.debug("get_threads: execute_command returned: %s", result)

        if isinstance(result, OperationError):
            logger.debug("get_threads: returning error from execute_command")
            return result

        thread_info = extract_mi_result_payload(command_result_payload(result))
        logger.debug("get_threads: thread_info type=%s, value=%s", type(thread_info), thread_info)

        if thread_info is None:
            logger.warning("get_threads: thread_info is None - GDB returned incomplete data")
            return OperationError(
                message="GDB returned incomplete data - may still be loading symbols"
            )
        payload = thread_list_info_from_payload(thread_info)
        logger.debug(
            "get_threads: found %s threads, current_thread_id=%s",
            payload.count,
            payload.current_thread_id,
        )
        logger.debug("get_threads: threads data: %s", payload.threads)

        return OperationSuccess(payload)

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """List the inferiors currently managed by this GDB session."""

        result = self._command_runner.execute_command_result(
            "info inferiors", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        payload = self._parse_inferiors_output(result.value.output or "")
        self._runtime.update_inferior_inventory(
            current_inferior_id=payload.current_inferior_id,
            count=payload.count,
            inferior_ids=inferior_ids(payload),
        )
        inferiors_with_state = self._enrich_inferiors_with_runtime_state(payload.inferiors)
        payload = InferiorListInfo(
            inferiors=inferiors_with_state,
            count=payload.count,
            current_inferior_id=payload.current_inferior_id,
        )
        return OperationSuccess(payload)

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        """Select a specific inferior to make it the current debugger context."""

        result = self._command_runner.execute_command_result(
            f"inferior {inferior_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        self._runtime.mark_inferior_selected(inferior_id)
        inventory_result = self.list_inferiors()
        if isinstance(inventory_result, OperationError):
            return OperationSuccess(
                InferiorSelectionInfo(
                    inferior_id=inferior_id,
                    message=f"Inferior {inferior_id} selected",
                ),
                warnings=(
                    "Inferior selection succeeded, but refreshing inferior inventory failed: "
                    f"{inventory_result.message}",
                ),
            )

        selected_record = next(
            (
                record
                for record in inventory_result.value.inferiors
                if record.get("inferior_id") == inferior_id
            ),
            None,
        )
        if selected_record is None:
            return OperationSuccess(
                InferiorSelectionInfo(
                    inferior_id=inferior_id,
                    message=f"Inferior {inferior_id} selected",
                ),
                warnings=(
                    f"GDB selected inferior {inferior_id}, but it was missing from the refreshed inventory.",
                ),
            )

        return OperationSuccess(self._inferior_selection_info(selected_record))

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Select a specific thread to make it the current thread."""
        result = self._command_runner.execute_command_result(
            f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        if isinstance(raw_payload, dict):
            frame_payload = raw_payload.get("frame")
            if isinstance(frame_payload, dict):
                self._runtime.mark_frame_selected(self._int_or_none(frame_payload.get("level")))
        self._runtime.mark_thread_selected(thread_id)

        return OperationSuccess(
            thread_selection_info_from_payload(
                thread_id,
                raw_payload,
            )
        )

    def get_backtrace(
        self, thread_id: Optional[int] = None, max_frames: int = DEFAULT_MAX_BACKTRACE_FRAMES
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Get the stack backtrace for a specific thread or the current thread."""
        selection = self._capture_selection() if thread_id is not None else None
        if isinstance(selection, OperationError):
            return selection

        if thread_id is not None and (selection is None or selection.thread_id != thread_id):
            switch_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(switch_result, OperationError):
                return switch_result

        result = self._command_runner.execute_command_result(
            f"-stack-list-frames 0 {max_frames - 1}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None and selection.thread_id != thread_id:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        effective_thread_id = (
            thread_id if thread_id is not None else self._runtime.current_thread_id
        )
        payload = backtrace_info_from_payload(
            effective_thread_id,
            extract_mi_result_payload(command_result_payload(result)),
        )

        if selection is not None and selection.thread_id != thread_id:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(payload)

    def get_frame_info(self) -> OperationSuccess[FrameInfo] | OperationError:
        """Get information about the current stack frame."""
        result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        frame_info = frame_info_from_payload(
            extract_mi_result_payload(command_result_payload(result))
        )
        level = frame_info.frame.get("level")
        self._runtime.mark_frame_selected(self._int_or_none(level))

        return OperationSuccess(frame_info)

    def disassemble(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        start_address: str | None = None,
        end_address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        instruction_count: int = 32,
        mode: Literal["assembly", "mixed"] = "mixed",
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        """Return structured disassembly for one resolved code location."""

        selection: _SelectionSnapshot | None = None
        location_result: _ResolvedCodeLocation | OperationError

        if all(value is None for value in (function, address, start_address, end_address, file, line)):
            if thread_id is not None or frame is not None:
                captured_selection = self._capture_selection()
                if isinstance(captured_selection, OperationError):
                    return captured_selection
                selection = captured_selection

            selection_error = self._select_for_inspection(
                selection,
                thread_id=thread_id,
                frame=frame,
            )
            if selection_error is not None:
                return self._selection_error_with_restore(selection, selection_error)

            location_result = _ResolvedCodeLocation(
                scope="current_context",
                thread_id=(
                    thread_id
                    if thread_id is not None
                    else selection.thread_id if selection is not None else self._runtime.current_thread_id
                ),
                frame=frame if frame is not None else selection.frame_number if selection is not None else self._runtime.current_frame,
                address="$pc",
            )
        elif function is not None:
            location_result = _ResolvedCodeLocation(scope="function", function=function)
        elif address is not None:
            location_result = _ResolvedCodeLocation(scope="address", address=address)
        elif start_address is not None and end_address is not None:
            location_result = _ResolvedCodeLocation(
                scope="address_range",
                start_address=start_address,
                end_address=end_address,
            )
        elif file is not None and line is not None:
            location_result = _ResolvedCodeLocation(scope="file_line", file=file, fullname=file, line=line)
        else:
            return OperationError(message="Invalid disassembly selector combination")

        if isinstance(location_result, OperationError):
            if selection is None:
                return location_result
            return self._selection_error_with_restore(selection, location_result)

        command = self._build_disassemble_command(
            location_result,
            instruction_count=instruction_count,
            mode=mode,
        )
        if isinstance(command, OperationError):
            if selection is None:
                return command
            return self._selection_error_with_restore(selection, command)

        result = self._command_runner.execute_command_result(command, timeout_sec=DEFAULT_TIMEOUT_SEC)
        if isinstance(result, OperationError):
            if selection is None:
                return result
            return self._selection_error_with_restore(selection, result)

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        instructions = self._normalize_disassembly_payload(
            raw_payload,
            current_address=location_result.address,
        )
        instructions = instructions[:instruction_count]

        info = DisassemblyInfo(
            scope=location_result.scope,
            thread_id=location_result.thread_id,
            frame=location_result.frame,
            function=location_result.function or self._first_instruction_function(instructions),
            file=location_result.file or self._first_instruction_file(instructions),
            fullname=location_result.fullname or self._first_instruction_fullname(instructions),
            line=location_result.line or self._first_instruction_line(instructions),
            start_address=location_result.start_address or self._first_instruction_address(instructions),
            end_address=location_result.end_address or self._last_instruction_address(instructions),
            mode=mode,
            instructions=instructions,
            count=len(instructions),
        )

        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(info)

    def get_source_context(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
        function: str | None = None,
        address: str | None = None,
        file: str | None = None,
        line: int | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        context_before: int = 5,
        context_after: int = 5,
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        """Return structured source lines for one resolved code location."""

        selection: _SelectionSnapshot | None = None
        location_result: _ResolvedCodeLocation | OperationError

        if all(value is None for value in (function, address, file, line, start_line, end_line)):
            if thread_id is not None or frame is not None:
                captured_selection = self._capture_selection()
                if isinstance(captured_selection, OperationError):
                    return captured_selection
                selection = captured_selection

            selection_error = self._select_for_inspection(
                selection,
                thread_id=thread_id,
                frame=frame,
            )
            if selection_error is not None:
                return self._selection_error_with_restore(selection, selection_error)

            location_result = self._current_frame_location(
                thread_id=thread_id,
                frame=frame,
                selection=selection,
            )
        elif function is not None:
            location_result = self._resolve_info_line_location(
                command=f"info line {function}",
                scope="function",
                function=function,
                address=None,
            )
        elif address is not None:
            location_result = self._resolve_info_line_location(
                command=f"info line *{address}",
                scope="address",
                function=None,
                address=address,
            )
        elif file is not None and line is not None:
            location_result = _ResolvedCodeLocation(
                scope="file_line",
                file=file,
                fullname=file,
                line=line,
            )
        elif file is not None and start_line is not None and end_line is not None:
            location_result = _ResolvedCodeLocation(
                scope="file_range",
                file=file,
                fullname=file,
                line=None,
            )
        else:
            return OperationError(message="Invalid source context selector combination")

        if isinstance(location_result, OperationError):
            if selection is None:
                return location_result
            return self._selection_error_with_restore(selection, location_result)

        read_result = self._read_source_context(
            location_result,
            requested_start_line=start_line,
            requested_end_line=end_line,
            context_before=context_before,
            context_after=context_after,
        )
        if isinstance(read_result, OperationError):
            if selection is None:
                return read_result
            return self._selection_error_with_restore(selection, read_result)

        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(
            SourceContextInfo(
                scope=location_result.scope,
                thread_id=location_result.thread_id,
                frame=location_result.frame,
                function=location_result.function,
                address=location_result.address,
                file=read_result.file,
                fullname=read_result.fullname,
                line=location_result.line,
                start_line=read_result.start_line,
                end_line=read_result.end_line,
                lines=read_result.lines,
                count=len(read_result.lines),
            )
        )

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Select a specific stack frame to make it the current frame."""
        result = self._command_runner.execute_command_result(
            f"-stack-select-frame {frame_number}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            return result

        self._runtime.mark_frame_selected(frame_number)

        frame_info_result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(frame_info_result, OperationError):
            return OperationSuccess(
                FrameSelectionInfo(
                    frame_number=frame_number,
                    message=f"Frame {frame_number} selected",
                )
            )

        return OperationSuccess(
            frame_selection_info_from_payload(
                frame_number,
                extract_mi_result_payload(command_result_payload(frame_info_result)),
            )
        )

    def evaluate_expression(
        self,
        expression: str,
        thread_id: Optional[int] = None,
        frame: Optional[int] = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate an expression in the current context."""
        selection = (
            self._capture_selection() if thread_id is not None or frame is not None else None
        )
        if isinstance(selection, OperationError):
            return selection

        selection_error = self._select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._selection_error_with_restore(selection, selection_error)

        result = self._command_runner.execute_command_result(
            build_evaluate_expression_command(expression), timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            if selection is not None:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        value = raw_payload.get("value") if isinstance(raw_payload, dict) else None
        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(ExpressionValueInfo(expression=expression, value=value))

    def read_memory(
        self,
        address: str,
        count: int,
        *,
        offset: int = 0,
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Read raw target memory bytes from one address expression."""

        result = self._command_runner.execute_command_result(
            build_read_memory_command(address, count, offset=offset),
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )

        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        blocks = memory_block_records(payload)
        captured_bytes = sum(len(block.get("contents", "")) // 2 for block in blocks)
        return OperationSuccess(
            MemoryReadInfo(
                address=address,
                count=count,
                offset=offset,
                blocks=blocks,
                block_count=len(blocks),
                captured_bytes=captured_bytes,
            )
        )

    def get_variables(
        self, thread_id: Optional[int] = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Get local variables for a specific frame."""
        selection = self._capture_selection()
        if isinstance(selection, OperationError):
            return selection

        selection_error = self._select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._selection_error_with_restore(selection, selection_error)

        result = self._command_runner.execute_command_result(
            "-stack-list-variables --simple-values", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error
            return result

        effective_thread_id = thread_id if thread_id is not None else selection.thread_id
        payload = variables_info_from_payload(
            effective_thread_id,
            frame,
            extract_mi_result_payload(command_result_payload(result)),
        )
        restore_error = self._restore_selection(selection)
        if restore_error is not None:
            return restore_error

        return OperationSuccess(payload)

    def get_registers(
        self,
        thread_id: Optional[int] = None,
        frame: Optional[int] = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        """Get register values for current frame."""
        selection = (
            self._capture_selection() if thread_id is not None or frame is not None else None
        )
        if isinstance(selection, OperationError):
            return selection

        selection_error = self._select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._selection_error_with_restore(selection, selection_error)

        resolved_numbers = self._resolve_register_number_filters(
            register_numbers=register_numbers or [],
            register_names=register_names or [],
        )
        if isinstance(resolved_numbers, OperationError):
            if selection is not None:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return resolved_numbers

        format_token = "x" if value_format == "hex" else "N"
        registers_cmd = self._register_values_command(format_token, resolved_numbers)
        result = self._command_runner.execute_command_result(
            registers_cmd,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )

        if isinstance(result, OperationError):
            if selection is not None:
                restore_error = self._restore_selection(selection)
                if restore_error is not None:
                    return restore_error
            return result

        payload = registers_info_from_payload(extract_mi_result_payload(command_result_payload(result)))
        filtered_registers = payload.registers

        if not include_vector_registers:
            name_map_result = self._load_register_name_map(filtered_registers)
            if isinstance(name_map_result, OperationError):
                if selection is not None:
                    restore_error = self._restore_selection(selection)
                    if restore_error is not None:
                        return restore_error
                return name_map_result
            filtered_registers = self._filter_vector_registers(
                filtered_registers,
                name_map=name_map_result,
            )

        if max_registers is not None:
            filtered_registers = filtered_registers[:max_registers]

        payload = RegistersInfo(registers=filtered_registers)
        if selection is not None:
            restore_error = self._restore_selection(selection)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(payload)

    def _resolve_register_number_filters(
        self,
        *,
        register_numbers: list[int],
        register_names: list[str],
    ) -> list[int] | None | OperationError:
        """Resolve explicit register number/name selectors into one number list."""

        selected_numbers: list[int] = []
        seen_numbers: set[int] = set()
        for number in register_numbers:
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            selected_numbers.append(number)

        if register_names:
            name_to_number = self._register_name_to_number_index()
            if isinstance(name_to_number, OperationError):
                return name_to_number

            missing_names: list[str] = []
            for register_name in register_names:
                resolved_number = name_to_number.get(register_name)
                if resolved_number is None:
                    missing_names.append(register_name)
                    continue
                if resolved_number in seen_numbers:
                    continue
                seen_numbers.add(resolved_number)
                selected_numbers.append(resolved_number)

            if missing_names:
                return OperationError(
                    message=(
                        "Unknown register names: "
                        + ", ".join(sorted(missing_names))
                    )
                )

        return selected_numbers or None

    def _register_name_to_number_index(self) -> dict[str, int] | OperationError:
        """Build a name-to-number index from `-data-list-register-names` output."""

        result = self._command_runner.execute_command_result(
            "-data-list-register-names",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        if not isinstance(payload, dict):
            return OperationError(
                message="GDB returned malformed register-name payload"
            )

        raw_names = payload.get("register-names")
        if not isinstance(raw_names, list):
            return OperationError(
                message="GDB did not return register names in the expected format"
            )

        index: dict[str, int] = {}
        for number, raw_name in enumerate(raw_names):
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            index[name] = number
        return index

    def _load_register_name_map(
        self,
        registers: list[RegisterRecord],
    ) -> dict[int, str] | OperationError:
        """Load register names for one returned register set."""

        numbers: list[int] = []
        for register in registers:
            raw_number = register.get("number")
            number = self._int_or_none(raw_number)
            if number is None:
                continue
            numbers.append(number)

        if not numbers:
            return {}

        command = "-data-list-register-names " + " ".join(str(number) for number in numbers)
        result = self._command_runner.execute_command_result(
            command,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        if not isinstance(payload, dict):
            return OperationError(message="GDB returned malformed register-name payload")

        raw_names = payload.get("register-names")
        if not isinstance(raw_names, list):
            return OperationError(message="GDB did not return register names in the expected format")

        mapping: dict[int, str] = {}
        for index, raw_name in enumerate(raw_names):
            if index >= len(numbers):
                break
            if isinstance(raw_name, str):
                mapping[numbers[index]] = raw_name.strip()
        return mapping

    def _filter_vector_registers(
        self,
        registers: list[RegisterRecord],
        *,
        name_map: dict[int, str],
    ) -> list[RegisterRecord]:
        """Return register records with vector/SIMD names omitted."""

        filtered: list[RegisterRecord] = []
        for register in registers:
            raw_number = register.get("number")
            number = self._int_or_none(raw_number)
            name = name_map.get(number, "") if number is not None else ""
            if name and _VECTOR_REGISTER_NAME_RE.match(name):
                continue
            filtered.append(register)
        return filtered

    @staticmethod
    def _register_values_command(format_token: str, register_numbers: list[int] | None) -> str:
        """Build a register-values MI command with optional explicit register numbers."""

        if not register_numbers:
            return f"-data-list-register-values {format_token}"
        numbers = " ".join(str(number) for number in register_numbers)
        return f"-data-list-register-values {format_token} {numbers}"

    def _current_frame_location(
        self,
        *,
        thread_id: int | None,
        frame: int | None,
        selection: _SelectionSnapshot | None,
    ) -> _ResolvedCodeLocation | OperationError:
        """Resolve the current frame into a normalized code location."""

        result = self._command_runner.execute_command_result(
            "-stack-info-frame",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        raw_payload = extract_mi_result_payload(command_result_payload(result))
        if not isinstance(raw_payload, dict):
            return OperationError(message="GDB returned malformed frame data")

        frame_payload = raw_payload.get("frame")
        if not isinstance(frame_payload, dict):
            return OperationError(message="GDB did not return frame data")

        resolved_thread_id = (
            thread_id
            if thread_id is not None
            else selection.thread_id if selection is not None else self._runtime.current_thread_id
        )
        resolved_frame = (
            frame if frame is not None else self._int_or_none(frame_payload.get("level"))
        )
        return _ResolvedCodeLocation(
            scope="current_context",
            thread_id=resolved_thread_id,
            frame=resolved_frame,
            function=self._str_or_none(frame_payload.get("func")),
            address=self._str_or_none(frame_payload.get("addr")),
            file=self._str_or_none(frame_payload.get("file")),
            fullname=self._str_or_none(frame_payload.get("fullname")),
            line=self._int_or_none(frame_payload.get("line")),
        )

    def _build_disassemble_command(
        self,
        location: _ResolvedCodeLocation,
        *,
        instruction_count: int,
        mode: Literal["assembly", "mixed"],
    ) -> str | OperationError:
        """Build one MI disassembly command for the resolved selector mode."""

        mode_token = "0" if mode == "assembly" else "1"
        if location.scope in {"current_context", "file_line"}:
            file_selector = location.fullname or location.file
            if file_selector is not None and location.line is not None:
                return (
                    f"-data-disassemble -f {quote_mi_string(file_selector)} "
                    f"-l {location.line} -n {instruction_count} -- {mode_token}"
                )
            if location.address is not None:
                return f"-data-disassemble -a {quote_mi_string(location.address)} -- {mode_token}"
            return OperationError(message="Unable to resolve a current source line or address for disassembly")

        if location.scope == "function" and location.function is not None:
            return f"-data-disassemble -a {quote_mi_string(location.function)} -- {mode_token}"
        if location.scope == "address" and location.address is not None:
            return f"-data-disassemble -a {quote_mi_string(location.address)} -- {mode_token}"
        if (
            location.scope == "address_range"
            and location.start_address is not None
            and location.end_address is not None
        ):
            return (
                f"-data-disassemble -s {quote_mi_string(location.start_address)} "
                f"-e {quote_mi_string(location.end_address)} -- {mode_token}"
            )
        return OperationError(message="Unable to build a disassembly command for the resolved selector")

    def _normalize_disassembly_payload(
        self,
        payload: object,
        *,
        current_address: str | None,
    ) -> list[DisassemblyInstructionRecord]:
        """Flatten MI disassembly payloads into stable instruction records."""

        if not isinstance(payload, dict):
            return []

        raw_instructions = payload.get("asm_insns")
        if not isinstance(raw_instructions, list):
            return []

        instructions: list[DisassemblyInstructionRecord] = []
        for entry in raw_instructions:
            if not isinstance(entry, dict):
                continue

            nested = entry.get("line_asm_insn")
            if isinstance(nested, list):
                line = self._int_or_none(entry.get("line"))
                file = self._str_or_none(entry.get("file"))
                fullname = self._str_or_none(entry.get("fullname"))
                for raw_instruction in nested:
                    record = self._normalize_disassembly_instruction(
                        raw_instruction,
                        file=file,
                        fullname=fullname,
                        line=line,
                        current_address=current_address,
                    )
                    if record is not None:
                        instructions.append(record)
                continue

            record = self._normalize_disassembly_instruction(
                entry,
                file=None,
                fullname=None,
                line=None,
                current_address=current_address,
            )
            if record is not None:
                instructions.append(record)

        return instructions

    def _normalize_disassembly_instruction(
        self,
        payload: object,
        *,
        file: str | None,
        fullname: str | None,
        line: int | None,
        current_address: str | None,
    ) -> DisassemblyInstructionRecord | None:
        """Normalize one MI disassembly instruction record."""

        if not isinstance(payload, dict):
            return None

        address = self._str_or_none(payload.get("address"))
        instruction = self._str_or_none(payload.get("inst"))
        if address is None or instruction is None:
            return None

        record: DisassemblyInstructionRecord = {
            "address": address,
            "instruction": instruction,
        }
        function = self._str_or_none(payload.get("func-name")) or self._str_or_none(payload.get("func"))
        if function is not None:
            record["function"] = function
        offset = self._int_or_none(payload.get("offset"))
        if offset is not None:
            record["offset"] = offset
        opcodes = self._str_or_none(payload.get("opcodes"))
        if opcodes is not None:
            record["opcodes"] = opcodes
        if file is not None:
            record["file"] = file
        if fullname is not None:
            record["fullname"] = fullname
        if line is not None:
            record["line"] = line
        if current_address is not None and self._addresses_equal(address, current_address):
            record["is_current"] = True
        return record

    def _resolve_info_line_location(
        self,
        *,
        command: str,
        scope: str,
        function: str | None,
        address: str | None,
    ) -> _ResolvedCodeLocation | OperationError:
        """Resolve `info line ...` output into a normalized code location."""

        result = self._command_runner.execute_command_result(
            command,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        output = result.value.output or ""
        match = _INFO_LINE_RE.search(output)
        if match is None:
            return OperationError(message=f"GDB did not return source information for {command!r}")

        line = int(match.group("line"))
        file = match.group("file")
        return _ResolvedCodeLocation(
            scope=scope,
            function=function,
            address=address,
            file=file,
            fullname=file,
            line=line,
            start_address=match.group("start"),
            end_address=match.group("end"),
        )

    def _read_source_context(
        self,
        location: _ResolvedCodeLocation,
        *,
        requested_start_line: int | None,
        requested_end_line: int | None,
        context_before: int,
        context_after: int,
    ) -> _ResolvedSourceWindow | OperationError:
        """Read one source window from disk for the resolved location."""

        file_selector = location.fullname or location.file
        if file_selector is None:
            return OperationError(message="Resolved source location has no file path")

        path = Path(file_selector).expanduser()
        if not path.exists():
            return OperationError(message=f"Source file not found: {file_selector}")

        source_lines = path.read_text().splitlines()
        if requested_start_line is not None and requested_end_line is not None:
            start_line = requested_start_line
            end_line = requested_end_line
            current_line = None
        else:
            current_line = location.line
            if current_line is None:
                return OperationError(message="Resolved source location has no source line")
            start_line = max(1, current_line - context_before)
            end_line = current_line + context_after

        if start_line > len(source_lines):
            return OperationError(message=f"Source line {start_line} is outside {file_selector}")

        end_line = min(end_line, len(source_lines))
        lines: list[SourceLineRecord] = []
        for line_number in range(start_line, end_line + 1):
            record: SourceLineRecord = {
                "line_number": line_number,
                "text": source_lines[line_number - 1],
            }
            if current_line is not None and line_number == current_line:
                record["is_current"] = True
            lines.append(record)

        resolved_fullname = location.fullname or str(path.resolve())
        return _ResolvedSourceWindow(
            file=location.file or str(path),
            fullname=resolved_fullname,
            start_line=start_line,
            end_line=end_line,
            lines=lines,
        )

    @staticmethod
    def _first_instruction_address(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first instruction address when available."""

        if not instructions:
            return None
        address = instructions[0].get("address")
        return address if isinstance(address, str) else None

    @staticmethod
    def _last_instruction_address(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the last instruction address when available."""

        if not instructions:
            return None
        address = instructions[-1].get("address")
        return address if isinstance(address, str) else None

    @staticmethod
    def _first_instruction_function(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first function label present in the instruction list."""

        for instruction in instructions:
            function = instruction.get("function")
            if isinstance(function, str):
                return function
        return None

    @staticmethod
    def _first_instruction_file(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first source file present in the instruction list."""

        for instruction in instructions:
            file = instruction.get("file")
            if isinstance(file, str):
                return file
        return None

    @staticmethod
    def _first_instruction_fullname(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first full source path present in the instruction list."""

        for instruction in instructions:
            fullname = instruction.get("fullname")
            if isinstance(fullname, str):
                return fullname
        return None

    @staticmethod
    def _first_instruction_line(
        instructions: list[DisassemblyInstructionRecord],
    ) -> int | None:
        """Return the first source line present in the instruction list."""

        for instruction in instructions:
            line = instruction.get("line")
            if isinstance(line, int):
                return line
        return None

    @staticmethod
    def _addresses_equal(left: str, right: str) -> bool:
        """Compare address strings while tolerating formatting differences."""

        try:
            return int(left, 0) == int(right, 0)
        except ValueError:
            return left == right

    @staticmethod
    def _str_or_none(value: object) -> str | None:
        """Return a string-compatible scalar as text when possible."""

        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        return None

    def _capture_selection(self) -> _SelectionSnapshot | OperationError:
        """Capture the currently selected thread and frame for later restoration."""

        thread_result = self._command_runner.execute_command_result(
            "-thread-info", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(thread_result, OperationError):
            return thread_result

        thread_payload = extract_mi_result_payload(command_result_payload(thread_result))
        current_thread = None
        if isinstance(thread_payload, dict):
            current_thread = self._int_or_none(thread_payload.get("current-thread-id"))

        frame_result = self._command_runner.execute_command_result(
            "-stack-info-frame", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(frame_result, OperationError):
            return frame_result

        frame_payload = extract_mi_result_payload(command_result_payload(frame_result))
        current_frame = None
        if isinstance(frame_payload, dict):
            current_frame_payload = frame_payload.get("frame")
            if isinstance(current_frame_payload, dict):
                current_frame = self._int_or_none(current_frame_payload.get("level"))

        self._runtime.mark_thread_selected(current_thread)
        self._runtime.mark_frame_selected(current_frame)

        return _SelectionSnapshot(thread_id=current_thread, frame_number=current_frame)

    def _restore_selection(self, selection: _SelectionSnapshot) -> OperationError | None:
        """Restore a previously captured debugger selection."""

        if selection.thread_id is not None:
            thread_restore = self._command_runner.execute_command_result(
                f"-thread-select {selection.thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_restore, OperationError):
                return OperationError(
                    message=(
                        "Inspection completed but failed to restore the original thread selection: "
                        f"{thread_restore.message}"
                    )
                )

        if selection.frame_number is not None:
            frame_restore = self._command_runner.execute_command_result(
                f"-stack-select-frame {selection.frame_number}",
                timeout_sec=DEFAULT_TIMEOUT_SEC,
            )
            if isinstance(frame_restore, OperationError):
                return OperationError(
                    message=(
                        "Inspection completed but failed to restore the original frame selection: "
                        f"{frame_restore.message}"
                    )
                )

        self._runtime.mark_thread_selected(selection.thread_id)
        self._runtime.mark_frame_selected(selection.frame_number)
        return None

    def _select_for_inspection(
        self,
        selection: _SelectionSnapshot | None,
        *,
        thread_id: int | None,
        frame: int | None,
    ) -> OperationError | None:
        """Temporarily switch thread/frame for one inspection call."""

        if selection is not None and thread_id is not None and selection.thread_id != thread_id:
            thread_result = self._command_runner.execute_command_result(
                f"-thread-select {thread_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(thread_result, OperationError):
                return thread_result

        if selection is not None and frame is not None and selection.frame_number != frame:
            frame_result = self._command_runner.execute_command_result(
                f"-stack-select-frame {frame}", timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            if isinstance(frame_result, OperationError):
                return frame_result

        return None

    def _selection_error_with_restore(
        self,
        selection: _SelectionSnapshot | None,
        selection_error: OperationError,
    ) -> OperationError:
        """Return a selection error, restoring the original context when possible."""

        if selection is None:
            return selection_error

        restore_error = self._restore_selection(selection)
        if restore_error is None:
            return selection_error

        return OperationError(
            message=(
                f"{selection_error.message}. "
                "Also failed to restore the original thread/frame selection: "
                f"{restore_error.message}"
            )
        )

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        """Parse a GDB string/integer field into an integer when possible."""

        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _parse_inferiors_output(self, output: str) -> InferiorListInfo:
        """Parse `info inferiors` CLI output into a structured inferior list."""

        return parse_inferiors_output(
            output,
            current_inferior_id=self._runtime.current_inferior_id,
        )

    @staticmethod
    def _looks_like_connection(value: str) -> bool:
        """Heuristically identify a connection column from `info inferiors` output."""

        return looks_like_connection(value)

    @staticmethod
    def _inferior_selection_info(record: InferiorRecord) -> InferiorSelectionInfo:
        """Convert one inferior record into a selection response."""

        inferior_id = record["inferior_id"] if "inferior_id" in record else 0
        display = record["display"] if "display" in record else None
        description = record["description"] if "description" in record else None
        connection = record["connection"] if "connection" in record else None
        executable = record["executable"] if "executable" in record else None

        return InferiorSelectionInfo(
            inferior_id=inferior_id,
            is_current=bool(record.get("is_current", False)),
            display=display,
            description=description,
            connection=connection,
            executable=executable,
            message=(
                f"Inferior {inferior_id} selected" if inferior_id > 0 else "Inferior selected"
            ),
        )

    def _enrich_inferiors_with_runtime_state(
        self,
        inferiors: list[InferiorRecord],
    ) -> list[InferiorRecord]:
        """Attach runtime execution-state metadata to listed inferiors when known."""

        state_by_inferior = {
            record["inferior_id"]: record
            for record in self._runtime.inferiors_state_summary()
            if isinstance(record.get("inferior_id"), int)
        }

        enriched: list[InferiorRecord] = []
        for inferior in inferiors:
            inferior_id = inferior.get("inferior_id")
            if not isinstance(inferior_id, int):
                enriched.append(cast(InferiorRecord, dict(inferior)))
                continue

            state_record = state_by_inferior.get(inferior_id)
            if state_record is None:
                enriched.append(cast(InferiorRecord, dict(inferior)))
                continue

            enriched_record = cast(InferiorRecord, dict(inferior))
            execution_state = state_record.get("execution_state")
            if isinstance(execution_state, str):
                enriched_record["execution_state"] = execution_state
            enriched_record["stop_reason"] = (
                str(state_record["stop_reason"])
                if isinstance(state_record.get("stop_reason"), str)
                else None
            )
            exit_code = state_record.get("exit_code")
            enriched_record["exit_code"] = int(exit_code) if isinstance(exit_code, int) else None
            enriched.append(enriched_record)

        return enriched
