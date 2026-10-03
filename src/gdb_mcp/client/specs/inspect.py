"""CLI parser, validation, and payload builders for inspection."""

from __future__ import annotations

import argparse


from gdb_mcp.contracts import (
    CLI_LOCATION_KIND_CHOICES,
    DISASSEMBLY_MODES,
    INSPECT_QUERY_ACTIONS,
    REGISTER_VALUE_FORMATS,
)
from gdb_mcp.mcp.schemas import (
    InspectQueryArgs,
)

from ..builders.inspect import build_inspect_query_payload
from ..inputs import (
    InspectQueryInput,
    LocationInput,
)
from ..parsers import (
    add_boolean_flag,
    CliUsageError,
    format_cli_flag,
    validate_model_payload,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


def _validate_location_input(typed_input: LocationInput | None, *, context: str) -> None:
    if typed_input is None:
        raise CliUsageError(f"--location-kind required with {context}")

    if typed_input.kind == "current":
        invalid_flags: list[str] = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind current"
            )
        return

    if typed_input.kind == "function":
        if typed_input.function is None:
            raise CliUsageError("--function required with --location-kind function")
        invalid_flags = []
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind function"
            )
        return

    if typed_input.kind == "address":
        if typed_input.address is None:
            raise CliUsageError("--address required with --location-kind address")
        invalid_flags = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind address"
            )
        return

    if typed_input.kind == "address_range":
        if typed_input.start_address is None:
            raise CliUsageError("--start-address required with --location-kind address-range")
        if typed_input.end_address is None:
            raise CliUsageError("--end-address required with --location-kind address-range")
        invalid_flags = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.file is not None:
            invalid_flags.append("--file")
        if typed_input.line is not None:
            invalid_flags.append("--line")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind address-range"
            )
        return

    if typed_input.kind == "file_line":
        if typed_input.file is None:
            raise CliUsageError("--file required with --location-kind file-line")
        if typed_input.line is None:
            raise CliUsageError("--line required with --location-kind file-line")
        invalid_flags = []
        if typed_input.function is not None:
            invalid_flags.append("--function")
        if typed_input.address is not None:
            invalid_flags.append("--address")
        if typed_input.start_address is not None:
            invalid_flags.append("--start-address")
        if typed_input.end_address is not None:
            invalid_flags.append("--end-address")
        if typed_input.start_line is not None:
            invalid_flags.append("--start-line")
        if typed_input.end_line is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind file-line"
            )
        return

    if typed_input.file is None:
        raise CliUsageError("--file required with --location-kind file-range")
    if typed_input.start_line is None:
        raise CliUsageError("--start-line required with --location-kind file-range")
    if typed_input.end_line is None:
        raise CliUsageError("--end-line required with --location-kind file-range")
    invalid_flags = []
    if typed_input.function is not None:
        invalid_flags.append("--function")
    if typed_input.address is not None:
        invalid_flags.append("--address")
    if typed_input.start_address is not None:
        invalid_flags.append("--start-address")
    if typed_input.end_address is not None:
        invalid_flags.append("--end-address")
    if typed_input.line is not None:
        invalid_flags.append("--line")
    if invalid_flags:
        raise CliUsageError(
            f"{', '.join(sorted(invalid_flags))} not valid with --location-kind file-range"
        )


def _validate_inspect_query_input(typed_input: InspectQueryInput) -> None:
    invalid_flags: list[str] = []
    location_flags = [format_cli_flag(field_name) for field_name in typed_input.location_fields]
    if typed_input.action == "evaluate":
        if typed_input.expression is None:
            raise CliUsageError("--expression required with --action evaluate")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "variables":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "registers":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "memory":
        if typed_input.memory_address is None:
            raise CliUsageError("--address required with --action memory")
        if typed_input.count is None:
            raise CliUsageError("--count required with --action memory")
        if typed_input.thread_id is not None:
            invalid_flags.append("--thread-id")
        if typed_input.frame is not None:
            invalid_flags.append("--frame")
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        invalid_flags.extend(location_flags)
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
    elif typed_input.action == "disassembly":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        if typed_input.context_before is not None:
            invalid_flags.append("--context-before")
        if typed_input.context_after is not None:
            invalid_flags.append("--context-after")
        _raise_invalid_action_flags(typed_input.action, invalid_flags)
        _validate_location_input(typed_input.location, context="--action disassembly")
        return
    elif typed_input.action == "source":
        if typed_input.expression is not None:
            invalid_flags.append("--expression")
        if typed_input.register_numbers:
            invalid_flags.append("--register-number")
        if typed_input.register_names:
            invalid_flags.append("--register-name")
        if typed_input.include_vector_registers is not None:
            invalid_flags.append("--include-vector-registers")
        if typed_input.max_registers is not None:
            invalid_flags.append("--max-registers")
        if typed_input.value_format is not None:
            invalid_flags.append("--value-format")
        if typed_input.memory_address is not None:
            invalid_flags.append("--address")
        if typed_input.count is not None:
            invalid_flags.append("--count")
        if typed_input.offset is not None:
            invalid_flags.append("--offset")
        if typed_input.instruction_count is not None:
            invalid_flags.append("--instruction-count")
        if typed_input.mode is not None:
            invalid_flags.append("--mode")
        _raise_invalid_action_flags(typed_input.action, invalid_flags)
        _validate_location_input(typed_input.location, context="--action source")
        return

    _raise_invalid_action_flags(typed_input.action, invalid_flags)


def _configure_inspect_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INSPECT_QUERY_ACTIONS)
    parser.add_argument("--thread-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--frame", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--expression", default=argparse.SUPPRESS)
    parser.add_argument(
        "--register-number",
        dest="register_numbers",
        action="append",
        type=int,
        default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--register-name",
        dest="register_names",
        action="append",
        default=argparse.SUPPRESS,
    )
    add_boolean_flag(
        parser,
        "include_vector_registers",
        help_text="Include vector and SIMD registers",
        suppress_default=True,
    )
    parser.add_argument("--max-registers", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--value-format",
        choices=REGISTER_VALUE_FORMATS,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--address", default=argparse.SUPPRESS)
    parser.add_argument("--count", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--offset", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--location-kind",
        choices=CLI_LOCATION_KIND_CHOICES,
        default=argparse.SUPPRESS,
    )
    parser.add_argument("--function", default=argparse.SUPPRESS)
    parser.add_argument("--start-address", default=argparse.SUPPRESS)
    parser.add_argument("--end-address", default=argparse.SUPPRESS)
    parser.add_argument("--file", default=argparse.SUPPRESS)
    parser.add_argument("--line", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--start-line", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--end-line", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--instruction-count", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=DISASSEMBLY_MODES, default=argparse.SUPPRESS)
    parser.add_argument("--context-before", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--context-after", type=int, default=argparse.SUPPRESS)


def _build_inspect_query(typed_input: InspectQueryInput) -> dict[str, object]:
    _validate_inspect_query_input(typed_input)
    payload = build_inspect_query_payload(typed_input)
    return validate_model_payload(InspectQueryArgs, payload)
