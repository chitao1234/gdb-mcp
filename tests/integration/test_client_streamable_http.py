"""Integration test for the streamable HTTP CLI client."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Sequence
from io import StringIO
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from gdb_mcp.client.cli import main
from gdb_mcp.mcp.app import create_streamable_http_app
from gdb_mcp.mcp.runtime import ServerRuntime
from tests.integration.program_sources import CRASHING_C_PROGRAM, TEST_CPP_PROGRAM
from tests.support import dig, integer, mapping_list, text

from .conftest import CompileProgram, StartSession

TEST_SERVER_URL = "http://testserver/mcp"

CliClientFactory = Callable[
    [], contextlib.AbstractAsyncContextManager[tuple[httpx.AsyncClient, StringIO]]
]


@contextlib.asynccontextmanager
async def _open_cli_client(
    app: Starlette,
) -> AsyncIterator[tuple[httpx.AsyncClient, StringIO]]:
    """Open the app lifespan together with an ASGI-bound HTTP client and stdout."""

    stdout = StringIO()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as http_client,
    ):
        yield http_client, stdout


@pytest.fixture
def cli_client(integration_runtime: ServerRuntime) -> CliClientFactory:
    """Provide a factory for a CLI HTTP client bound to the real ASGI app."""

    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    return lambda: _open_cli_client(app)


async def _run_cli(
    client_factory: CliClientFactory, argv: Sequence[str]
) -> tuple[int, dict[str, object]]:
    """Run one CLI command against the real app and decode its JSON output."""

    async with client_factory() as (http_client, stdout):
        exit_code = await main(
            ["--server-url", TEST_SERVER_URL, *argv],
            stdout=stdout,
            http_client=http_client,
        )
    return exit_code, json.loads(stdout.getvalue())


def _init_command_args(commands: Sequence[str]) -> list[str]:
    """Expand init commands into repeated ``--startup-init-command`` arguments."""

    args: list[str] = []
    for command in commands:
        args.extend(["--startup-init-command", command])
    return args


@pytest.mark.integration
def test_cli_calls_real_streamable_http_app(cli_client: CliClientFactory) -> None:
    """The CLI can query status through a real streamable HTTP app."""

    exit_code, payload = asyncio.run(_run_cli(cli_client, ["--json", "status"]))

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["action"] == "list"


@pytest.mark.integration
def test_cli_calls_real_streamable_http_workflow_batch(
    cli_client: CliClientFactory,
    compile_program: CompileProgram,
    start_session: StartSession,
) -> None:
    """A batch step reaches the real server and reports its action."""

    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_batch.cpp",
        compiler="g++",
    )
    session_id = start_session(program)

    exit_code, payload = asyncio.run(
        _run_cli(
            cli_client,
            [
                "--json",
                "batch",
                "--session-id",
                str(session_id),
                "--step",
                "gdb_session_query",
                "--step-arg",
                "action=status",
            ],
        )
    )

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["count"] == 1
    steps = mapping_list(payload["steps"])
    assert steps[0]["tool"] == "gdb_session_query"
    assert steps[0]["action"] == "status"


@pytest.mark.integration
def test_cli_calls_real_streamable_http_workflow_batch_with_nested_step_args(
    cli_client: CliClientFactory,
    compile_program: CompileProgram,
    start_session: StartSession,
) -> None:
    """Nested step arguments are parsed and run as a multi-step batch."""

    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_nested_batch.cpp",
        compiler="g++",
    )
    session_id = start_session(program)

    exit_code, payload = asyncio.run(
        _run_cli(
            cli_client,
            [
                "--json",
                "batch",
                "--session-id",
                str(session_id),
                "--step",
                "gdb_breakpoint_manage",
                "--step-label",
                "break-main",
                "--step-arg",
                "action=create",
                "--step-arg",
                "breakpoint.kind=code",
                "--step-arg",
                "breakpoint.location=main",
                "--step",
                "gdb_execution_manage",
                "--step-label",
                "run",
                "--step-arg",
                "action=run",
                "--step-arg",
                "execution.wait_until=stop",
                "--step",
                "gdb_context_query",
                "--step-label",
                "stack",
                "--step-arg",
                "action=backtrace",
                "--step-arg",
                "query.max_frames=1",
            ],
        )
    )

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["count"] == 3
    steps = mapping_list(payload["steps"])
    assert steps[0]["label"] == "break-main"
    assert steps[1]["label"] == "run"
    assert dig(steps[1], "stop_event", "reason") == "breakpoint-hit"
    assert steps[2]["label"] == "stack"
    assert steps[2]["action"] == "backtrace"
    assert integer(dig(steps[2], "result", "result", "count")) >= 1


@pytest.mark.integration
def test_cli_calls_real_streamable_http_workflow_batch_with_single_list_step_arg(
    cli_client: CliClientFactory,
    compile_program: CompileProgram,
    start_session: StartSession,
) -> None:
    """A single list-valued step argument is accepted as one item."""

    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_single_list_batch.cpp",
        compiler="g++",
    )
    session_id = start_session(program)

    exit_code, payload = asyncio.run(
        _run_cli(
            cli_client,
            [
                "--json",
                "batch",
                "--session-id",
                str(session_id),
                "--step",
                "gdb_breakpoint_query",
                "--step-arg",
                "action=list",
                "--step-arg",
                "query.kinds=code",
            ],
        )
    )

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["count"] == 1
    steps = mapping_list(payload["steps"])
    assert steps[0]["tool"] == "gdb_breakpoint_query"
    assert steps[0]["action"] == "list"


@pytest.mark.integration
def test_cli_calls_real_streamable_http_run_until_failure(
    cli_client: CliClientFactory,
    compile_program: CompileProgram,
    default_init_commands: list[str],
    tmp_path: Path,
) -> None:
    """A campaign stops on a signal and writes the requested capture bundle."""

    crashing = compile_program(
        CRASHING_C_PROGRAM,
        filename="cli_crash_signal.c",
        compiler="gcc",
    )

    exit_code, payload = asyncio.run(
        _run_cli(
            cli_client,
            [
                "--json",
                "campaign",
                "--startup-program",
                crashing,
                *_init_command_args(default_init_commands),
                "--max-iterations",
                "1",
                "--failure-stop-reason",
                "signal-received",
                "--capture-output-dir",
                str(tmp_path),
                "--capture-bundle-name",
                "cli-failure",
            ],
        )
    )

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["matched_failure"] is True
    assert dig(payload, "capture_bundle", "bundle_name") == "cli-failure"
    assert Path(text(dig(payload, "capture_bundle", "manifest_path"))).exists()


@pytest.mark.integration
def test_cli_calls_real_streamable_http_run_until_failure_with_setup_steps(
    cli_client: CliClientFactory,
    compile_program: CompileProgram,
    default_init_commands: list[str],
    tmp_path: Path,
) -> None:
    """Setup steps run before the campaign loop and can trigger the failure."""

    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_run_until_failure_setup.cpp",
        compiler="g++",
    )

    exit_code, payload = asyncio.run(
        _run_cli(
            cli_client,
            [
                "--json",
                "campaign",
                "--startup-program",
                program,
                *_init_command_args(default_init_commands),
                "--setup-step",
                "gdb_breakpoint_manage",
                "--setup-step-label",
                "break-main",
                "--setup-step-arg",
                "action=create",
                "--setup-step-arg",
                "breakpoint.kind=code",
                "--setup-step-arg",
                "breakpoint.location=main",
                "--max-iterations",
                "1",
                "--failure-stop-reason",
                "breakpoint-hit",
                "--capture-output-dir",
                str(tmp_path),
                "--capture-bundle-name",
                "cli-break-main",
            ],
        )
    )

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["matched_failure"] is True
    assert dig(payload, "capture_bundle", "bundle_name") == "cli-break-main"
    assert Path(text(dig(payload, "capture_bundle", "manifest_path"))).exists()
