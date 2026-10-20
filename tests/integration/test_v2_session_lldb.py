"""Real LLDB coverage, enabled when an LLDB installation is selected.

The native bridge is built on first use, so this test is skipped unless
``LLDB_PREFIX`` (or another discovery variable) points at an installation.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import cast

import pytest

from gdb_mcp.domain import OperationSuccess
from gdb_mcp.session.lldb_backend import LldbSbBackend
from gdb_mcp.session.protocols import OsModuleProtocol, TimeModuleProtocol

from .conftest import CompileProgram

pytestmark = pytest.mark.skipif(
    not os.environ.get("LLDB_PREFIX") or shutil.which("gcc") is None,
    reason="set LLDB_PREFIX to run real LLDB integration coverage",
)

_LLDB_PROGRAM = """
#include <stdio.h>
int add(int left, int right) { int sum = left + right; return sum; }
int main(void) { printf("%d\\n", add(2, 3)); return 0; }
"""


def test_lldb_start_breakpoint_run_and_status(compile_program: CompileProgram) -> None:
    program = compile_program(_LLDB_PROGRAM, filename="lldb_demo.c", compiler="gcc")
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os), time_module=cast(TimeModuleProtocol, time)
    )
    try:
        started = backend.start(program=program, debugger="lldb")
        assert isinstance(started, OperationSuccess)
        assert started.value.target_loaded is True

        breakpoint = backend.set_breakpoint("add")
        assert isinstance(breakpoint, OperationSuccess)
        assert breakpoint.value.breakpoint.number == "1"

        ran = backend.run(timeout_sec=10)
        assert isinstance(ran, OperationSuccess)
        status = backend.get_status()
        assert isinstance(status, OperationSuccess)
        assert status.value.execution_state == "paused"
        assert status.value.stop_reason == "breakpoint"
        assert status.value.stop_kind == "breakpoint"
        assert status.value.inferior_count == 1

        evaluated = backend.evaluate_expression("2 + 3")
        assert isinstance(evaluated, OperationSuccess)
        assert evaluated.value.value == "5"

        variables = backend.get_variables()
        assert isinstance(variables, OperationSuccess)
        assert {variable.name for variable in variables.value.variables} >= {"left", "right"}
    finally:
        backend.stop()
