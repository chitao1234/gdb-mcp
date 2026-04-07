"""Integration test for the streamable HTTP CLI client."""

from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

import httpx
import pytest

from gdb_mcp.client.cli import main
from gdb_mcp.mcp.app import create_streamable_http_app
from tests.integration.program_sources import CRASHING_C_PROGRAM, TEST_CPP_PROGRAM


@pytest.mark.integration
def test_cli_calls_real_streamable_http_app(integration_runtime):
    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    stdout = StringIO()

    async def exercise() -> tuple[int, dict[str, object]]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                exit_code = await main(
                    [
                        "--server-url",
                        "http://testserver/mcp",
                        "--json",
                        "gdb_session_query",
                        "--action",
                        "list",
                    ],
                    stdout=stdout,
                    http_client=http_client,
                )

        return exit_code, json.loads(stdout.getvalue())

    exit_code, payload = asyncio.run(exercise())

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["action"] == "list"


@pytest.mark.integration
def test_cli_calls_real_streamable_http_workflow_batch(
    integration_runtime,
    compile_program,
    start_session_result,
):
    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_batch.cpp",
        compiler="g++",
    )
    session_id = start_session_result(program)["session_id"]
    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    stdout = StringIO()

    async def exercise() -> tuple[int, dict[str, object]]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                exit_code = await main(
                    [
                        "--server-url",
                        "http://testserver/mcp",
                        "--json",
                        "gdb_workflow_batch",
                        "--session-id",
                        str(session_id),
                        "--step",
                        "gdb_session_query",
                        "--step-arg",
                        "action=status",
                    ],
                    stdout=stdout,
                    http_client=http_client,
                )

        return exit_code, json.loads(stdout.getvalue())

    exit_code, payload = asyncio.run(exercise())

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["steps"][0]["tool"] == "gdb_session_query"
    assert payload["steps"][0]["action"] == "status"


@pytest.mark.integration
def test_cli_calls_real_streamable_http_workflow_batch_with_nested_step_args(
    integration_runtime,
    compile_program,
    start_session_result,
):
    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_nested_batch.cpp",
        compiler="g++",
    )
    session_id = start_session_result(program)["session_id"]
    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    stdout = StringIO()

    async def exercise() -> tuple[int, dict[str, object]]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                exit_code = await main(
                    [
                        "--server-url",
                        "http://testserver/mcp",
                        "--json",
                        "gdb_workflow_batch",
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
                        "execution.wait.until=stop",
                        "--step",
                        "gdb_context_query",
                        "--step-label",
                        "stack",
                        "--step-arg",
                        "action=backtrace",
                        "--step-arg",
                        "query.max_frames=1",
                    ],
                    stdout=stdout,
                    http_client=http_client,
                )

        return exit_code, json.loads(stdout.getvalue())

    exit_code, payload = asyncio.run(exercise())

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["count"] == 3
    assert payload["steps"][0]["label"] == "break-main"
    assert payload["steps"][1]["label"] == "run"
    assert payload["steps"][1]["stop_event"]["reason"] == "breakpoint-hit"
    assert payload["steps"][2]["label"] == "stack"
    assert payload["steps"][2]["action"] == "backtrace"
    assert payload["steps"][2]["result"]["result"]["count"] >= 1


@pytest.mark.integration
def test_cli_calls_real_streamable_http_run_until_failure(
    integration_runtime,
    compile_program,
    tmp_path,
):
    crashing = compile_program(
        CRASHING_C_PROGRAM,
        filename="cli_crash_signal.c",
        compiler="gcc",
    )
    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    stdout = StringIO()

    async def exercise() -> tuple[int, dict[str, object]]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                exit_code = await main(
                    [
                        "--server-url",
                        "http://testserver/mcp",
                        "--json",
                        "gdb_run_until_failure",
                        "--startup-program",
                        crashing,
                        "--startup-init-command",
                        "set disable-randomization on",
                        "--startup-init-command",
                        "set startup-with-shell off",
                        "--max-iterations",
                        "1",
                        "--failure-stop-reason",
                        "signal-received",
                        "--capture-output-dir",
                        str(tmp_path),
                        "--capture-bundle-name",
                        "cli-failure",
                    ],
                    stdout=stdout,
                    http_client=http_client,
                )

        return exit_code, json.loads(stdout.getvalue())

    exit_code, payload = asyncio.run(exercise())

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["matched_failure"] is True
    assert payload["capture_bundle"]["bundle_name"] == "cli-failure"
    assert Path(payload["capture_bundle"]["manifest_path"]).exists()


@pytest.mark.integration
def test_cli_calls_real_streamable_http_run_until_failure_with_setup_steps(
    integration_runtime,
    compile_program,
    tmp_path,
):
    program = compile_program(
        TEST_CPP_PROGRAM,
        filename="cli_run_until_failure_setup.cpp",
        compiler="g++",
    )
    app = create_streamable_http_app(
        integration_runtime.app,
        path="/mcp",
        on_shutdown=integration_runtime.shutdown_sessions,
    )
    stdout = StringIO()

    async def exercise() -> tuple[int, dict[str, object]]:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                exit_code = await main(
                    [
                        "--server-url",
                        "http://testserver/mcp",
                        "--json",
                        "gdb_run_until_failure",
                        "--startup-program",
                        program,
                        "--startup-init-command",
                        "set disable-randomization on",
                        "--startup-init-command",
                        "set startup-with-shell off",
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
                    stdout=stdout,
                    http_client=http_client,
                )

        return exit_code, json.loads(stdout.getvalue())

    exit_code, payload = asyncio.run(exercise())

    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["matched_failure"] is True
    assert payload["capture_bundle"]["bundle_name"] == "cli-break-main"
    assert Path(payload["capture_bundle"]["manifest_path"]).exists()
