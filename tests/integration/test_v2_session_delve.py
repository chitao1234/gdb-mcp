"""Exercise the same Go workflow with each selected Delve executable."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

import pytest

from gdb_mcp import contracts as c
from gdb_mcp.contracts import DEBUGGER_DELVE
from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.mcp.handlers import dispatch_tool_call
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.registry import SessionRegistry
from gdb_mcp.session.requests import DisassemblyRequest, ExecutionOptions, SourceContextRequest
from tests.support import dig, mapping_list

T = TypeVar("T")
pytestmark = pytest.mark.integration

PROGRAM = """package main
import ("fmt"; "os"; "time")
var buffer = [8]byte{1,2,3,4,5,6,7,8}
//go:noinline
func sum(left, right int) int {
    total := left + right
    fmt.Printf("total=%d\\n", total)
    return total
}
func main() {
    fmt.Println(buffer)
    sum(2, 3)
    if len(os.Args) > 1 {
        for { time.Sleep(10 * time.Millisecond) }
    }
}
"""


def success(result: OperationSuccess[T] | OperationError) -> T:
    assert isinstance(result, OperationSuccess), result
    return result.value


def _binaries() -> list[str]:
    configured = os.environ.get("DELVE_TEST_BINARIES") or os.environ.get("DLV_PATH")
    return configured.split(os.pathsep) if configured else [shutil.which("dlv") or "dlv"]


@pytest.fixture(params=_binaries(), ids=lambda path: str(path))
def delve_binary(request: pytest.FixtureRequest) -> str:
    binary = str(request.param)
    found = shutil.which(binary)
    if found is None:
        pytest.skip(f"Delve executable not available: {binary}")
    return found


@pytest.fixture
def go_program(tmp_path: Path) -> Iterator[tuple[str, str, int]]:
    compiler = os.environ.get("GO_PATH") or shutil.which("go")
    if compiler is None:
        pytest.skip("Go compiler is not available")
    source = tmp_path / "main.go"
    source.write_text(PROGRAM)
    binary = tmp_path / "program"
    subprocess.run(
        [compiler, "build", "-gcflags=all=-N -l", "-o", str(binary), str(source)],
        check=True,
        capture_output=True,
    )
    yield str(binary), str(source), PROGRAM.splitlines().index(
        '    fmt.Printf("total=%d\\n", total)'
    ) + 1


def test_delve_go_workflow(
    delve_binary: str, go_program: tuple[str, str, int], tmp_path: Path
) -> None:
    program, source, line = go_program
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        started = success(
            session.start(program=program, debugger=DEBUGGER_DELVE, debugger_path=delve_binary)
        )
        assert started.execution_state == "paused"
        assert dig(started.diagnostics.backend_data.data, "version", "APIVersion") == 2
        breakpoint = success(session.set_breakpoint(f"{source}:{line}", condition="left == 2"))
        number = int(breakpoint.breakpoint.number or "0")
        assert number > 0
        success(session.disable_breakpoint(number))
        assert success(session.get_breakpoint(number)).breakpoint.enabled is False
        success(session.enable_breakpoint(number))
        success(session.run(options=ExecutionOptions(timeout_sec=10)))
        assert session.execution_state == "paused"
        assert session.runtime.stop_kind == "breakpoint"
        assert success(session.evaluate_expression("total")).value == "5"
        threads = success(session.get_threads())
        assert threads.current_thread_id is not None
        assert any(thread.id == threads.current_thread_id for thread in threads.threads)
        success(session.select_thread(int(threads.current_thread_id)))
        frames = success(session.get_backtrace(max_frames=5)).frames
        assert frames[0].func == "main.sum"
        success(session.select_frame(1))
        session.get_status()
        assert session.runtime.current_frame == 1
        success(session.select_frame(0))
        assert {variable.name for variable in success(session.get_variables()).variables} >= {
            "left",
            "right",
            "total",
        }
        registers = success(session.get_registers(max_registers=5)).registers
        assert registers
        memory = success(session.read_memory("&main.buffer", 8))
        assert memory.blocks[0].contents == "0102030405060708"
        assert success(session.disassemble(DisassemblyRequest(instruction_count=5))).instructions
        context = success(
            session.get_source_context(SourceContextRequest(context_before=1, context_after=1))
        )
        assert any(value.line_number == line for value in context.lines)
        capture = success(
            session.capture_bundle(
                output_dir=str(tmp_path), bundle_name="capture", include_backtraces=False
            )
        )
        assert not capture.failed_sections
        success(session.delete_breakpoint(number))
        success(session.step_instruction(options=ExecutionOptions(timeout_sec=10)))
        success(session.next_instruction(options=ExecutionOptions(timeout_sec=10)))
        success(session.next(options=ExecutionOptions(timeout_sec=10)))
        finished = success(session.finish(options=ExecutionOptions(timeout_sec=10)))
        assert finished.return_value == "5"
        success(session.continue_execution(options=ExecutionOptions(timeout_sec=10)))
        assert session.get_status().value.execution_state == "exited"
        assert session.runtime.exit_code == 0
        success(session.run(options=ExecutionOptions(timeout_sec=10)))
        assert session.get_status().value.execution_state == "exited"
    finally:
        success(session.stop())


def test_delve_timeout_then_interrupt(delve_binary: str, go_program: tuple[str, str, int]) -> None:
    program, _, _ = go_program
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        success(
            session.start(
                program=program, args=["loop"], debugger=DEBUGGER_DELVE, debugger_path=delve_binary
            )
        )
        result = session.run(options=ExecutionOptions(timeout_sec=1))
        assert isinstance(result, OperationError) and result.code == "command_timeout"
        assert session.get_status().value.execution_state == "running"
        success(session.interrupt())
        stopped = success(session.wait_for_stop(timeout_sec=5))
        assert stopped.matched
        assert stopped.execution_state == "paused"
        assert len(session.stop_history) == 2
    finally:
        success(session.stop())


def test_delve_defers_start_until_attach_and_preserves_target(
    delve_binary: str, go_program: tuple[str, str, int]
) -> None:
    program, _, _ = go_program
    process = subprocess.Popen(
        [program, "loop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        time.sleep(0.05)
        started = success(session.start(debugger=DEBUGGER_DELVE, debugger_path=delve_binary))
        assert not session.has_controller
        assert started.execution_state == "not_started"
        assert not session.get_status().value.debugger_alive
        success(session.attach_process(process.pid, timeout_sec=10))
        assert session.runtime.attached_pid == process.pid
        assert success(session.get_threads()).threads
        success(session.stop())
        assert process.poll() is None
    finally:
        session.stop()
        process.terminate()
        process.wait(timeout=5)


def test_delve_temporary_breakpoint_and_watchpoint(
    delve_binary: str, go_program: tuple[str, str, int]
) -> None:
    program, source, line = go_program
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        success(session.start(program=program, debugger=DEBUGGER_DELVE, debugger_path=delve_binary))
        created = success(session.set_breakpoint(f"{source}:{line}", temporary=True))
        success(session.run(options=ExecutionOptions(timeout_sec=10)))
        assert all(
            record.number != created.breakpoint.number
            for record in success(session.list_breakpoints()).breakpoints
        )
        watch = success(session.set_watchpoint("main.buffer[0]"))
        assert watch.breakpoint.watch is not None
        success(session.delete_watchpoint(int(watch.breakpoint.number or "0")))
        success(session.call_function("sum(7, 8)", timeout_sec=10))
    finally:
        success(session.stop())


@pytest.mark.parametrize("endpoint_kind", ["tcp", "unix"])
def test_external_delve_disconnect_preserves_server(
    delve_binary: str, go_program: tuple[str, str, int], tmp_path: Path, endpoint_kind: str
) -> None:
    program, _, _ = go_program
    if endpoint_kind == "unix" and os.name == "nt":
        pytest.skip("Unix sockets require a Unix host")
    if endpoint_kind == "unix":
        help_text = subprocess.run(
            [delve_binary, "exec", "--help"], check=True, capture_output=True, text=True
        ).stdout
        if "unix:" not in help_text:
            pytest.skip("This Delve release has no Unix listener support")
    address = "127.0.0.1:0" if endpoint_kind == "tcp" else "unix:" + str(tmp_path / "dlv.sock")
    server = subprocess.Popen(
        [
            delve_binary,
            "--headless",
            "--api-version=2",
            "--accept-multiclient",
            f"--listen={address}",
            "exec",
            program,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        assert server.stdout is not None
        announcement = server.stdout.readline().decode()
        match = re.search(r"API server listening at: (\S+)", announcement)
        assert match is not None, announcement
        endpoint = (
            f"tcp://{match.group(1)}"
            if endpoint_kind == "tcp"
            else "unix://" + str(tmp_path / "dlv.sock")
        )
        success(session.start(debugger=DEBUGGER_DELVE, endpoint=endpoint))
        success(session.set_breakpoint("main.sum"))
        success(session.continue_execution(options=ExecutionOptions(timeout_sec=10)))
        assert success(session.get_backtrace()).frames
        success(session.stop())
        assert server.poll() is None
        success(session.start(debugger=DEBUGGER_DELVE, endpoint=endpoint))
        assert success(session.get_threads()).threads
    finally:
        session.stop()
        server.terminate()
        server.wait(timeout=5)


def test_mcp_interrupt_during_blocking_run(
    delve_binary: str, go_program: tuple[str, str, int]
) -> None:
    program, _, _ = go_program
    registry = SessionRegistry()
    session_id = success(
        registry.start_session(
            program=program, debugger=DEBUGGER_DELVE, args=["loop"], debugger_path=delve_binary
        )
    ).session_id
    session = registry.get_session(session_id)
    assert session is not None

    async def check() -> None:
        execution = asyncio.create_task(
            dispatch_tool_call(
                c.TOOL_EXECUTION_MANAGE,
                {
                    "session_id": session_id,
                    "action": c.ACTION_RUN,
                    "execution": {"timeout_sec": 10},
                },
                registry,
                logger=logging.getLogger(__name__),
            )
        )
        try:
            for _ in range(200):
                if session.execution_state == "running":
                    break
                await asyncio.sleep(0.01)
            assert session.execution_state == "running"
            interrupted = await asyncio.wait_for(
                dispatch_tool_call(
                    c.TOOL_EXECUTION_MANAGE,
                    {"session_id": session_id, "action": c.ACTION_INTERRUPT},
                    registry,
                    logger=logging.getLogger(__name__),
                ),
                timeout=3,
            )
            assert not interrupted.isError
            assert not (await asyncio.wait_for(execution, timeout=3)).isError
        finally:
            await execution

    try:
        asyncio.run(check())
    finally:
        success(registry.close_session(session_id))


def test_delve_campaign_uses_selected_backend(
    delve_binary: str, go_program: tuple[str, str, int]
) -> None:
    program, _, _ = go_program
    registry = SessionRegistry()
    result = asyncio.run(
        dispatch_tool_call(
            c.TOOL_RUN_UNTIL_FAILURE,
            {
                "startup": {
                    "debugger": DEBUGGER_DELVE,
                    "program": program,
                    "debugger_path": delve_binary,
                },
                "failure": {"exit_codes": [0]},
                "capture": {"enabled": False},
            },
            registry,
            logger=logging.getLogger(__name__),
        )
    )
    assert not result.isError, result
    assert result.structuredContent is not None
    iteration = mapping_list(dig(result.structuredContent, "result", "iterations"))[0]
    assert iteration["phase"] == "run"
    assert dig(iteration, "result", "result", "backend_data", "debugger") == DEBUGGER_DELVE
    assert registry.active_session_count() == 0


def test_delve_jump_depends_on_selected_release(
    delve_binary: str, go_program: tuple[str, str, int]
) -> None:
    program, source, line = go_program
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        started = success(
            session.start(program=program, debugger=DEBUGGER_DELVE, debugger_path=delve_binary)
        )
        success(session.set_breakpoint(f"{source}:{line}"))
        success(session.run(options=ExecutionOptions(timeout_sec=10)))
        result = session.jump(f"{source}:{line + 1}", options=ExecutionOptions(timeout_sec=10))
        version = str(dig(started.diagnostics.backend_data.data, "version", "DelveVersion"))
        if re.search(r"\b1\.(20|24)\.", version):
            assert isinstance(result, OperationError) and result.code == "unsupported_operation"
        else:
            success(result)
            assert session.get_status().value.execution_state == "exited"
    finally:
        success(session.stop())
