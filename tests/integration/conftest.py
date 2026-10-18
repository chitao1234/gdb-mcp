"""Shared fixtures and helpers for integration tests."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest
from mcp.types import TextContent

from gdb_mcp.mcp.runtime import ServerRuntime, create_server_runtime
from gdb_mcp.session.registry import SessionRegistry

from ..support import integer, mapping


class CallGdbTool(Protocol):
    """Invoke one MCP tool and return its deserialized JSON payload."""

    def __call__(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]: ...


class CompileProgram(Protocol):
    """Compile one test program and return its executable path."""

    def __call__(
        self,
        source: str,
        *,
        filename: str,
        compiler: str,
        compiler_args: list[str] | None = None,
    ) -> str: ...


class CompileProgramWithCore(Protocol):
    """Compile a crashing test program and return its executable and core paths."""

    def __call__(
        self,
        source: str,
        *,
        filename: str,
        compiler: str,
        compiler_args: list[str] | None = None,
    ) -> tuple[str, str]: ...


class StartSession(Protocol):
    """Start a GDB session and return its session ID."""

    def __call__(
        self, program: str, *, init_commands: list[str] | None = None, **kwargs: object
    ) -> int: ...


class StartSessionResult(Protocol):
    """Start a GDB session and return the full start payload."""

    def __call__(
        self, program: str, *, init_commands: list[str] | None = None, **kwargs: object
    ) -> dict[str, object]: ...


class StopSession(Protocol):
    """Stop a GDB session and return the manage payload."""

    def __call__(self, session_id: int, *, ignore_errors: bool = False) -> dict[str, object]: ...


@pytest.fixture(scope="module")
def integration_runtime(request: pytest.FixtureRequest) -> Iterator[ServerRuntime]:
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
def call_gdb_tool(integration_runtime: ServerRuntime) -> CallGdbTool:
    """Invoke one MCP tool directly and deserialize the JSON response payload."""

    def invoke(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = asyncio.run(integration_runtime.call_tool(tool_name, arguments))
        content = result.content[0]
        assert isinstance(content, TextContent), content
        return mapping(json.loads(content.text))

    return invoke


@pytest.fixture(autouse=True)
def _disable_debuginfod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop GDB from blocking on network debuginfo downloads during tests.

    Distros often export ``DEBUGINFOD_URLS``; when the server is unreachable,
    ``run``/``gcore`` hang until the MI client's command timeout, which looks
    like a product bug. An empty value disables debuginfod for spawned GDBs.
    """

    monkeypatch.setenv("DEBUGINFOD_URLS", "")


@pytest.fixture(autouse=True)
def _cleanup_sessions(integration_runtime: ServerRuntime) -> Iterator[None]:
    """Ensure sessions do not leak between tests within a module."""

    yield

    integration_runtime.shutdown_sessions()


@pytest.fixture
def compile_program() -> Iterator[CompileProgram]:
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
            check=False,
        )

        if compile_result.returncode != 0:
            pytest.fail(f"Failed to compile test program: {compile_result.stderr}")

        return str(executable_file)

    yield build

    for tmpdir in temp_dirs:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def compile_program_with_core() -> Iterator[CompileProgramWithCore]:
    """Compile a program, generate a core dump with GDB, and return both paths.

    The core is produced by running the program under GDB and calling
    ``generate-core-file`` rather than relying on the kernel's ``core_pattern``
    (which on many systems writes outside the working directory or discards
    cores entirely).
    """

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
            check=False,
        )

        if compile_result.returncode != 0:
            pytest.fail(f"Failed to compile crashing test program: {compile_result.stderr}")

        core_file = tmpdir / "core"
        run_result = subprocess.run(
            [
                "gdb",
                "--quiet",
                "--batch",
                "-ex",
                "set pagination off",
                "-ex",
                "set debuginfod enabled off",
                "-ex",
                "run",
                "-ex",
                f"generate-core-file {core_file}",
                "--args",
                str(executable_file),
            ],
            capture_output=True,
            text=True,
            cwd=str(tmpdir),
            timeout=60,
            check=False,
        )

        if not core_file.exists():
            pytest.fail(
                "Expected GDB to generate a core dump but none was produced. "
                f"stdout={run_result.stdout!r} stderr={run_result.stderr!r}"
            )

        return str(executable_file), str(core_file)

    yield build

    for tmpdir in temp_dirs:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def default_init_commands() -> list[str]:
    """Return the standard init commands used by most integration sessions."""

    return [
        "set disable-randomization on",
        "set startup-with-shell off",
    ]


@pytest.fixture
def start_session(start_session_result: StartSessionResult) -> StartSession:
    """Start a GDB session and return its session ID."""

    def start(
        program: str,
        *,
        init_commands: list[str] | None = None,
        **kwargs: object,
    ) -> int:
        return integer(
            start_session_result(
                program,
                init_commands=init_commands,
                **kwargs,
            )["session_id"]
        )

    return start


@pytest.fixture
def start_session_result(
    call_gdb_tool: CallGdbTool, default_init_commands: list[str]
) -> StartSessionResult:
    """Start a GDB session and return the full start payload."""

    def start(
        program: str,
        *,
        init_commands: list[str] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "program": program,
            "init_commands": (
                list(default_init_commands) if init_commands is None else init_commands
            ),
        }
        payload.update(kwargs)

        result = call_gdb_tool("gdb_session_start", payload)
        assert result["status"] == "success", f"Failed to start session: {result}"
        return result

    return start


@pytest.fixture
def stop_session(call_gdb_tool: CallGdbTool) -> StopSession:
    """Stop a GDB session and optionally ignore cleanup failures."""

    def stop(session_id: int, *, ignore_errors: bool = False) -> dict[str, object]:
        result = call_gdb_tool(
            "gdb_session_manage",
            {"session_id": session_id, "action": "stop", "session": {}},
        )
        if not ignore_errors:
            assert result["status"] == "success", f"Failed to stop session: {result}"
        return result

    return stop
