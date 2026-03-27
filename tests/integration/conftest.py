"""Shared fixtures and helpers for integration tests."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from gdb_mcp.mcp.runtime import create_server_runtime
from gdb_mcp.session.registry import SessionRegistry


@pytest.fixture(scope="module")
def integration_runtime(request):
    """Create an isolated MCP runtime and session registry for one test module."""

    session_manager = SessionRegistry()
    logger = logging.getLogger(f"test-integration.{request.module.__name__}")
    runtime = create_server_runtime(
        session_manager_provider=lambda: session_manager,
        logger=logger,
    )

    yield runtime

    runtime.shutdown_sessions()


@pytest.fixture(scope="module")
def call_gdb_tool(integration_runtime):
    """Invoke one MCP tool and deserialize the JSON response payload."""

    def invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = asyncio.run(integration_runtime.call_tool(tool_name, arguments))
        return json.loads(result[0].text)

    return invoke


@pytest.fixture(autouse=True, scope="module")
def _install_module_tool_caller(request, call_gdb_tool):
    """Expose the shared tool caller under the historical module-global name."""

    setattr(request.module, "call_gdb_tool", call_gdb_tool)


@pytest.fixture(autouse=True)
def _cleanup_sessions(integration_runtime):
    """Ensure sessions do not leak between tests within a module."""

    yield

    integration_runtime.shutdown_sessions()


@pytest.fixture
def compile_program():
    """Compile one test program and clean up its temporary directory after the test."""

    temp_dirs: list[Path] = []

    def build(
        source: str,
        *,
        filename: str,
        compiler: str,
        compiler_args: list[str] | None = None,
    ) -> str:
        tmpdir = Path(tempfile.mkdtemp(prefix="gdb-mcp-tests-"))
        temp_dirs.append(tmpdir)

        source_file = tmpdir / filename
        executable_file = tmpdir / Path(filename).stem
        source_file.write_text(source)

        compile_result = subprocess.run(
            [
                compiler,
                *(compiler_args or ["-g", "-O0"]),
                "-o",
                str(executable_file),
                str(source_file),
            ],
            capture_output=True,
            text=True,
        )

        if compile_result.returncode != 0:
            pytest.fail(f"Failed to compile test program: {compile_result.stderr}")

        return str(executable_file)

    yield build

    for tmpdir in temp_dirs:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def compile_program_with_core():
    """Compile a program, run it with core dumps enabled, and return executable/core paths."""

    temp_dirs: list[Path] = []

    def build(
        source: str,
        *,
        filename: str,
        compiler: str,
        compiler_args: list[str] | None = None,
    ) -> tuple[str, str]:
        tmpdir = Path(tempfile.mkdtemp(prefix="gdb-mcp-core-tests-"))
        temp_dirs.append(tmpdir)

        source_file = tmpdir / filename
        executable_file = tmpdir / Path(filename).stem
        source_file.write_text(source)

        compile_result = subprocess.run(
            [
                compiler,
                *(compiler_args or ["-g", "-O0"]),
                "-o",
                str(executable_file),
                str(source_file),
            ],
            capture_output=True,
            text=True,
        )

        if compile_result.returncode != 0:
            pytest.fail(f"Failed to compile crashing test program: {compile_result.stderr}")

        run_result = subprocess.run(
            [
                "bash",
                "-lc",
                'ulimit -c unlimited; cd "$1"; shift; "$@" >/dev/null 2>&1',
                "_",
                str(tmpdir),
                f"./{executable_file.name}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        core_files = sorted(
            tmpdir.glob("core*"), key=lambda path: path.stat().st_mtime, reverse=True
        )
        if not core_files:
            pytest.fail(
                "Expected a core dump but none was produced. "
                f"stdout={run_result.stdout!r} stderr={run_result.stderr!r}"
            )

        return str(executable_file), str(core_files[0])

    yield build

    for tmpdir in temp_dirs:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def default_init_commands():
    """Return the standard init commands used by most integration sessions."""

    return [
        "set disable-randomization on",
        "set startup-with-shell off",
    ]


@pytest.fixture
def start_session(start_session_result):
    """Start a GDB session and return its session ID."""

    def start(
        program: str,
        *,
        init_commands: list[str] | None = None,
        **kwargs: Any,
    ) -> int:
        return start_session_result(
            program,
            init_commands=init_commands,
            **kwargs,
        )["session_id"]

    return start


@pytest.fixture
def start_session_result(call_gdb_tool, default_init_commands):
    """Start a GDB session and return the full start payload."""

    def start(
        program: str,
        *,
        init_commands: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "program": program,
            "init_commands": (
                list(default_init_commands) if init_commands is None else init_commands
            ),
        }
        payload.update(kwargs)

        result = call_gdb_tool("gdb_start_session", payload)
        assert result["status"] == "success", f"Failed to start session: {result}"
        return result

    return start


@pytest.fixture
def stop_session(call_gdb_tool):
    """Stop a GDB session and optionally ignore cleanup failures."""

    def stop(session_id: int, *, ignore_errors: bool = False) -> dict[str, Any]:
        result = call_gdb_tool("gdb_stop_session", {"session_id": session_id})
        if not ignore_errors:
            assert result["status"] == "success", f"Failed to stop session: {result}"
        return result

    return stop
