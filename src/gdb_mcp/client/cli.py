"""CLI entrypoint for the MCP HTTP client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr
from typing import Literal, Protocol, TextIO, TypedDict, cast, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from gdb_mcp import __version__
from gdb_mcp.contracts import (
    ACTION_STATUS,
    ACTION_STOP,
    TOOL_ATTACH_PROCESS,
    TOOL_SESSION_CONNECT,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)
from gdb_mcp.mcp.schemas import TOOL_MODELS
from gdb_mcp.mcp.tool_examples import TOOL_EXAMPLES

from .daemon import (
    DaemonError,
    ResolvedServer,
    daemon_status,
    forget_session_id,
    remember_session_id,
    remembered_session_id,
    resolve_server,
    stop_daemon,
)
from .facade import FACADE_COMMANDS, FACADE_GROUPS, FacadeCommand
from .namespace import NamespaceLike
from .parsers import (
    CliUsageError,
    format_validation_error,
    serialize_model,
    validate_model,
)
from .results import ToolEnvelope
from .runtime import invoke_tool
from .specs import (
    CLIENT_TOOL_SPECS,
    TOOL_DESCRIPTIONS,
    TOOL_HELP_DESCRIPTIONS,
    RegisteredToolCliSpec,
)

_GLOBAL_FLAGS = {"--server-url", "--json", "--payload-json"}
_SERVER_URL_ENV = "GDB_MCP_SERVER_URL"


@runtime_checkable
class _ExceptionGroupLike(Protocol):
    """Structural type for Python 3.11+ exception groups."""

    exceptions: tuple[BaseException, ...]


def _is_exception_group(exc: BaseException) -> bool:
    """Return whether the runtime exception carries grouped exceptions."""

    return isinstance(exc, _ExceptionGroupLike)


def _format_runtime_error(exc: BaseException) -> str:
    """Render one runtime failure for CLI stderr output."""

    if isinstance(exc, _ExceptionGroupLike):
        exceptions = exc.exceptions
        if not exceptions:
            return str(exc) or type(exc).__name__
        return _format_runtime_error(exceptions[0])

    message = str(exc).strip()
    return message or type(exc).__name__


def _add_invocation_flags(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool = False,
) -> None:
    """Add shared connection and output flags to a parser."""

    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--server-url", default=default)
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
    )
    parser.add_argument(
        "--payload-json",
        default=default,
        help=(
            "Raw MCP tool payload as JSON. Mutually exclusive with field flags; "
            "validated against the published tool schema."
        ),
    )


def _add_version_flag(parser: argparse.ArgumentParser) -> None:
    """Add the `--version` action using the packaged client version."""

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )


def _add_global_session_id(parser: argparse.ArgumentParser) -> None:
    """Add `--session-id` before the subcommand, mirrored by every tool spec.

    The top-level default is ``None``; tool specs use ``SUPPRESS`` so a value
    placed before the subcommand is not clobbered by the subparser default.
    """

    parser.add_argument("--session-id", type=int, default=None)


def _tool_epilog(tool_name: str) -> str | None:
    """Render validated payload examples for one tool's help page."""

    examples = TOOL_EXAMPLES.get(tool_name)
    if not examples:
        return None
    lines = ["Examples (send one as-is with --payload-json):"]
    lines.extend(json.dumps(example) for example in examples)
    return "\n".join(lines)


def _field_flags_requested(argv: Sequence[str] | None) -> list[str]:
    """Return the non-global flags present on the command line."""

    tokens = sys.argv[1:] if argv is None else list(argv)
    requested: list[str] = []
    for token in tokens:
        name = token.split("=", 1)[0]
        if name.startswith("--") and name not in _GLOBAL_FLAGS:
            requested.append(name)
    return requested


def _payload_from_json(
    tool_name: str,
    raw_payload: str,
    parser: argparse.ArgumentParser,
) -> BaseModel:
    """Validate one raw JSON payload against the published tool schema."""

    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        parser.error(f"--payload-json is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        parser.error("--payload-json must be a JSON object")
    try:
        return validate_model(TOOL_MODELS[tool_name], parsed)
    except ValidationError as exc:
        parser.error(format_validation_error(exc))


def _payload_reports_step_failures(payload: dict[str, object]) -> bool:
    """Return whether any completed workflow step returned an error envelope."""

    batch = cast(WorkflowBatchEnvelope, payload["result"])
    return any(step["result"]["status"] == "error" for step in batch["steps"])


class WorkflowStepEnvelope(TypedDict):
    """The status-bearing part of a nested tool result."""

    status: Literal["success", "error"]


class WorkflowStep(TypedDict):
    """One completed workflow step as published in its result array."""

    result: WorkflowStepEnvelope


class WorkflowBatchEnvelope(TypedDict):
    """The workflow fields the CLI reads from a successful result."""

    steps: list[WorkflowStep]


def _normalize_tool_name(tool_name: str) -> str | None:
    """Return the canonical tool name, accepting the short gdb_-less form."""

    if tool_name in CLIENT_TOOL_SPECS:
        return tool_name
    prefixed = f"gdb_{tool_name}"
    return prefixed if prefixed in CLIENT_TOOL_SPECS else None


def _configure_facade_command(parser: argparse.ArgumentParser, command: FacadeCommand) -> None:
    """Reuse the tool's flags for one facade command and add its shorthands."""

    spec = CLIENT_TOOL_SPECS[command.tool]
    _add_invocation_flags(parser, suppress_defaults=True)
    spec.configure_parser(parser)
    for action in parser._actions:
        if action.dest in {"action", "session_id"} or action.dest in command.relax:
            action.required = False
    action_types = {action.dest: action.type for action in parser._actions}
    for argument, flag in command.positionals:
        parser.add_argument(
            f"_{argument}",
            nargs="?",
            metavar=argument.upper(),
            type=action_types.get(argument) or str,
            help=f"shorthand for {flag}",
        )


def _add_facade_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register every facade command, including grouped ones such as break add."""

    groups: dict[str, argparse._SubParsersAction[argparse.ArgumentParser]] = {}
    for name, command in FACADE_COMMANDS.items():
        words = name.split(" ")
        if len(words) == 1:
            parser = subparsers.add_parser(name, help=command.help)
        else:
            group_name = words[0]
            children = groups.get(group_name)
            if children is None:
                group_parser = subparsers.add_parser(
                    group_name,
                    help=FACADE_GROUPS.get(group_name, ""),
                )
                children = group_parser.add_subparsers(dest="facade_group", required=True)
                groups[group_name] = children
            parser = children.add_parser(words[1], help=command.help)
        parser.set_defaults(tool_name=command.tool, facade_command=command)
        _configure_facade_command(parser, command)


def _add_tool_command(
    container: argparse._SubParsersAction[argparse.ArgumentParser],
    tool_name: str,
    spec: RegisteredToolCliSpec,
) -> None:
    """Register one tool subcommand under `tool`, gdb_ prefix optional."""

    subparser = container.add_parser(
        tool_name,
        aliases=[tool_name.removeprefix("gdb_")],
        help=TOOL_HELP_DESCRIPTIONS.get(tool_name, ""),
        description=TOOL_DESCRIPTIONS.get(tool_name, ""),
        epilog=_tool_epilog(tool_name),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_invocation_flags(subparser, suppress_defaults=True)
    spec.configure_parser(subparser)
    subparser.set_defaults(tool_name=tool_name, facade_command=None)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""

    parser = argparse.ArgumentParser(prog="gdb-mcp-client")
    _add_version_flag(parser)
    _add_invocation_flags(parser)
    _add_global_session_id(parser)
    parser.set_defaults(facade_command=None)

    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_facade_commands(subparsers)

    tool_parser = subparsers.add_parser("tool", help="Call an MCP tool by name")
    tool_subparsers = tool_parser.add_subparsers(
        dest="tool_name", required=True, metavar="tool_name"
    )
    for tool_name, spec in CLIENT_TOOL_SPECS.items():
        _add_tool_command(tool_subparsers, tool_name, spec)

    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Manage the per-project background server",
    )
    daemon_commands = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_commands.add_parser("status", help="Show the background server for this project")
    stop_parser = daemon_commands.add_parser("stop", help="Stop the background server")
    stop_parser.add_argument(
        "--force",
        action="store_true",
        help="Stop even when debug sessions are active",
    )

    session_parser = subparsers.add_parser(
        "session",
        help="Manage the project's default session",
    )
    session_commands = session_parser.add_subparsers(dest="session_command", required=True)
    use_parser = session_commands.add_parser(
        "use",
        help="Make one session the default for this project",
    )
    _add_invocation_flags(use_parser, suppress_defaults=True)
    use_parser.add_argument("session_id", type=int, metavar="session_id")
    session_commands.add_parser("current", help="Print the default session for this project")

    return parser


def build_payload_parser() -> argparse.ArgumentParser:
    """Build the parser used for --payload-json invocations.

    Field flags do not participate in this mode, so tool-specific parsers
    (and their required flags) are intentionally left out; the raw payload
    is validated against the published tool schema instead.
    """

    parser = argparse.ArgumentParser(prog="gdb-mcp-client")
    _add_version_flag(parser)
    _add_invocation_flags(parser)
    _add_global_session_id(parser)
    parser.add_argument("tool_name", metavar="tool_name")
    return parser


def _strip_tool_prefix(argv: Sequence[str] | None) -> Sequence[str] | None:
    """Drop a leading `tool` token so payload invocations accept both forms."""

    tokens = sys.argv[1:] if argv is None else list(argv)
    if tokens and tokens[0] == "tool":
        return tokens[1:]
    return argv


def _select_parser(argv: Sequence[str] | None) -> tuple[argparse.ArgumentParser, bool]:
    """Pick the invocation parser and whether the raw-payload mode is active."""

    tokens = sys.argv[1:] if argv is None else list(argv)
    payload_mode = any(token.split("=", 1)[0] == "--payload-json" for token in tokens)
    return (build_payload_parser(), True) if payload_mode else (build_parser(), False)


class CliNamespace(NamespaceLike, Protocol):
    """Options shared by the facade and tool surfaces."""

    tool_name: str
    command: str | None
    session_command: str | None
    daemon_command: str | None
    session_id: int | None
    server_url: str | None
    json: bool
    payload_json: str | None
    force: bool
    facade_command: FacadeCommand | None
    action: str | None
    breakpoint_kind: str | None
    program: str | None
    endpoint: str | None
    pid: int | None
    frame: int | None
    address: str | None
    location: str | None
    number: int | None
    expression: str | None
    event: str | None
    function_call: str | None
    _program: str | None
    _endpoint: str | None
    _pid: int | None
    _frame: int | None
    _address: str | None
    _location: str | None
    _number: int | None
    _expression: str | None
    _event: str | None
    _function_call: str | None
    _command: str | None


def parse_client_args(
    argv: Sequence[str] | None = None,
    *,
    parser: argparse.ArgumentParser | None = None,
) -> CliNamespace:
    """Parse top-level CLI arguments."""

    arg_parser = build_parser() if parser is None else parser
    args: CliNamespace = arg_parser.parse_args(argv)
    if not args.server_url:
        args.server_url = os.environ.get(_SERVER_URL_ENV)
    return args


def _duplicate_facade_field(flag: str) -> CliUsageError:
    """Build the error raised when a flag and its shorthand are both provided."""

    return CliUsageError(f"pass either {flag} or the positional argument, not both")


def _merge_facade_positional(
    args: CliNamespace,
    argument: str,
    flag: str,
) -> None:
    """Fold one facade positional into its flag field, rejecting duplicates."""

    if argument == "program":
        if args._program is None:
            return
        if args.program is not None:
            raise _duplicate_facade_field(flag)
        args.program = args._program
    elif argument == "endpoint":
        if args._endpoint is None:
            return
        if args.endpoint is not None:
            raise _duplicate_facade_field(flag)
        args.endpoint = args._endpoint
    elif argument == "pid":
        if args._pid is None:
            return
        if args.pid is not None:
            raise _duplicate_facade_field(flag)
        args.pid = args._pid
    elif argument == "frame":
        if args._frame is None:
            return
        if args.frame is not None:
            raise _duplicate_facade_field(flag)
        args.frame = args._frame
    elif argument == "address":
        if args._address is None:
            return
        if args.address is not None:
            raise _duplicate_facade_field(flag)
        args.address = args._address
    elif argument == "location":
        if args._location is None:
            return
        if args.location is not None:
            raise _duplicate_facade_field(flag)
        args.location = args._location
    elif argument == "number":
        if args._number is None:
            return
        if args.number is not None:
            raise _duplicate_facade_field(flag)
        args.number = args._number
    elif argument == "expression":
        if args._expression is None:
            return
        if args.expression is not None:
            raise _duplicate_facade_field(flag)
        args.expression = args._expression
    elif argument == "event":
        if args._event is None:
            return
        if args.event is not None:
            raise _duplicate_facade_field(flag)
        args.event = args._event
    elif argument == "function_call":
        if args._function_call is None:
            return
        if args.function_call is not None:
            raise _duplicate_facade_field(flag)
        args.function_call = args._function_call
    elif argument == "command":
        if args._command is None:
            return
        if args.command is not None:
            raise _duplicate_facade_field(flag)
        args.command = args._command
    else:
        raise CliUsageError(f"unsupported facade positional: {argument}")


def _apply_facade_metadata(args: CliNamespace, command: FacadeCommand) -> None:
    """Fold a facade command's positionals and implied fields into the namespace."""

    for argument, flag in command.positionals:
        _merge_facade_positional(args, argument, flag)
    if command.action is not None and args.action is None:
        args.action = command.action
    for field_name, value in command.fixed.items():
        if field_name == "breakpoint_kind" and args.breakpoint_kind is None:
            args.breakpoint_kind = value
        elif field_name != "breakpoint_kind":
            raise CliUsageError(f"unsupported facade fixed field: {field_name}")


def _session_id_error(exc: ValidationError) -> bool:
    """Return whether a validation failure is about a missing session id."""

    missing_types = {"missing", "int_type"}
    return any(
        "session_id" in error.get("loc", ()) and error.get("type") in missing_types
        for error in exc.errors()
    )


def _build_payload(
    spec: RegisteredToolCliSpec,
    args: CliNamespace,
    resolved: ResolvedServer,
) -> BaseModel:
    """Build one tool payload, filling a remembered session id when needed."""

    try:
        return spec.build_arguments(args)
    except ValidationError as exc:
        if not resolved.managed or not _session_id_error(exc):
            raise
        remembered = remembered_session_id()
        if remembered is None:
            raise CliUsageError(
                "--session-id required: no debug session is recorded for this project yet"
            ) from exc
        args.session_id = remembered
        return spec.build_arguments(args)


def _session_id_from_payload(payload: dict[str, object]) -> int | None:
    """Return the session id reported by a session-creating response."""

    envelope = ToolEnvelope.from_payload(payload)
    return envelope.session_id


async def _ensure_attach_session(
    args: CliNamespace,
    resolved: ResolvedServer,
    http_client: httpx.AsyncClient | None,
) -> None:
    """Give `attach` a session, starting a fresh one when none is recorded.

    Attaching conceptually creates a debug session, so when the caller has not
    supplied ``--session-id`` and the project has no remembered session we
    start an empty GDB session and attach into it.
    """

    if args.session_id is not None:
        return
    if resolved.managed:
        remembered = remembered_session_id()
        if remembered is not None:
            args.session_id = remembered
            return

    response = await invoke_tool(
        resolved.url,
        TOOL_SESSION_START,
        {},
        http_client=http_client,
        auth_token=resolved.token,
    )
    if response.is_error:
        raise CliUsageError(response.envelope().message or "could not start a session to attach to")
    session_id = _session_id_from_payload(response.payload)
    if session_id is None:
        raise CliUsageError("could not start a session to attach to")
    if resolved.managed:
        remember_session_id(session_id)
    args.session_id = session_id


def _default_session_annotation(
    args: CliNamespace,
    payload: dict[str, object],
    resolved: ResolvedServer,
) -> str:
    """Describe the default session on session listings."""

    if not resolved.managed or args.tool_name != TOOL_SESSION_QUERY:
        return ""
    envelope = ToolEnvelope.from_payload(payload)
    if not envelope.is_list_action():
        return ""
    session_id = remembered_session_id()
    if session_id is None:
        return ""
    result = envelope.result
    sessions = result.get("sessions") if result is not None else None
    running = isinstance(sessions, list) and any(
        isinstance(item, dict) and item.get("session_id") == session_id for item in sessions
    )
    suffix = "" if running else " (not running)"
    return f"\ndefault session: {session_id}{suffix}"


async def _run_session_command(
    args: CliNamespace,
    output: TextIO,
    error_output: TextIO,
) -> int:
    """Handle the session meta commands."""

    if args.session_command == "current":
        session_id = remembered_session_id()
        if session_id is None:
            error_output.write("gdb-mcp-client: no default session for this project\n")
            return 1
        output.write(f"{session_id}\n")
        return 0

    session_id = args.session_id
    if session_id is None:
        error_output.write("gdb-mcp-client: error: session_id is required\n")
        return 1
    try:
        resolved = await resolve_server(explicit_url=args.server_url)
    except DaemonError as exc:
        error_output.write(f"gdb-mcp-client: error: {exc}\n")
        return 1
    if not resolved.managed:
        error_output.write(
            "gdb-mcp-client: error: default sessions need the project background server; "
            "pass --session-id when using --server-url\n"
        )
        return 1
    try:
        response = await invoke_tool(
            resolved.url,
            TOOL_SESSION_QUERY,
            {"action": ACTION_STATUS, "session_id": session_id},
            auth_token=resolved.token,
        )
    except Exception as exc:
        error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
        return 1
    except BaseException as exc:
        if _is_exception_group(exc):
            error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
            return 1
        raise
    if response.is_error:
        message = response.envelope().message or "request failed"
        error_output.write(f"gdb-mcp-client: error: {message}\n")
        return 1
    remember_session_id(session_id)
    output.write(f"default session: {session_id}\n")
    return 0


async def _run_daemon_command(args: CliNamespace, output: TextIO) -> int:
    """Handle the daemon meta commands."""

    if args.daemon_command == "status":
        output.write(await daemon_status() + "\n")
        return 0
    result = await stop_daemon(force=bool(args.force))
    output.write(result.message + "\n")
    return 0 if result.stopped else 1


async def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> int:
    """Run one CLI invocation and return its process exit code."""

    output = sys.stdout if stdout is None else stdout
    error_output = sys.stderr if stderr is None else stderr
    parser, payload_mode = _select_parser(argv)
    effective_argv = _strip_tool_prefix(argv) if payload_mode else argv
    with redirect_stderr(error_output):
        if payload_mode:
            conflicting = _field_flags_requested(effective_argv)
            if conflicting:
                parser.error(
                    "--payload-json cannot be combined with field flags: "
                    + ", ".join(sorted(set(conflicting)))
                )
        args = parse_client_args(effective_argv, parser=parser)
        if not payload_mode:
            if args.command == "daemon":
                return await _run_daemon_command(args, output)
            if args.command == "session":
                return await _run_session_command(args, output, error_output)
        canonical = _normalize_tool_name(args.tool_name)
        if canonical is None:
            parser.error(f"unknown tool: {args.tool_name}")
        args.tool_name = canonical
    try:
        resolved = await resolve_server(explicit_url=args.server_url)
    except DaemonError as exc:
        error_output.write(f"gdb-mcp-client: error: {exc}\n")
        return 1
    spec = CLIENT_TOOL_SPECS[args.tool_name]
    with redirect_stderr(error_output):
        if not payload_mode and args.facade_command is not None:
            try:
                _apply_facade_metadata(args, args.facade_command)
            except CliUsageError as exc:
                parser.error(str(exc))
        if not payload_mode and args.tool_name == TOOL_ATTACH_PROCESS:
            try:
                await _ensure_attach_session(args, resolved, http_client)
            except CliUsageError as exc:
                parser.error(str(exc))
            except Exception as exc:
                error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
                return 1
        if payload_mode:
            request = _payload_from_json(args.tool_name, cast(str, args.payload_json), parser)
        else:
            try:
                request = _build_payload(spec, args, resolved)
            except CliUsageError as exc:
                parser.error(str(exc))
            except ValidationError as exc:
                parser.error(format_validation_error(exc))
    payload = serialize_model(request)
    try:
        response = await invoke_tool(
            resolved.url,
            args.tool_name,
            payload,
            http_client=http_client,
            auth_token=resolved.token,
        )
    except Exception as exc:
        error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
        return 1
    except BaseException as exc:
        if _is_exception_group(exc):
            error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
            return 1
        raise

    if (
        resolved.managed
        and not response.is_error
        and args.tool_name in {TOOL_SESSION_START, TOOL_SESSION_CONNECT}
    ):
        session_id = _session_id_from_payload(response.payload)
        if session_id is not None:
            remember_session_id(session_id)
    if (
        resolved.managed
        and not response.is_error
        and args.tool_name == TOOL_SESSION_MANAGE
        and response.envelope().action == ACTION_STOP
        and args.session_id == remembered_session_id()
    ):
        forget_session_id()

    if args.json:
        output.write(json.dumps(response.payload, indent=2) + "\n")
    else:
        rendered = spec.render_human(response.payload)
        output.write(
            rendered + _default_session_annotation(args, response.payload, resolved) + "\n"
        )

    if response.is_error:
        return 1
    if args.tool_name == TOOL_WORKFLOW_BATCH and _payload_reports_step_failures(response.payload):
        return 1
    return 0


def run_client(argv: Sequence[str] | None = None) -> None:
    """Synchronous console-script wrapper."""

    raise SystemExit(asyncio.run(main(argv)))
