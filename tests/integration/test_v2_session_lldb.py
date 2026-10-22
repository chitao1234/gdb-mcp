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
from gdb_mcp.session.requests import ExecutionOptions

from .conftest import CompileProgram

LLDB_PREFIX = os.environ.get("LLDB_PREFIX")
LLDB_LIBRARY = os.environ.get("LLDB_LIBRARY")

_LLDB_PROGRAM = """
#include <stdio.h>
int add(int left, int right) { int sum = left + right; return sum; }
int main(void) { printf("%d\\n", add(2, 3)); return 0; }
"""


def test_lldb_start_breakpoint_run_and_status(compile_program: CompileProgram) -> None:
    if LLDB_PREFIX is None and LLDB_LIBRARY is None:
        pytest.fail(
            "LLDB_PREFIX or LLDB_LIBRARY must select the LLDB installation; "
            "LLDB integration coverage is not optional"
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
