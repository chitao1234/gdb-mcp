"""Disassembly resolution and normalization methods."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from ...domain import (
    DisassemblyInfo,
    DisassemblyInstructionRecord,
    OperationError,
    OperationSuccess,
)
from ...domain.parsing import as_int, as_optional_mapping, as_text_or_number
from ...transport import (
    extract_mi_result_payload,
    quote_mi_string,
)
from ..constants import DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from .base import (
    InspectionContext,
    _ResolvedCodeLocation,
    _SelectionSnapshot,
)

# `-data-disassemble -a` (whole function by name or address) was added in GDB 8.3
# and is advertised through `-list-features`. Older GDB resolves the same range
# through the CLI `disassemble`, whose output prefixes every instruction with its
# address.
_FEATURE_DISASSEMBLE_A = "data-disassemble-a-option"
_CLI_DISASSEMBLE_INSN_ADDRESS_RE = re.compile(r"^\s*(?:=>\s*)?(0x[0-9a-fA-F]+)\s+<", re.MULTILINE)


class InspectionDisassembly:
    """Disassembly resolution and normalization methods."""

    def __init__(self, context: InspectionContext):
        self._context = context
        self._runtime = context.runtime
        self._command_runner = context.command_runner

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
                captured_selection = self._context.capture_selection()
                if isinstance(captured_selection, OperationError):
                    return captured_selection
                selection = captured_selection

            selection_changed, selection_error = self._context.select_for_inspection(
                selection,
                thread_id=thread_id,
                frame=frame,
            )
            if selection_error is not None:
                return self._context.selection_error_with_restore(selection, selection_error)

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
            return self._context.selection_error_with_restore(selection, location_result)

        command = self._build_disassemble_command(
            location_result,
            instruction_count=instruction_count,
            mode=mode,
        )
        if isinstance(command, OperationError):
            if selection is None:
                return command
            return self._context.selection_error_with_restore(selection, command)

        result = self._command_runner.execute_command_result(
            command, timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            if selection is None:
                return result
            return self._context.selection_error_with_restore(selection, result)

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
            restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
            if restore_error is not None:
                return restore_error

        return OperationSuccess(info)

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
                return self._whole_function_command(location.address, mode_token=mode_token)
            return OperationError(
                message="Unable to resolve a current source line or address for disassembly"
            )

        if location.scope == "function" and location.function is not None:
            return self._whole_function_command(location.function, mode_token=mode_token)
        if location.scope == "address" and location.address is not None:
            return self._whole_function_command(location.address, mode_token=mode_token)
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

    def _whole_function_command(self, target: str, *, mode_token: str) -> str | OperationError:
        """Build the command that disassembles the whole function containing TARGET.

        ``-data-disassemble -a`` only exists from GDB 8.3 on. Older GDB still
        disassembles a whole function through the CLI ``disassemble``, so resolve
        that range from its output and hand it to ``-data-disassemble -s/-e``,
        which returns the same structured payload.
        """

        if self._supports_disassemble_a():
            return f"-data-disassemble -a {quote_mi_string(target)} -- {mode_token}"

        address_range = self._whole_function_address_range(target)
        if isinstance(address_range, OperationError):
            return address_range
        start_address, end_address = address_range
        return f"-data-disassemble -s {start_address} -e {end_address} -- {mode_token}"

    def _supports_disassemble_a(self) -> bool:
        """Return whether this GDB supports ``-data-disassemble -a`` (GDB >= 8.3)."""

        cached = self._runtime.disassemble_a_supported
        if cached is not None:
            return cached

        result = self._command_runner.execute_command_result(
            "-list-features", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        supported = False
        if isinstance(result, OperationSuccess):
            payload = extract_mi_result_payload(command_result_payload(result))
            if isinstance(payload, dict):
                features = payload.get("features")
                if isinstance(features, list):
                    supported = _FEATURE_DISASSEMBLE_A in features
        self._runtime.disassemble_a_supported = supported
        return supported

    def _whole_function_address_range(self, target: str) -> tuple[str, str] | OperationError:
        """Return the ``[start, end)`` address range of the function holding TARGET."""

        result = self._command_runner.execute_command_result(
            f"disassemble {target}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        addresses = sorted(
            int(address, 16)
            for address in _CLI_DISASSEMBLE_INSN_ADDRESS_RE.findall(result.value.output or "")
        )
        if not addresses:
            return OperationError(
                message=f"GDB returned no disassembly for {target!r}",
                code="disassembly_unavailable",
            )
        return (hex(addresses[0]), hex(addresses[-1] + 1))

    def _normalize_disassembly_payload(
        self,
        payload: Mapping[str, object] | None,
        *,
        current_address: str | None,
    ) -> list[DisassemblyInstructionRecord]:
        """Flatten MI disassembly payloads into stable instruction records."""

        if payload is None:
            return []

        raw_instructions = payload.get("asm_insns")
        if not isinstance(raw_instructions, list):
            return []

        instructions: list[DisassemblyInstructionRecord] = []
        for entry in raw_instructions:
            entry_payload = as_optional_mapping(entry)
            if entry_payload is None:
                continue

            nested = entry_payload.get("line_asm_insn")
            if isinstance(nested, list):
                line = as_int(entry_payload.get("line"))
                file = as_text_or_number(entry_payload.get("file"))
                fullname = as_text_or_number(entry_payload.get("fullname"))
                for raw_instruction in nested:
                    instruction_payload = as_optional_mapping(raw_instruction)
                    if instruction_payload is None:
                        continue
                    record = self._normalize_disassembly_instruction(
                        instruction_payload,
                        file=file,
                        fullname=fullname,
                        line=line,
                        current_address=current_address,
                    )
                    if record is not None:
                        instructions.append(record)
                continue

            record = self._normalize_disassembly_instruction(
                entry_payload,
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
        payload: Mapping[str, object],
        *,
        file: str | None,
        fullname: str | None,
        line: int | None,
        current_address: str | None,
    ) -> DisassemblyInstructionRecord | None:
        """Normalize one MI disassembly instruction record."""

        address = as_text_or_number(payload.get("address"))
        instruction = as_text_or_number(payload.get("inst"))
        if address is None or instruction is None:
            return None

        record: dict[str, object] = {
            "address": address,
            "instruction": instruction,
        }
        function = as_text_or_number(payload.get("func-name")) or as_text_or_number(
            payload.get("func")
        )
        if function is not None:
            record["function"] = function
        offset = as_int(payload.get("offset"))
        if offset is not None:
            record["offset"] = offset
        opcodes = as_text_or_number(payload.get("opcodes"))
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
        return DisassemblyInstructionRecord.from_payload(record)

    @staticmethod
    def _first_instruction_address(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first instruction address when available."""

        if not instructions:
            return None
        address = instructions[0].address
        return address if isinstance(address, str) else None

    @staticmethod
    def _last_instruction_address(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the last instruction address when available."""

        if not instructions:
            return None
        address = instructions[-1].address
        return address if isinstance(address, str) else None

    @staticmethod
    def _first_instruction_function(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first function label present in the instruction list."""

        for instruction in instructions:
            function = instruction.function
            if isinstance(function, str):
                return function
        return None

    @staticmethod
    def _first_instruction_file(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first source file present in the instruction list."""

        for instruction in instructions:
            file = instruction.file
            if isinstance(file, str):
                return file
        return None

    @staticmethod
    def _first_instruction_fullname(
        instructions: list[DisassemblyInstructionRecord],
    ) -> str | None:
        """Return the first full source path present in the instruction list."""

        for instruction in instructions:
            fullname = instruction.fullname
            if isinstance(fullname, str):
                return fullname
        return None

    @staticmethod
    def _first_instruction_line(
        instructions: list[DisassemblyInstructionRecord],
    ) -> int | None:
        """Return the first source line present in the instruction list."""

        for instruction in instructions:
            line = instruction.line
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
