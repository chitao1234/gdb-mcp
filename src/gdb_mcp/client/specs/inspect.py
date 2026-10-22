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

from ..namespace import NamespaceLike, ParsedOptions, one_of, snapshot
from ..parsers import (
    CliUsageError,
    add_boolean_flag,
    format_cli_flag,
    validate_model,
)
from .common import (
    _add_action,
    _add_session_id,
    _raise_invalid_action_flags,
)


def _validate_location_input(kind: str | None, values: ParsedOptions, *, context: str) -> None:
    if kind is None:
        raise CliUsageError(f"--location-kind required with {context}")

    if kind == "current":
        invalid_flags: list[str] = []
        if values.get("function") is not None:
            invalid_flags.append("--function")
        if values.get("address") is not None:
            invalid_flags.append("--address")
        if values.get("start_address") is not None:
            invalid_flags.append("--start-address")
        if values.get("end_address") is not None:
            invalid_flags.append("--end-address")
        if values.get("file") is not None:
            invalid_flags.append("--file")
        if values.get("line") is not None:
            invalid_flags.append("--line")
        if values.get("start_line") is not None:
            invalid_flags.append("--start-line")
        if values.get("end_line") is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind current"
            )
        return

    if kind == "function":
        if values.get("function") is None:
            raise CliUsageError("--function required with --location-kind function")
        invalid_flags = []
        if values.get("address") is not None:
            invalid_flags.append("--address")
        if values.get("start_address") is not None:
            invalid_flags.append("--start-address")
        if values.get("end_address") is not None:
            invalid_flags.append("--end-address")
        if values.get("file") is not None:
            invalid_flags.append("--file")
        if values.get("line") is not None:
            invalid_flags.append("--line")
        if values.get("start_line") is not None:
            invalid_flags.append("--start-line")
        if values.get("end_line") is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind function"
            )
        return

    if kind == "address":
        if values.get("address") is None:
            raise CliUsageError("--address required with --location-kind address")
        invalid_flags = []
        if values.get("function") is not None:
            invalid_flags.append("--function")
        if values.get("start_address") is not None:
            invalid_flags.append("--start-address")
        if values.get("end_address") is not None:
            invalid_flags.append("--end-address")
        if values.get("file") is not None:
            invalid_flags.append("--file")
        if values.get("line") is not None:
            invalid_flags.append("--line")
        if values.get("start_line") is not None:
            invalid_flags.append("--start-line")
        if values.get("end_line") is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind address"
            )
        return

    if kind == "address_range":
        if values.get("start_address") is None:
            raise CliUsageError("--start-address required with --location-kind address-range")
        if values.get("end_address") is None:
            raise CliUsageError("--end-address required with --location-kind address-range")
        invalid_flags = []
        if values.get("function") is not None:
            invalid_flags.append("--function")
        if values.get("address") is not None:
            invalid_flags.append("--address")
        if values.get("file") is not None:
            invalid_flags.append("--file")
        if values.get("line") is not None:
            invalid_flags.append("--line")
        if values.get("start_line") is not None:
            invalid_flags.append("--start-line")
        if values.get("end_line") is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind address-range"
            )
        return

    if kind == "file_line":
        if values.get("file") is None:
            raise CliUsageError("--file required with --location-kind file-line")
        if values.get("line") is None:
            raise CliUsageError("--line required with --location-kind file-line")
        invalid_flags = []
        if values.get("function") is not None:
            invalid_flags.append("--function")
        if values.get("address") is not None:
            invalid_flags.append("--address")
        if values.get("start_address") is not None:
            invalid_flags.append("--start-address")
        if values.get("end_address") is not None:
            invalid_flags.append("--end-address")
        if values.get("start_line") is not None:
            invalid_flags.append("--start-line")
        if values.get("end_line") is not None:
            invalid_flags.append("--end-line")
        if invalid_flags:
            raise CliUsageError(
                f"{', '.join(sorted(invalid_flags))} not valid with --location-kind file-line"
            )
        return

    if values.get("file") is None:
        raise CliUsageError("--file required with --location-kind file-range")
    if values.get("start_line") is None:
        raise CliUsageError("--start-line required with --location-kind file-range")
    if values.get("end_line") is None:
        raise CliUsageError("--end-line required with --location-kind file-range")
    invalid_flags = []
    if values.get("function") is not None:
        invalid_flags.append("--function")
    if values.get("address") is not None:
        invalid_flags.append("--address")
    if values.get("start_address") is not None:
        invalid_flags.append("--start-address")
    if values.get("end_address") is not None:
        invalid_flags.append("--end-address")
    if values.get("line") is not None:
        invalid_flags.append("--line")
    if invalid_flags:
        raise CliUsageError(
            f"{', '.join(sorted(invalid_flags))} not valid with --location-kind file-range"
        )


def _validate_inspect_query_input(action: str, values: ParsedOptions, kind: str | None) -> None:
    invalid_flags: list[str] = []
    location_flags = [
        format_cli_flag(name)
        for name in (
            "location_kind",
            "function",
            "address",
            "start_address",
            "end_address",
            "file",
            "line",
            "start_line",
            "end_line",
        )
        if values.get(name) is not None and not (name == "address" and action == "memory")
    ]
    if action == "evaluate":
        if values.get("expression") is None:
            raise CliUsageError("--expression required with --action evaluate")
        if values.get("register_numbers"):
            invalid_flags.append("--register-number")
        if values.get("register_names"):
            invalid_flags.append("--register-name")
        if values.get("include_vector_registers") is not None:
            invalid_flags.append("--include-vector-registers")
        if values.get("max_registers") is not None:
            invalid_flags.append("--max-registers")
        if values.get("value_format") is not None:
            invalid_flags.append("--value-format")
        if values.get("count") is not None:
            invalid_flags.append("--count")
        if values.get("offset") is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if values.get("instruction_count") is not None:
            invalid_flags.append("--instruction-count")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        if values.get("context_before") is not None:
            invalid_flags.append("--context-before")
        if values.get("context_after") is not None:
            invalid_flags.append("--context-after")
    elif action == "variables":
        if values.get("expression") is not None:
            invalid_flags.append("--expression")
        if values.get("register_numbers"):
            invalid_flags.append("--register-number")
        if values.get("register_names"):
            invalid_flags.append("--register-name")
        if values.get("include_vector_registers") is not None:
            invalid_flags.append("--include-vector-registers")
        if values.get("max_registers") is not None:
            invalid_flags.append("--max-registers")
        if values.get("value_format") is not None:
            invalid_flags.append("--value-format")
        if values.get("count") is not None:
            invalid_flags.append("--count")
        if values.get("offset") is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if values.get("instruction_count") is not None:
            invalid_flags.append("--instruction-count")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        if values.get("context_before") is not None:
            invalid_flags.append("--context-before")
        if values.get("context_after") is not None:
            invalid_flags.append("--context-after")
    elif action == "registers":
        if values.get("expression") is not None:
            invalid_flags.append("--expression")
        if values.get("count") is not None:
            invalid_flags.append("--count")
        if values.get("offset") is not None:
            invalid_flags.append("--offset")
        invalid_flags.extend(location_flags)
        if values.get("instruction_count") is not None:
            invalid_flags.append("--instruction-count")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        if values.get("context_before") is not None:
            invalid_flags.append("--context-before")
        if values.get("context_after") is not None:
            invalid_flags.append("--context-after")
    elif action == "memory":
        if values.get("address") is None:
            raise CliUsageError("--address required with --action memory")
        if values.get("count") is None:
            raise CliUsageError("--count required with --action memory")
        if values.get("thread_id") is not None:
            invalid_flags.append("--thread-id")
        if values.get("frame") is not None:
            invalid_flags.append("--frame")
        if values.get("expression") is not None:
            invalid_flags.append("--expression")
        if values.get("register_numbers"):
            invalid_flags.append("--register-number")
        if values.get("register_names"):
            invalid_flags.append("--register-name")
        if values.get("include_vector_registers") is not None:
            invalid_flags.append("--include-vector-registers")
        if values.get("max_registers") is not None:
            invalid_flags.append("--max-registers")
        if values.get("value_format") is not None:
            invalid_flags.append("--value-format")
        invalid_flags.extend(location_flags)
        if values.get("instruction_count") is not None:
            invalid_flags.append("--instruction-count")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        if values.get("context_before") is not None:
            invalid_flags.append("--context-before")
        if values.get("context_after") is not None:
            invalid_flags.append("--context-after")
    elif action == "disassembly":
        if values.get("expression") is not None:
            invalid_flags.append("--expression")
        if values.get("register_numbers"):
            invalid_flags.append("--register-number")
        if values.get("register_names"):
            invalid_flags.append("--register-name")
        if values.get("include_vector_registers") is not None:
            invalid_flags.append("--include-vector-registers")
        if values.get("max_registers") is not None:
            invalid_flags.append("--max-registers")
        if values.get("value_format") is not None:
            invalid_flags.append("--value-format")
        if values.get("count") is not None:
            invalid_flags.append("--count")
        if values.get("offset") is not None:
            invalid_flags.append("--offset")
        if values.get("context_before") is not None:
            invalid_flags.append("--context-before")
        if values.get("context_after") is not None:
            invalid_flags.append("--context-after")
        _raise_invalid_action_flags(action, invalid_flags)
        _validate_location_input(kind, values, context="--action disassembly")
        return
    elif action == "source":
        if values.get("expression") is not None:
            invalid_flags.append("--expression")
        if values.get("register_numbers"):
            invalid_flags.append("--register-number")
        if values.get("register_names"):
            invalid_flags.append("--register-name")
        if values.get("include_vector_registers") is not None:
            invalid_flags.append("--include-vector-registers")
        if values.get("max_registers") is not None:
            invalid_flags.append("--max-registers")
        if values.get("value_format") is not None:
            invalid_flags.append("--value-format")
        if values.get("count") is not None:
            invalid_flags.append("--count")
        if values.get("offset") is not None:
            invalid_flags.append("--offset")
        if values.get("instruction_count") is not None:
            invalid_flags.append("--instruction-count")
        if values.get("mode") is not None:
            invalid_flags.append("--mode")
        _raise_invalid_action_flags(action, invalid_flags)
        _validate_location_input(kind, values, context="--action source")
        return

    _raise_invalid_action_flags(action, invalid_flags)


def _configure_inspect_query(parser: argparse.ArgumentParser) -> None:
    _add_session_id(parser)
    _add_action(parser, choices=INSPECT_QUERY_ACTIONS)
    parser.add_argument("--thread-id", type=int, default=None)
    parser.add_argument("--frame", type=int, default=None)
    parser.add_argument("--expression", default=None)
    parser.add_argument(
        "--register-number",
        dest="register_numbers",
        action="append",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--register-name",
        dest="register_names",
        action="append",
        default=None,
    )
    add_boolean_flag(
        parser,
        "include_vector_registers",
        help_text="Include vector and SIMD registers",
        optional=True,
    )
    parser.add_argument("--max-registers", type=int, default=None)
    parser.add_argument(
        "--value-format",
        choices=REGISTER_VALUE_FORMATS,
        default=None,
    )
    parser.add_argument("--address", default=None)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--offset", type=int, default=None)
    parser.add_argument(
        "--location-kind",
        choices=CLI_LOCATION_KIND_CHOICES,
        default=None,
    )
    parser.add_argument("--function", default=None)
    parser.add_argument("--start-address", default=None)
    parser.add_argument("--end-address", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--line", type=int, default=None)
    parser.add_argument("--start-line", type=int, default=None)
    parser.add_argument("--end-line", type=int, default=None)
    parser.add_argument("--instruction-count", type=int, default=None)
    parser.add_argument("--mode", choices=DISASSEMBLY_MODES, default=None)
    parser.add_argument("--context-before", type=int, default=None)
    parser.add_argument("--context-after", type=int, default=None)


def _build_inspect_query(namespace: NamespaceLike) -> InspectQueryArgs:
    values = snapshot(namespace)
    action = one_of(values, "action", INSPECT_QUERY_ACTIONS)
    raw_kind = values.get("location_kind")
    kind = raw_kind.replace("-", "_") if raw_kind is not None else None
    if (
        kind is None
        and action in {"source", "disassembly"}
        and all(
            values.get(name) is None
            for name in (
                "function",
                "address",
                "start_address",
                "end_address",
                "file",
                "line",
                "start_line",
                "end_line",
            )
        )
    ):
        kind = "current"
    _validate_inspect_query_input(action, values, kind)
    query: dict[str, object] = {
        name: value
        for name in (
            "expression",
            "include_vector_registers",
            "max_registers",
            "value_format",
            "count",
            "offset",
            "instruction_count",
            "mode",
            "context_before",
            "context_after",
        )
        if (value := values.get(name)) is not None
    }
    for name in ("register_numbers", "register_names"):
        items = values.get(name)
        if items:
            query[name] = items
    context = {
        name: selector
        for name in ("thread_id", "frame")
        if (selector := values.get(name)) is not None
    }
    if context:
        query["context"] = context
    if action == "memory":
        query["address"] = values.get("address")
    elif action in {"source", "disassembly"}:
        location = {
            name: field
            for name in (
                "function",
                "address",
                "start_address",
                "end_address",
                "file",
                "line",
                "start_line",
                "end_line",
            )
            if (field := values.get(name)) is not None
        }
        location["kind"] = kind
        query["location"] = location
    payload: dict[str, object] = {"session_id": values.get("session_id"), "action": action}
    if query:
        payload["query"] = query
    return validate_model(InspectQueryArgs, payload)
