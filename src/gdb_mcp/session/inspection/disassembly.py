"""Disassembly resolution and normalization methods."""

from __future__ import annotations

from typing import Literal

from ...domain import (
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    OperationError,
    OperationSuccess,
)
from ...transport import (
    extract_mi_result_payload,
    quote_mi_string,
)
from ..constants import DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from .base import (
    InspectionBase,
    _INFO_LINE_RE,
    _ResolvedCodeLocation,
    _SelectionSnapshot,
)


class InspectionDisassemblyMixin(InspectionBase):
    """Disassembly resolution and normalization methods."""

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

        if all(
            value is None for value in (function, address, start_address, end_address, file, line)
        ):
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
                    else (
                        selection.thread_id
                        if selection is not None
                        else self._runtime.current_thread_id
                    )
                ),
                frame=(
                    frame
                    if frame is not None
                    else (
                        selection.frame_number
                        if selection is not None
                        else self._runtime.current_frame
                    )
                ),
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
            location_result = _ResolvedCodeLocation(
                scope="file_line", file=file, fullname=file, line=line
            )
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

        result = self._command_runner.execute_command_result(
            command, timeout_sec=DEFAULT_TIMEOUT_SEC
        )
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
            start_address=location_result.start_address
            or self._first_instruction_address(instructions),
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
            return OperationError(
                message="Unable to resolve a current source line or address for disassembly"
            )

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
        return OperationError(
            message="Unable to build a disassembly command for the resolved selector"
        )

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
        function = self._str_or_none(payload.get("func-name")) or self._str_or_none(
            payload.get("func")
        )
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
