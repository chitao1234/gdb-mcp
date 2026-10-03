"""Human-friendly command surface layered on top of the tool specs.

Each facade command names a tool, optionally fixes an action, and may map a
positional argument onto one of the tool's flags. The flags themselves are
defined once by the tool specs, so the two surfaces cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from gdb_mcp.contracts import (
    ACTION_LIST,
    ACTION_STOP,
    TOOL_ATTACH_PROCESS,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_CALL_FUNCTION,
    TOOL_CAPTURE_BUNDLE,
    TOOL_CONTEXT_QUERY,
    TOOL_EXECUTE_COMMAND,
    TOOL_EXECUTION_MANAGE,
    TOOL_INSPECT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)


@dataclass(frozen=True)
class FacadeCommand:
    """One human command mapped onto a tool call."""

    tool: str
    action: str | None = None
    positionals: tuple[tuple[str, str], ...] = ()
    fixed: Mapping[str, object] = field(default_factory=dict)
    relax: tuple[str, ...] = ()
    help: str = ""


FACADE_GROUPS: dict[str, str] = {
    "break": "Manage breakpoints",
}

FACADE_COMMANDS: dict[str, FacadeCommand] = {
    "start": FacadeCommand(
        TOOL_SESSION_START,
        positionals=(("program", "--program"),),
        help="Start a new debug session",
    ),
    "status": FacadeCommand(TOOL_SESSION_QUERY, action=ACTION_LIST, help="List debug sessions"),
    "stop": FacadeCommand(TOOL_SESSION_MANAGE, action=ACTION_STOP, help="Stop a debug session"),
    "run": FacadeCommand(TOOL_EXECUTION_MANAGE, action="run", help="Run the inferior"),
    "continue": FacadeCommand(
        TOOL_EXECUTION_MANAGE,
        action="continue",
        help="Continue the inferior",
    ),
    "interrupt": FacadeCommand(
        TOOL_EXECUTION_MANAGE,
        action="interrupt",
        help="Interrupt the inferior",
    ),
    "step": FacadeCommand(TOOL_EXECUTION_MANAGE, action="step", help="Step one source line"),
    "next": FacadeCommand(TOOL_EXECUTION_MANAGE, action="next", help="Step over one source line"),
    "finish": FacadeCommand(
        TOOL_EXECUTION_MANAGE,
        action="finish",
        help="Run until the current frame returns",
    ),
    "wait-stop": FacadeCommand(
        TOOL_EXECUTION_MANAGE,
        action="wait_for_stop",
        help="Wait for the next stop event",
    ),
    "attach": FacadeCommand(
        TOOL_ATTACH_PROCESS,
        positionals=(("pid", "--pid"),),
        relax=("pid",),
        help="Attach to a running process",
    ),
    "bt": FacadeCommand(TOOL_CONTEXT_QUERY, action="backtrace", help="Show the backtrace"),
    "threads": FacadeCommand(TOOL_CONTEXT_QUERY, action="threads", help="List threads"),
    "frame": FacadeCommand(
        TOOL_CONTEXT_QUERY,
        action="frame",
        positionals=(("frame", "--frame"),),
        help="Show one frame",
    ),
    "locals": FacadeCommand(TOOL_INSPECT_QUERY, action="variables", help="Show local variables"),
    "regs": FacadeCommand(TOOL_INSPECT_QUERY, action="registers", help="Show registers"),
    "memory": FacadeCommand(
        TOOL_INSPECT_QUERY,
        action="memory",
        positionals=(("address", "--address"),),
        help="Read memory",
    ),
    "disasm": FacadeCommand(TOOL_INSPECT_QUERY, action="disassembly", help="Disassemble code"),
    "list": FacadeCommand(TOOL_INSPECT_QUERY, action="source", help="Show source"),
    "break add": FacadeCommand(
        TOOL_BREAKPOINT_MANAGE,
        action="create",
        positionals=(("location", "--location"),),
        fixed={"breakpoint_kind": "code"},
        help="Add a breakpoint",
    ),
    "break list": FacadeCommand(
        TOOL_BREAKPOINT_QUERY,
        action=ACTION_LIST,
        help="List breakpoints",
    ),
    "break rm": FacadeCommand(
        TOOL_BREAKPOINT_MANAGE,
        action="delete",
        positionals=(("number", "--number"),),
        help="Delete a breakpoint",
    ),
    "break enable": FacadeCommand(
        TOOL_BREAKPOINT_MANAGE,
        action="enable",
        positionals=(("number", "--number"),),
        help="Enable a breakpoint",
    ),
    "break disable": FacadeCommand(
        TOOL_BREAKPOINT_MANAGE,
        action="disable",
        positionals=(("number", "--number"),),
        help="Disable a breakpoint",
    ),
    "watch": FacadeCommand(
        TOOL_BREAKPOINT_MANAGE,
        action="create",
        positionals=(("expression", "--expression"),),
        fixed={"breakpoint_kind": "watch"},
        help="Watch an expression",
    ),
    "catch": FacadeCommand(
        TOOL_BREAKPOINT_MANAGE,
        action="create",
        positionals=(("event", "--event"),),
        fixed={"breakpoint_kind": "catch"},
        help="Catch an event",
    ),
    "call": FacadeCommand(
        TOOL_CALL_FUNCTION,
        positionals=(("function_call", "--function-call"),),
        relax=("function_call",),
        help="Call a function in the inferior",
    ),
    "exec": FacadeCommand(
        TOOL_EXECUTE_COMMAND,
        positionals=(("command", "--command"),),
        relax=("command",),
        help="Run a GDB command",
    ),
    "capture": FacadeCommand(TOOL_CAPTURE_BUNDLE, help="Capture a debugging bundle"),
    "batch": FacadeCommand(TOOL_WORKFLOW_BATCH, help="Run a batch of tool steps"),
    "campaign": FacadeCommand(TOOL_RUN_UNTIL_FAILURE, help="Run until a failure is hit"),
}
