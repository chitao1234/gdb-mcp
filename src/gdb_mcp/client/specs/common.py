"""Shared CLI spec plumbing (spec dataclasses, flag helpers, descriptions)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, cast


from gdb_mcp.mcp.schemas import (
    build_tool_definitions,
)

from ..parsers import (
    CliUsageError,
)

_ToolInputT = TypeVar("_ToolInputT")


@dataclass(frozen=True)
class ToolCliSpec(Generic[_ToolInputT]):
    """One CLI mapping for a public MCP tool."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    parse_input: Callable[[argparse.Namespace], _ToolInputT]
    build_arguments: Callable[[_ToolInputT], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


@dataclass(frozen=True)
class RegisteredToolCliSpec:
    """Runtime CLI spec with erased parsed-input type."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    parse_input: Callable[[argparse.Namespace], object]
    build_arguments: Callable[[object], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


TOOL_DESCRIPTIONS: dict[str, str] = {}
TOOL_HELP_DESCRIPTIONS: dict[str, str] = {}
for _tool in build_tool_definitions():
    TOOL_DESCRIPTIONS[_tool.name] = _tool.description or ""
    TOOL_HELP_DESCRIPTIONS[_tool.name] = (_tool.description or "").replace("%", "%%")


def _parse_namespace(namespace: argparse.Namespace) -> argparse.Namespace:
    return namespace


def _erase_builder(
    builder: Callable[[_ToolInputT], dict[str, object]],
) -> Callable[[object], dict[str, object]]:
    def build_arguments(parsed_input: object) -> dict[str, object]:
        return builder(cast(_ToolInputT, parsed_input))

    return build_arguments


def _register_tool_spec(spec: ToolCliSpec[_ToolInputT]) -> RegisteredToolCliSpec:
    return RegisteredToolCliSpec(
        name=spec.name,
        configure_parser=spec.configure_parser,
        parse_input=spec.parse_input,
        build_arguments=_erase_builder(spec.build_arguments),
        render_human=spec.render_human,
    )


def _configure_session_id_and_timeout(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=None)
    parser.add_argument("--timeout-sec", type=int, default=30)


def _add_session_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=None)


def _add_action(parser: argparse.ArgumentParser, *, choices: tuple[str, ...]) -> None:
    parser.add_argument("--action", required=True, choices=choices)


def _raise_invalid_action_flags(action: str, invalid_flags: list[str]) -> None:
    if invalid_flags:
        raise CliUsageError(f"{', '.join(sorted(invalid_flags))} not valid with --action {action}")
