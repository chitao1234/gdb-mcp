"""CLI entrypoint for the MCP HTTP client."""

from __future__ import annotations

import argparse
import asyncio
import builtins
from contextlib import redirect_stderr
import json
import os
import sys
from collections.abc import Sequence
from typing import TextIO

import httpx
from pydantic import ValidationError

from gdb_mcp.contracts import TOOL_SESSION_START
from gdb_mcp.mcp.schemas import TOOL_MODELS
from gdb_mcp.mcp.tool_examples import TOOL_EXAMPLES

from .daemon import (
    DaemonError,
    ResolvedServer,
    daemon_status,
    remembered_session_id,
    remember_session_id,
    resolve_server,
    stop_daemon,
)
from .facade import FACADE_COMMANDS, FACADE_GROUPS, FacadeCommand
from .parsers import CliUsageError, format_validation_error, validate_model_payload
from .runtime import invoke_tool
from .specs import (
    CLIENT_TOOL_SPECS,
    TOOL_DESCRIPTIONS,
    TOOL_HELP_DESCRIPTIONS,
    RegisteredToolCliSpec,
)

_GLOBAL_FLAGS = {"--server-url", "--json", "--payload-json"}
_SERVER_URL_ENV = "GDB_MCP_SERVER_URL"

_BASE_EXCEPTION_GROUP_TYPE = getattr(builtins, "BaseExceptionGroup", None)


def _is_exception_group(exc: BaseException) -> bool:
    """Return whether the runtime exception is a Python 3.11+ exception group."""

    return _BASE_EXCEPTION_GROUP_TYPE is not None and isinstance(exc, _BASE_EXCEPTION_GROUP_TYPE)


def _format_runtime_error(exc: BaseException) -> str:
    """Render one runtime failure for CLI stderr output."""

    if _is_exception_group(exc):
        exceptions = getattr(exc, "exceptions", ())
        if not exceptions:
            return str(exc) or exc.__class__.__name__
        return _format_runtime_error(exceptions[0])

    message = str(exc).strip()
    return message or exc.__class__.__name__


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
) -> dict[str, object]:
    """Validate one raw JSON payload against the published tool schema."""

    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        parser.error(f"--payload-json is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        parser.error("--payload-json must be a JSON object")
    try:
        return validate_model_payload(TOOL_MODELS[tool_name], parsed)
    except ValidationError as exc:
        parser.error(format_validation_error(exc))


def _payload_reports_step_failures(payload: dict[str, object]) -> bool:
    """Batch envelopes keep status=success while individual steps fail."""

    error_count = payload.get("error_count")
    return isinstance(error_count, int) and error_count > 0


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
    for argument, flag in command.positionals:
        parser.add_argument(
            f"_{argument}",
            nargs="?",
            metavar=argument.upper(),
            help=f"shorthand for {flag}",
        )


def _add_facade_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register every facade command, including grouped ones such as break add."""

    groups: dict[str, argparse._SubParsersAction] = {}
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
    container: argparse._SubParsersAction,
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
    _add_invocation_flags(parser)
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

    return parser


def build_payload_parser() -> argparse.ArgumentParser:
    """Build the parser used for --payload-json invocations.

    Field flags do not participate in this mode, so tool-specific parsers
    (and their required flags) are intentionally left out; the raw payload
    is validated against the published tool schema instead.
    """

    parser = argparse.ArgumentParser(prog="gdb-mcp-client")
    _add_invocation_flags(parser)
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


def parse_client_args(
    argv: Sequence[str] | None = None,
    *,
    parser: argparse.ArgumentParser | None = None,
) -> argparse.Namespace:
    """Parse top-level CLI arguments."""

    arg_parser = build_parser() if parser is None else parser
    args = arg_parser.parse_args(argv)
    if not args.server_url:
        args.server_url = os.environ.get(_SERVER_URL_ENV)
    return args


def _apply_facade_metadata(args: argparse.Namespace, command: FacadeCommand) -> None:
    """Fold a facade command's positionals and implied fields into the namespace."""

    for argument, flag in command.positionals:
        value = getattr(args, f"_{argument}", None)
        if value is None:
            continue
        dest = flag.lstrip("-").replace("-", "_")
        if getattr(args, dest, None) is not None:
            raise CliUsageError(f"pass either {flag} or the positional argument, not both")
        setattr(args, dest, value)
    if command.action is not None:
        args.action = command.action
    for field_name, value in command.fixed.items():
        if getattr(args, field_name, None) is None:
            setattr(args, field_name, value)


def _session_id_error(exc: ValidationError) -> bool:
    """Return whether a validation failure is about a missing session id."""

    missing_types = {"missing", "int_type"}
    return any(
        "session_id" in error.get("loc", ()) and error.get("type") in missing_types
        for error in exc.errors()
    )


def _build_payload(
    spec: RegisteredToolCliSpec,
    args: argparse.Namespace,
    resolved: ResolvedServer,
) -> dict[str, object]:
    """Build one tool payload, filling a remembered session id when needed."""

    try:
        return spec.build_arguments(spec.parse_input(args))
    except ValidationError as exc:
        if not resolved.managed or not _session_id_error(exc):
            raise
        remembered = remembered_session_id()
        if remembered is None:
            raise CliUsageError(
                "--session-id required: no debug session is recorded for this project yet"
            ) from exc
        args.session_id = remembered
        return spec.build_arguments(spec.parse_input(args))


def _session_id_from_payload(payload: dict[str, object]) -> int | None:
    """Return the session id reported by a session-creating response."""

    result = payload.get("result")
    candidates: list[object] = [payload.get("session_id")]
    if isinstance(result, dict):
        candidates.append(result.get("session_id"))
    return next((value for value in candidates if isinstance(value, int)), None)


async def _run_daemon_command(args: argparse.Namespace, output: TextIO) -> int:
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
        if getattr(args, "command", None) == "daemon":
            return await _run_daemon_command(args, output)
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
        if getattr(args, "facade_command", None) is not None:
            try:
                _apply_facade_metadata(args, args.facade_command)
            except CliUsageError as exc:
                parser.error(str(exc))
        if payload_mode:
            payload = _payload_from_json(args.tool_name, args.payload_json, parser)
        else:
            try:
                payload = _build_payload(spec, args, resolved)
            except CliUsageError as exc:
                parser.error(str(exc))
            except ValidationError as exc:
                parser.error(format_validation_error(exc))
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

    if resolved.managed and not response.is_error and args.tool_name == TOOL_SESSION_START:
        session_id = _session_id_from_payload(response.payload)
        if session_id is not None:
            remember_session_id(session_id)

    if args.json:
        output.write(json.dumps(response.payload, indent=2) + "\n")
    else:
        output.write(spec.render_human(response.payload) + "\n")

    if response.is_error:
        return 1
    if _payload_reports_step_failures(response.payload):
        return 1
    return 0


def run_client(argv: Sequence[str] | None = None) -> None:
    """Synchronous console-script wrapper."""

    raise SystemExit(asyncio.run(main(argv)))
