"""Real LLDB integration coverage.

The native bridge is built on first use. The caller must select the LLDB
installation with ``LLDB_PREFIX`` or ``LLDB_LIBRARY``; a missing selection is a
test failure, not a skip, so LLDB has the same coverage status as GDB.
"""

from __future__ import annotations

import os
import time
from typing import cast

import pytest

from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.lldb_backend import LldbSbBackend
from gdb_mcp.session.protocols import OsModuleProtocol, TimeModuleProtocol
from gdb_mcp.session.requests import (
    AddressLocation,
    CurrentLocation,
    DisassemblyRequest,
    ExecutionOptions,
    SourceContextRequest,
)

from .conftest import CompileProgram

LLDB_PREFIX = os.environ.get("LLDB_PREFIX")
LLDB_LIBRARY = os.environ.get("LLDB_LIBRARY")
LLDB_BRIDGE = os.environ.get("LLDB_BRIDGE_EXECUTABLE") or os.environ.get("LLDB_BRIDGE_DIR")

_LLDB_PROGRAM = """
#include <stdio.h>
int add(int left, int right) { int sum = left + right; return sum; }
int main(void) { printf("%d\\n", add(2, 3)); return 0; }
"""


def test_lldb_start_breakpoint_run_and_status(compile_program: CompileProgram) -> None:
    if LLDB_PREFIX is None and LLDB_LIBRARY is None and LLDB_BRIDGE is None:
        pytest.fail(
            "LLDB_PREFIX, LLDB_LIBRARY, or LLDB_BRIDGE_EXECUTABLE must select the LLDB "
            "installation; LLDB integration coverage is not optional"
        )
    program = compile_program(_LLDB_PROGRAM, filename="lldb_demo.c", compiler="gcc")
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    try:
        started = backend.start(
            SessionConfig.from_inputs(program=program, debugger="lldb", debugger_path=LLDB_PREFIX)
        )
        assert isinstance(started, OperationSuccess)
        assert started.value.target_loaded is True

        breakpoint = backend.set_breakpoint("add")
        assert isinstance(breakpoint, OperationSuccess)
        assert breakpoint.value.breakpoint.number == "1"

        ran = backend.run(options=ExecutionOptions(timeout_sec=10))
        assert isinstance(ran, OperationSuccess)
        status = backend.get_status()
        assert isinstance(status, OperationSuccess)
        assert status.value.execution_state == "paused"
        assert status.value.stop_reason == "breakpoint"
        assert status.value.stop_kind == "breakpoint"
        assert status.value.inferior_count == 1

        inventory = backend.list_inferiors()
        assert isinstance(inventory, OperationSuccess)
        assert inventory.value.inferiors[0].execution_state == "paused"
        assert backend.runtime.stop_reason == "breakpoint"
        assert backend.runtime.stop_kind == "breakpoint"

        trace = backend.get_backtrace()
        assert isinstance(trace, OperationSuccess)
        caller = trace.value.frames[1]
        for location in (CurrentLocation(), AddressLocation("$pc")):
            disassembly = backend.disassemble(
                DisassemblyRequest(location=location, frame=1, instruction_count=1)
            )
            assert isinstance(disassembly, OperationSuccess)
            assert disassembly.value.instructions[0].address == caller.addr
            assert disassembly.value.instructions[0].is_current
            source = backend.get_source_context(SourceContextRequest(location=location, frame=1))
            assert isinstance(source, OperationSuccess)
            assert source.value.location.line == caller.line
            if isinstance(location, CurrentLocation):
                assert source.value.location.function == "main"

        selected = backend.get_frame_info()
        assert isinstance(selected, OperationSuccess)
        assert selected.value.frame is not None
        assert selected.value.frame.level == 0
        assert isinstance(backend.select_frame(1), OperationSuccess)
        disassembly = backend.disassemble(DisassemblyRequest(frame=0, instruction_count=1))
        assert isinstance(disassembly, OperationSuccess)
        assert disassembly.value.instructions[0].address == trace.value.frames[0].addr
        source = backend.get_source_context(SourceContextRequest(frame=0))
        assert isinstance(source, OperationSuccess)
        assert source.value.location.function == "add"
        selected = backend.get_frame_info()
        assert isinstance(selected, OperationSuccess)
        assert selected.value.frame is not None
        assert selected.value.frame.level == 1

        for disassembly_request, source_request in (
            (DisassemblyRequest(thread_id=99999), SourceContextRequest(thread_id=99999)),
            (DisassemblyRequest(frame=99999), SourceContextRequest(frame=99999)),
        ):
            invalid_disassembly = backend.disassemble(disassembly_request)
            invalid_source = backend.get_source_context(source_request)
            assert isinstance(invalid_disassembly, OperationError)
            assert invalid_disassembly.code == "not_found"
            assert isinstance(invalid_source, OperationError)
            assert invalid_source.code == "not_found"
        assert isinstance(backend.select_frame(0), OperationSuccess)

        evaluated = backend.evaluate_expression("2 + 3")
        assert isinstance(evaluated, OperationSuccess)
        assert evaluated.value.value == "5"

        variables = backend.get_variables()
        assert isinstance(variables, OperationSuccess)
        assert {variable.name for variable in variables.value.variables} >= {"left", "right"}

        finished = backend.finish(options=ExecutionOptions(timeout_sec=10))
        assert isinstance(finished, OperationSuccess)
        assert finished.value.return_value == "5"
    finally:
        backend.stop()


def test_lldb_inventory_keeps_running_target_running(compile_program: CompileProgram) -> None:
    program = compile_program(
        "int main(void) { for (;;) {} }", filename="lldb_running.c", compiler="gcc"
    )
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    try:
        assert isinstance(
            backend.start(
                SessionConfig.from_inputs(
                    program=program, debugger="lldb", debugger_path=LLDB_PREFIX
                )
            ),
            OperationSuccess,
        )
        assert isinstance(
            backend.run(options=ExecutionOptions(wait_for_stop=False)), OperationSuccess
        )
        inventory = backend.list_inferiors()
        assert isinstance(inventory, OperationSuccess)
        assert inventory.value.inferiors[0].execution_state == "running"
        assert backend.runtime.execution_state == "running"
        assert backend.runtime.stop_reason is None
    finally:
        backend.stop()


def test_lldb_code_inspection_uses_another_threads_context(
    compile_program: CompileProgram,
) -> None:
    program = compile_program(
        """
#include <pthread.h>
void *worker(void *arg) { return arg; }
int main(void) {
    pthread_t thread;
    pthread_create(&thread, 0, worker, 0);
    pthread_join(thread, 0);
    return 0;
}
""",
        filename="lldb_threads.c",
        compiler="gcc",
        compiler_args=["-g", "-O0", "-pthread"],
    )
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    try:
        assert isinstance(
            backend.start(
                SessionConfig.from_inputs(
                    program=program, debugger="lldb", debugger_path=LLDB_PREFIX
                )
            ),
            OperationSuccess,
        )
        assert isinstance(backend.set_breakpoint("worker"), OperationSuccess)
        assert isinstance(backend.run(options=ExecutionOptions(timeout_sec=10)), OperationSuccess)
        status = backend.get_status()
        assert isinstance(status, OperationSuccess)
        original_thread = backend.runtime.current_thread_id
        threads = backend.get_threads()
        assert isinstance(threads, OperationSuccess)
        other = next(
            thread for thread in threads.value.threads if thread.id != str(original_thread)
        )
        assert other.id is not None
        thread_id = int(other.id)
        trace = backend.get_backtrace(thread_id=thread_id)
        assert isinstance(trace, OperationSuccess)
        caller = next(frame for frame in trace.value.frames if frame.func == "main")
        assert caller.level is not None

        disassembly = backend.disassemble(
            DisassemblyRequest(thread_id=thread_id, frame=caller.level, instruction_count=1)
        )
        assert isinstance(disassembly, OperationSuccess)
        assert disassembly.value.instructions[0].address == caller.addr
        assert disassembly.value.instructions[0].is_current
        source = backend.get_source_context(
            SourceContextRequest(thread_id=thread_id, frame=caller.level)
        )
        assert isinstance(source, OperationSuccess)
        assert source.value.location.function == "main"
        assert source.value.location.line == caller.line
        assert isinstance(backend.get_status(), OperationSuccess)
        assert backend.runtime.current_thread_id == original_thread
        assert backend.runtime.current_frame == 0
    finally:
        backend.stop()


def test_lldb_instruction_steps_and_return(
    compile_program: CompileProgram,
) -> None:
    program = compile_program(_LLDB_PROGRAM, filename="lldb_instruction.c", compiler="gcc")
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    try:
        started = backend.start(
            SessionConfig.from_inputs(program=program, debugger="lldb", debugger_path=LLDB_PREFIX)
        )
        assert isinstance(started, OperationSuccess)
        backend.set_breakpoint("add")
        ran = backend.run(options=ExecutionOptions(timeout_sec=10))
        assert isinstance(ran, OperationSuccess)

        step_into = backend.step_instruction(
            options=ExecutionOptions(timeout_sec=10, wait_for_stop=True)
        )
        assert isinstance(step_into, OperationSuccess)
        status = backend.get_status()
        assert isinstance(status, OperationSuccess)
        assert status.value.execution_state == "paused"

        step_over = backend.next_instruction(
            options=ExecutionOptions(timeout_sec=10, wait_for_stop=True)
        )
        assert isinstance(step_over, OperationSuccess)

        returned = backend.return_from_frame(value="42")
        assert isinstance(returned, OperationSuccess)
        assert returned.value.backend_data is not None
        assert returned.value.backend_data.data.get("return_value") == "42"

        reverse_step = backend.step_instruction(reverse=True)
        assert isinstance(reverse_step, OperationError)
        assert reverse_step.code == "unsupported_combination"
        reverse_next = backend.next_instruction(reverse=True)
        assert isinstance(reverse_next, OperationError)
        assert reverse_next.code == "unsupported_combination"
    finally:
        backend.stop()
