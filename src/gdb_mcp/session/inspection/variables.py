"""Variable and register inspection methods."""

from __future__ import annotations

from typing import Literal

from ...domain import (
    OperationError,
    OperationSuccess,
    RegisterRecord,
    RegistersInfo,
    VariablesInfo,
    registers_info_from_payload,
    variables_info_from_payload,
)
from ...transport import (
    extract_mi_result_payload,
)
from ..constants import DEFAULT_TIMEOUT_SEC
from ..result_utils import command_result_payload
from .base import InspectionContext
from .register_classes import (
    RegisterFamily,
    guess_register_family,
    is_vector_register,
)


class InspectionVariables:
    """Variable and register inspection methods."""

    def __init__(self, context: InspectionContext):
        self._context = context
        self._runtime = context.runtime
        self._command_runner = context.command_runner

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Get local variables for a specific frame."""
        selection = self._context.capture_selection()
        if isinstance(selection, OperationError):
            return selection

        selection_changed, selection_error = self._context.select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._context.selection_error_with_restore(selection, selection_error)

        result = self._command_runner.execute_command_result(
            "-stack-list-variables --simple-values", timeout_sec=DEFAULT_TIMEOUT_SEC
        )

        if isinstance(result, OperationError):
            restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
            if restore_error is not None:
                return restore_error
            return result

        effective_thread_id = thread_id if thread_id is not None else selection.thread_id
        payload = variables_info_from_payload(
            effective_thread_id,
            frame,
            extract_mi_result_payload(command_result_payload(result)),
        )
        restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
        if restore_error is not None:
            return restore_error

        return OperationSuccess(payload)

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
        """Get register values for current frame."""
        explicit_selector = bool(register_numbers or register_names)
        selection = (
            self._context.capture_selection()
            if thread_id is not None or frame is not None
            else None
        )
        if isinstance(selection, OperationError):
            return selection

        selection_changed, selection_error = self._context.select_for_inspection(
            selection,
            thread_id=thread_id,
            frame=frame,
        )
        if selection_error is not None:
            return self._context.selection_error_with_restore(selection, selection_error)

        resolved_numbers = self._resolve_register_number_filters(
            register_numbers=register_numbers or [],
            register_names=register_names or [],
        )
        if isinstance(resolved_numbers, OperationError):
            if selection is not None:
                restore_error = self._context.restore_selection_if_changed(
                    selection, selection_changed
                )
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
                restore_error = self._context.restore_selection_if_changed(
                    selection, selection_changed
                )
                if restore_error is not None:
                    return restore_error
            return result

        payload = registers_info_from_payload(
            extract_mi_result_payload(command_result_payload(result))
        )
        filtered_registers = payload.registers

        if not include_vector_registers and not explicit_selector:
            register_names_result = self._load_all_register_names()
            if isinstance(register_names_result, OperationError):
                if selection is not None:
                    restore_error = self._context.restore_selection_if_changed(
                        selection, selection_changed
                    )
                    if restore_error is not None:
                        return restore_error
                return register_names_result
            filtered_registers = self._filter_vector_registers(
                filtered_registers,
                register_names=register_names_result,
                family=guess_register_family(register_names_result),
            )

        if max_registers is not None:
            filtered_registers = filtered_registers[:max_registers]

        payload = RegistersInfo(registers=filtered_registers)
        if selection is not None:
            restore_error = self._context.restore_selection_if_changed(selection, selection_changed)
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
                    message=("Unknown register names: " + ", ".join(sorted(missing_names)))
                )

        return selected_numbers or None

    def _register_name_to_number_index(self) -> dict[str, int] | OperationError:
        """Build a name-to-number index from `-data-list-register-names` output."""

        names_result = self._load_all_register_names()
        if isinstance(names_result, OperationError):
            return names_result

        index: dict[str, int] = {}
        for number, name in enumerate(names_result):
            if not name:
                continue
            index[name] = number
        return index

    def _load_all_register_names(self) -> list[str] | OperationError:
        """Load every register name, indexed by register number."""

        result = self._command_runner.execute_command_result(
            "-data-list-register-names",
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
        if isinstance(result, OperationError):
            return result

        payload = extract_mi_result_payload(command_result_payload(result))
        if not isinstance(payload, dict):
            return OperationError(message="GDB returned malformed register-name payload")

        raw_names = payload.get("register-names")
        if not isinstance(raw_names, list):
            return OperationError(
                message="GDB did not return register names in the expected format"
            )

        return [raw_name.strip() if isinstance(raw_name, str) else "" for raw_name in raw_names]

    def _filter_vector_registers(
        self,
        registers: list[RegisterRecord],
        *,
        register_names: list[str],
        family: RegisterFamily | None,
    ) -> list[RegisterRecord]:
        """Return register records with vector/SIMD names omitted."""

        filtered: list[RegisterRecord] = []
        for register in registers:
            number = register.number
            name = (
                register_names[number]
                if number is not None and 0 <= number < len(register_names)
                else ""
            )
            if name and is_vector_register(family, name):
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
