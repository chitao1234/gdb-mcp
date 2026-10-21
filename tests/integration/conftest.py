"""Shared fixtures and helpers for integration tests."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest
from mcp.types import TextContent

from gdb_mcp.mcp.runtime import ServerRuntime, create_server_runtime
from gdb_mcp.session.registry import SessionRegistry

from ..support import dig, integer, mapping
from .program_sources import TEST_CPP_PROGRAM

_PROCESS_TERMINATE_TIMEOUT_SEC = 5
_PROCESS_KILL_TIMEOUT_SEC = 10


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


# StartSession and StartSessionResult share one signature and differ only in their
# return annotation. They stay separate so each fixture keeps a precise return type.
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


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a child process, escalating to a kill if it does not exit."""

    if process.poll() is not None:
        return

    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=_PROCESS_TERMINATE_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=_PROCESS_KILL_TIMEOUT_SEC)


def status_query(call: CallGdbTool, session_id: int) -> dict[str, object]:
    """Query one session's status through the direct-v2 envelope."""

    return call(
        "gdb_session_query",
        {"session_id": session_id, "action": "status", "query": {}},
    )


def run(call: CallGdbTool, session_id: int, **execution: object) -> dict[str, object]:
    """Run the target in one session with the given execution options."""

    return call(
        "gdb_execution_manage",
        {"session_id": session_id, "action": "run", "execution": execution},
    )


def create_breakpoint(call: CallGdbTool, session_id: int, location: str) -> dict[str, object]:
    """Create a code breakpoint at one source location."""

    return call(
        "gdb_breakpoint_manage",
        {
            "session_id": session_id,
            "action": "create",
            "breakpoint": {"kind": "code", "location": location},
        },
    )


def compile_default_program(compile_program: CompileProgram, *, filename: str) -> str:
    """Compile the shared C++ program under a caller-chosen filename."""

    return compile_program(TEST_CPP_PROGRAM, filename=filename, compiler="g++")


def _compile(
    source: str,
    filename: str,
    compiler: str,
    compiler_args: list[str] | None = None,
    *,
    directory: Path | None = None,
    prefix: str = "gdb-mcp-tests-",
) -> tuple[Path, Path]:
    """Write one source file into a temporary directory and compile it.

    Returns the directory holding the build artifacts and the executable path.
    """

    tmpdir = Path(tempfile.mkdtemp(prefix=prefix)) if directory is None else directory
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

    return tmpdir, executable_file


@pytest.fixture(scope="module")
def integration_runtime(request: pytest.FixtureRequest) -> Iterator[ServerRuntime]:
    """Create an isolated MCP runtime and session registry for one test module."""

    session_manager = SessionRegistry()
    logger = logging.getLogger(f"test-integration.{request.module.__name__}")
    runtime = create_server_runtime(
        session_manager_provider=lambda: session_manager,
        logger=logger,
    )

    # Session cleanup happens after every test through _cleanup_sessions.
    yield runtime


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
def _tempdirs() -> Iterator[list[Path]]:
    """Track temporary build directories and remove them after the test."""

    temp_dirs: list[Path] = []

    yield temp_dirs

    for tmpdir in temp_dirs:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def compile_program(_tempdirs: list[Path]) -> CompileProgram:
    """Compile one test program and clean up its temporary directory after the test."""

    def build(
        source: str,
        *,
        filename: str,
        compiler: str,
        compiler_args: list[str] | None = None,
    ) -> str:
        tmpdir, executable_file = _compile(source, filename, compiler, compiler_args)
        _tempdirs.append(tmpdir)
        return str(executable_file)

    return build


@pytest.fixture
def compile_program_with_core(_tempdirs: list[Path]) -> CompileProgramWithCore:
    """Compile a program, generate a core dump with GDB, and return both paths.

    The core is produced by running the program under GDB and calling
    ``generate-core-file`` rather than relying on the kernel's ``core_pattern``
    (which on many systems writes outside the working directory or discards
    cores entirely).
    """

    def build(
        source: str,
        *,
        filename: str,
        compiler: str,
        compiler_args: list[str] | None = None,
    ) -> tuple[str, str]:
        tmpdir, executable_file = _compile(
            source,
            filename,
            compiler,
            compiler_args,
            prefix="gdb-mcp-core-tests-",
        )
        _tempdirs.append(tmpdir)

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

    return build


@pytest.fixture
def default_init_commands() -> list[str]:
    """Return the standard init commands used by most integration sessions.

    Only switches every supported GDB understands belong here: ``set
    startup-with-shell`` only exists from GDB 8 on, and an unknown init
    command aborts session start on the older releases this project supports.
    """

    return [
        "set disable-randomization on",
    ]


@pytest.fixture
def start_session_payload(
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
def start_session_result(start_session_payload: StartSessionResult) -> StartSessionResult:
    """Alias kept for integration modules outside the current refactor scope."""

    return start_session_payload


@pytest.fixture
def start_session(start_session_payload: StartSessionResult) -> StartSession:
    """Start a GDB session and return its session ID."""

    def start(
        program: str,
        *,
        init_commands: list[str] | None = None,
        **kwargs: object,
    ) -> int:
        result = start_session_payload(
            program,
            init_commands=init_commands,
            **kwargs,
        )
        return integer(dig(result, "result", "session_id"))

    return start


@pytest.fixture
def session_id(compiled_program: str, start_session: StartSession) -> int:
    """Start one integration session on the module's compiled program."""

    return start_session(compiled_program)


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
