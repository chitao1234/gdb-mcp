"""CLI entrypoint for the MCP HTTP client."""

from __future__ import annotations

import argparse
import asyncio
import builtins
from contextlib import redirect_stderr
import json
import sys
from collections.abc import Sequence
from typing import TextIO

import httpx
from pydantic import ValidationError

from .parsers import CliUsageError, format_validation_error
from .runtime import invoke_tool
from .specs import CLIENT_TOOL_SPECS, TOOL_DESCRIPTIONS, TOOL_HELP_DESCRIPTIONS


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


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""

    parser = argparse.ArgumentParser(prog="gdb-mcp-client")
    _add_invocation_flags(parser)

    subparsers = parser.add_subparsers(dest="tool_name", required=True)
    for tool_name, spec in CLIENT_TOOL_SPECS.items():
        subparser = subparsers.add_parser(
            tool_name,
            help=TOOL_HELP_DESCRIPTIONS.get(tool_name, ""),
            description=TOOL_DESCRIPTIONS.get(tool_name, ""),
        )
        _add_invocation_flags(subparser, suppress_defaults=True)
        spec.configure_parser(subparser)

    return parser


def parse_client_args(
    argv: Sequence[str] | None = None,
    *,
    parser: argparse.ArgumentParser | None = None,
) -> argparse.Namespace:
    """Parse top-level CLI arguments."""

    arg_parser = build_parser() if parser is None else parser
    args = arg_parser.parse_args(argv)
    if not args.server_url:
        arg_parser.error("the following arguments are required: --server-url")
    return args


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
    parser = build_parser()
    with redirect_stderr(error_output):
        args = parse_client_args(argv, parser=parser)
    spec = CLIENT_TOOL_SPECS[args.tool_name]
    with redirect_stderr(error_output):
        try:
            payload = spec.build_arguments(args)
        except CliUsageError as exc:
            parser.error(str(exc))
        except ValidationError as exc:
            parser.error(format_validation_error(exc))
    try:
        response = await invoke_tool(
            args.server_url,
            args.tool_name,
            payload,
            http_client=http_client,
        )
    except Exception as exc:
        error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
        return 1
    except BaseException as exc:
        if _is_exception_group(exc):
            error_output.write(f"gdb-mcp-client: error: {_format_runtime_error(exc)}\n")
            return 1
        raise

    if args.json:
        output.write(json.dumps(response.payload, indent=2) + "\n")
    else:
        output.write(spec.render_human(response.payload) + "\n")

    return 1 if response.is_error else 0


def run_client(argv: Sequence[str] | None = None) -> None:
    """Synchronous console-script wrapper."""

    raise SystemExit(asyncio.run(main(argv)))
