"""Minimal compatibility baseline for old GDB, LLDB, and Delve releases.

These tests stay on operations every supported debugger release provides:
start, break, run, status, and evaluate. Feature-specific suites run only on
the full modern profiles.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TypeVar, cast

import pytest

from gdb_mcp.contracts import DEBUGGER_DELVE
from gdb_mcp.domain import OperationError, OperationSuccess
from gdb_mcp.session.config import SessionConfig
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.lldb_backend import LldbSbBackend
from gdb_mcp.session.protocols import OsModuleProtocol, TimeModuleProtocol
from gdb_mcp.session.requests import ExecutionOptions

from .conftest import CompileProgram

pytestmark = pytest.mark.integration

T = TypeVar("T")

_C_PROGRAM = """
#include <stdio.h>
int add(int left, int right) { int sum = left + right; return sum; }
int main(void) { printf("%d\\n", add(2, 3)); return 0; }
"""

_GO_PROGRAM = """package main
import "fmt"
var total int
func sum(left, right int) int {
    total = left + right
    fmt.Printf("total=%d\\n", total)
    return total
}
func main() {
    sum(2, 3)
}
"""


def success(result: OperationSuccess[T] | OperationError) -> T:
    assert isinstance(result, OperationSuccess), result
    return result.value


def test_gdb_baseline(compile_program: CompileProgram) -> None:
    program = compile_program(_C_PROGRAM, filename="gdb_baseline.c", compiler="gcc")
    session = create_default_session_service("gdb")
    try:
        success(session.start(program=program, debugger="gdb"))
        success(session.set_breakpoint("add"))
        success(session.run(options=ExecutionOptions(timeout_sec=10)))
        status = success(session.get_status())
        assert status.stop_kind == "breakpoint"
        evaluated = success(session.evaluate_expression("2 + 3"))
        assert evaluated.value == "5"
    finally:
        session.stop()


def test_lldb_baseline(compile_program: CompileProgram) -> None:
    prefix = os.environ.get("LLDB_PREFIX") or os.environ.get("LLDB_LIBRARY")
    assert prefix, "LLDB_PREFIX or LLDB_LIBRARY must select the LLDB installation"
    program = compile_program(_C_PROGRAM, filename="lldb_baseline.c", compiler="gcc")
    backend = LldbSbBackend(
        os_module=cast(OsModuleProtocol, os),
        time_module=cast(TimeModuleProtocol, time),
    )
    try:
        success(
            backend.start(
                SessionConfig.from_inputs(program=program, debugger="lldb", debugger_path=prefix)
            )
        )
        success(backend.set_breakpoint("add"))
        success(backend.run(options=ExecutionOptions(timeout_sec=10)))
        status = success(backend.get_status())
        assert status.stop_kind == "breakpoint"
        evaluated = success(backend.evaluate_expression("2 + 3"))
        assert evaluated.value == "5"
    finally:
        backend.stop()


def test_delve_baseline(tmp_path: Path) -> None:
    go = shutil.which("go") or os.environ.get("GO_PATH")
    dlv = shutil.which("dlv") or os.environ.get("DLV_PATH")
    assert go, "Go compiler is not available"
    assert dlv, "Delve executable is not available"
    source = tmp_path / "main.go"
    source.write_text(_GO_PROGRAM)
    binary = tmp_path / "program"
    subprocess.run(
        [go, "build", "-gcflags=all=-N -l", "-o", str(binary), str(source)],
        check=True,
        capture_output=True,
    )
    line = _GO_PROGRAM.splitlines().index('    fmt.Printf("total=%d\\n", total)') + 1
    session = create_default_session_service(DEBUGGER_DELVE)
    try:
        success(session.start(program=str(binary), debugger=DEBUGGER_DELVE, debugger_path=dlv))
        success(session.set_breakpoint(f"{source}:{line}"))
        success(session.run(options=ExecutionOptions(timeout_sec=10)))
        status = success(session.get_status())
        assert status.stop_kind == "breakpoint"
        evaluated = success(session.evaluate_expression("total"))
        assert evaluated.value == "5"
    finally:
        session.stop()
