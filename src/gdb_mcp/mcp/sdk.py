"""Typed views of the MCP SDK's unannotated server registration hooks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeAlias

from mcp.server import Server
from mcp.types import CallToolResult, Tool

ListToolsHandler: TypeAlias = Callable[[], Awaitable[list[Tool]]]
ListToolsDecorator: TypeAlias = Callable[[ListToolsHandler], ListToolsHandler]
CallToolHandler: TypeAlias = Callable[[str, object], Awaitable[CallToolResult]]
CallToolDecorator: TypeAlias = Callable[[CallToolHandler], CallToolHandler]


class ListToolsRegistrar(Protocol):
    """Decorator factory returned by ``Server.list_tools``."""

    def __call__(self) -> ListToolsDecorator: ...


class CallToolRegistrar(Protocol):
    """Decorator factory returned by ``Server.call_tool``."""

    def __call__(self, *, validate_input: bool = ...) -> CallToolDecorator: ...


def list_tools_registrar(app: Server) -> ListToolsRegistrar:
    """Return ``Server.list_tools`` with its SDK signature applied."""

    registrar: ListToolsRegistrar = app.list_tools
    return registrar


def call_tool_registrar(app: Server) -> CallToolRegistrar:
    """Return ``Server.call_tool`` with its SDK signature applied."""

    registrar: CallToolRegistrar = app.call_tool
    return registrar
