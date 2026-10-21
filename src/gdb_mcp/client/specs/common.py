"""Shared CLI spec plumbing (spec dataclasses, flag helpers, descriptions)."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from gdb_mcp.mcp.schemas import (
    build_tool_definitions,
)

from ..namespace import NamespaceLike
from ..parsers import (
    CliUsageError,
)

_ToolInputT = TypeVar("_ToolInputT")


@dataclass(frozen=True)
class ToolCliSpec(Generic[_ToolInputT]):
    """One CLI mapping for a public MCP tool."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    parse_input: Callable[[NamespaceLike], _ToolInputT]
    build_arguments: Callable[[_ToolInputT], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


@dataclass(frozen=True)
class RegisteredToolCliSpec:
    """Runtime CLI spec with parse and build composed behind one call."""

    name: str
    configure_parser: Callable[[argparse.ArgumentParser], None]
    build_arguments: Callable[[NamespaceLike], dict[str, object]]
    render_human: Callable[[dict[str, object]], str]


TOOL_DESCRIPTIONS: dict[str, str] = {}
TOOL_HELP_DESCRIPTIONS: dict[str, str] = {}
for _tool in build_tool_definitions():
    TOOL_DESCRIPTIONS[_tool.name] = _tool.description or ""
    TOOL_HELP_DESCRIPTIONS[_tool.name] = (_tool.description or "").replace("%", "%%")


def _parse_namespace(namespace: NamespaceLike) -> NamespaceLike:
    return namespace


def _register_tool_spec(spec: ToolCliSpec[_ToolInputT]) -> RegisteredToolCliSpec:
    def build_arguments(namespace: NamespaceLike) -> dict[str, object]:
        return spec.build_arguments(spec.parse_input(namespace))

    return RegisteredToolCliSpec(
        name=spec.name,
        configure_parser=spec.configure_parser,
        build_arguments=build_arguments,
        render_human=spec.render_human,
    )


def _configure_session_id_and_timeout(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--timeout-sec", type=int, default=30)


def _add_session_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", type=int, default=argparse.SUPPRESS)


def _add_action(parser: argparse.ArgumentParser, *, choices: tuple[str, ...]) -> None:
    parser.add_argument("--action", required=True, choices=choices)


def _raise_invalid_action_flags(action: str, invalid_flags: list[str]) -> None:
    if invalid_flags:
        raise CliUsageError(f"{', '.join(sorted(invalid_flags))} not valid with --action {action}")
