"""Shared fixtures and helpers for integration tests."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import pytest

from gdb_mcp.mcp.runtime import create_server_runtime
from gdb_mcp.session.registry import SessionRegistry


def _compact_mapping(values: dict[str, object]) -> dict[str, object]:
    """Drop `None` values from a JSON-compatible mapping."""

    return {key: value for key, value in values.items() if value is not None}


def _flatten_action_result(payload: dict[str, object]) -> dict[str, object]:
    """Flatten action-wrapped success payloads to the legacy top-level shape."""

    result = payload.get("result")
    if payload.get("status") != "success" or not isinstance(result, dict):
        return payload

    flattened = dict(result)
    flattened["status"] = "success"
    warnings = payload.get("warnings")
    if warnings is not None:
        flattened["warnings"] = warnings
    return flattened


def _translate_location(arguments: dict[str, object]) -> dict[str, object]:
    """Translate legacy direct-location selectors to the v2 location union."""

    if "function" in arguments:
        return {"kind": "function", "function": arguments["function"]}
    if "address" in arguments and "start_address" not in arguments:
        return {"kind": "address", "address": arguments["address"]}
    if "start_address" in arguments and "end_address" in arguments:
        return {
            "kind": "address_range",
            "start_address": arguments["start_address"],
            "end_address": arguments["end_address"],
        }
    if "file" in arguments and "line" in arguments:
        return {"kind": "file_line", "file": arguments["file"], "line": arguments["line"]}
    if "file" in arguments and "start_line" in arguments and "end_line" in arguments:
        return {
            "kind": "file_range",
            "file": arguments["file"],
            "start_line": arguments["start_line"],
            "end_line": arguments["end_line"],
        }
    return {"kind": "current"}


def _translate_context(arguments: dict[str, object]) -> dict[str, object] | None:
    """Translate optional thread/frame overrides to the v2 context object."""

    context = _compact_mapping(
        {
            "thread_id": arguments.get("thread_id"),
            "frame": arguments.get("frame"),
        }
    )
    return context or None


def _translate_batch_steps(session_id: int, steps: object) -> object:
    """Translate legacy workflow step payloads to the v2 inventory."""

    if not isinstance(steps, list):
        return steps

    translated_steps: list[object] = []
    for step in steps:
        if isinstance(step, str):
            tool_name, arguments, _ = _translate_legacy_call(step, {"session_id": session_id})
            arguments.pop("session_id", None)
            translated_steps.append(tool_name if not arguments else {"tool": tool_name, "arguments": arguments})
            continue

        if not isinstance(step, dict):
            translated_steps.append(step)
            continue

        tool_name = step.get("tool")
        arguments = step.get("arguments", {})
        if isinstance(tool_name, str) and isinstance(arguments, dict):
            legacy_arguments = dict(arguments)
            legacy_arguments.setdefault("session_id", session_id)
            translated_tool, translated_arguments, _ = _translate_legacy_call(
                tool_name,
                legacy_arguments,
            )
            translated_arguments.pop("session_id", None)
            translated_steps.append(
                _compact_mapping(
                    {
                        "tool": translated_tool,
                        "label": step.get("label"),
                        "arguments": translated_arguments,
                    }
                )
            )
        else:
            translated_steps.append(step)
    return translated_steps


def _translate_legacy_call(tool_name: str, arguments: dict[str, object]) -> tuple[str, dict[str, object], bool]:
    """Map legacy integration call shapes onto the v2 MCP surface."""

    args = dict(arguments)

    if tool_name == "gdb_start_session":
        return "gdb_session_start", args, False
    if tool_name == "gdb_stop_session":
        return (
            "gdb_session_manage",
            {"session_id": args["session_id"], "action": "stop", "session": {}},
            True,
        )
    if tool_name == "gdb_list_sessions":
        return "gdb_session_query", {"action": "list", "query": {}}, True
    if tool_name == "gdb_get_status":
        return (
            "gdb_session_query",
            {"session_id": args["session_id"], "action": "status", "query": {}},
            True,
        )
    if tool_name == "gdb_add_inferior":
        return (
            "gdb_inferior_manage",
            {
                "session_id": args["session_id"],
                "action": "create",
                "inferior": _compact_mapping(
                    {
                        "executable": args.get("executable"),
                        "make_current": args.get("make_current", False),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_remove_inferior":
        return (
            "gdb_inferior_manage",
            {
                "session_id": args["session_id"],
                "action": "remove",
                "inferior": {"inferior_id": args["inferior_id"]},
            },
            True,
        )
    if tool_name == "gdb_list_inferiors":
        return (
            "gdb_inferior_query",
            {"session_id": args["session_id"], "action": "list", "query": {}},
            True,
        )
    if tool_name == "gdb_select_inferior":
        return (
            "gdb_inferior_manage",
            {
                "session_id": args["session_id"],
                "action": "select",
                "inferior": {"inferior_id": args["inferior_id"]},
            },
            True,
        )
    if tool_name == "gdb_set_follow_fork_mode":
        return (
            "gdb_inferior_manage",
            {
                "session_id": args["session_id"],
                "action": "set_follow_fork_mode",
                "inferior": {"mode": args["mode"]},
            },
            True,
        )
    if tool_name == "gdb_set_detach_on_fork":
        return (
            "gdb_inferior_manage",
            {
                "session_id": args["session_id"],
                "action": "set_detach_on_fork",
                "inferior": {"enabled": args["enabled"]},
            },
            True,
        )
    if tool_name == "gdb_run":
        execution = _compact_mapping({"args": args.get("args")})
        wait = _compact_mapping(
            {
                "until": "acknowledged" if args.get("wait_for_stop") is False else None,
                "timeout_sec": args.get("timeout_sec"),
            }
        )
        if wait:
            execution["wait"] = wait
        return (
            "gdb_execution_manage",
            {"session_id": args["session_id"], "action": "run", "execution": execution},
            True,
        )
    if tool_name in {"gdb_continue", "gdb_step", "gdb_next", "gdb_finish"}:
        action = tool_name.removeprefix("gdb_")
        execution = {}
        timeout_sec = args.get("timeout_sec")
        if timeout_sec is not None:
            execution["wait"] = {"timeout_sec": timeout_sec}
        return (
            "gdb_execution_manage",
            {"session_id": args["session_id"], "action": action, "execution": execution},
            True,
        )
    if tool_name == "gdb_interrupt":
        return (
            "gdb_execution_manage",
            {"session_id": args["session_id"], "action": "interrupt", "execution": {}},
            True,
        )
    if tool_name == "gdb_wait_for_stop":
        return (
            "gdb_execution_manage",
            {
                "session_id": args["session_id"],
                "action": "wait_for_stop",
                "execution": _compact_mapping(
                    {
                        "timeout_sec": args.get("timeout_sec"),
                        "stop_reasons": args.get("stop_reasons"),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_get_threads":
        return (
            "gdb_context_query",
            {"session_id": args["session_id"], "action": "threads", "query": {}},
            True,
        )
    if tool_name == "gdb_select_thread":
        return (
            "gdb_context_manage",
            {
                "session_id": args["session_id"],
                "action": "select_thread",
                "context": {"thread_id": args["thread_id"]},
            },
            True,
        )
    if tool_name == "gdb_get_backtrace":
        return (
            "gdb_context_query",
            {
                "session_id": args["session_id"],
                "action": "backtrace",
                "query": _compact_mapping(
                    {
                        "thread_id": args.get("thread_id"),
                        "max_frames": args.get("max_frames"),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_select_frame":
        return (
            "gdb_context_manage",
            {
                "session_id": args["session_id"],
                "action": "select_frame",
                "context": {"frame": args["frame_number"]},
            },
            True,
        )
    if tool_name == "gdb_get_frame_info":
        return (
            "gdb_context_query",
            {"session_id": args["session_id"], "action": "frame", "query": {}},
            True,
        )
    if tool_name == "gdb_set_breakpoint":
        return (
            "gdb_breakpoint_manage",
            {
                "session_id": args["session_id"],
                "action": "create",
                "breakpoint": _compact_mapping(
                    {
                        "kind": "code",
                        "location": args["location"],
                        "condition": args.get("condition"),
                        "temporary": args.get("temporary", False),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_set_watchpoint":
        return (
            "gdb_breakpoint_manage",
            {
                "session_id": args["session_id"],
                "action": "create",
                "breakpoint": _compact_mapping(
                    {
                        "kind": "watch",
                        "expression": args["expression"],
                        "access": args.get("access", "write"),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_set_catchpoint":
        return (
            "gdb_breakpoint_manage",
            {
                "session_id": args["session_id"],
                "action": "create",
                "breakpoint": _compact_mapping(
                    {
                        "kind": "catch",
                        "event": args["kind"],
                        "argument": args.get("argument"),
                        "temporary": args.get("temporary", False),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_list_breakpoints":
        return (
            "gdb_breakpoint_query",
            {"session_id": args["session_id"], "action": "list", "query": {}},
            True,
        )
    if tool_name in {"gdb_delete_watchpoint", "gdb_delete_breakpoint", "gdb_enable_breakpoint", "gdb_disable_breakpoint"}:
        action_map = {
            "gdb_delete_watchpoint": "delete",
            "gdb_delete_breakpoint": "delete",
            "gdb_enable_breakpoint": "enable",
            "gdb_disable_breakpoint": "disable",
        }
        return (
            "gdb_breakpoint_manage",
            {
                "session_id": args["session_id"],
                "action": action_map[tool_name],
                "breakpoint": {"number": args["number"]},
            },
            True,
        )
    if tool_name == "gdb_evaluate_expression":
        query = {"expression": args["expression"]}
        context = _translate_context(args)
        if context is not None:
            query["context"] = context
        return (
            "gdb_inspect_query",
            {"session_id": args["session_id"], "action": "evaluate", "query": query},
            True,
        )
    if tool_name == "gdb_get_variables":
        context = _translate_context(args)
        query = {}
        if context is not None:
            query["context"] = context
        return (
            "gdb_inspect_query",
            {"session_id": args["session_id"], "action": "variables", "query": query},
            True,
        )
    if tool_name == "gdb_get_registers":
        query = _compact_mapping(
            {
                "register_numbers": args.get("register_numbers"),
                "register_names": args.get("register_names"),
                "include_vector_registers": args.get("include_vector_registers"),
                "max_registers": args.get("max_registers"),
                "value_format": args.get("value_format"),
            }
        )
        context = _translate_context(args)
        if context is not None:
            query["context"] = context
        return (
            "gdb_inspect_query",
            {"session_id": args["session_id"], "action": "registers", "query": query},
            True,
        )
    if tool_name == "gdb_read_memory":
        return (
            "gdb_inspect_query",
            {
                "session_id": args["session_id"],
                "action": "memory",
                "query": _compact_mapping(
                    {
                        "address": args["address"],
                        "count": args["count"],
                        "offset": args.get("offset"),
                    }
                ),
            },
            True,
        )
    if tool_name == "gdb_disassemble":
        query = {
            "location": _translate_location(args),
            "instruction_count": args.get("instruction_count", 32),
            "mode": args.get("mode", "mixed"),
        }
        context = _translate_context(args)
        if context is not None:
            query["context"] = context
        return (
            "gdb_inspect_query",
            {"session_id": args["session_id"], "action": "disassembly", "query": query},
            True,
        )
    if tool_name == "gdb_get_source_context":
        query = {
            "location": _translate_location(args),
            "context_before": args.get("context_before", 5),
            "context_after": args.get("context_after", 5),
        }
        context = _translate_context(args)
        if context is not None:
            query["context"] = context
        return (
            "gdb_inspect_query",
            {"session_id": args["session_id"], "action": "source", "query": query},
            True,
        )
    if tool_name == "gdb_batch":
        return (
            "gdb_workflow_batch",
            {
                "session_id": args["session_id"],
                "steps": _translate_batch_steps(
                    cast(int, args["session_id"]),
                    cast(object, args.get("steps", [])),
                ),
                "fail_fast": args.get("fail_fast", True),
                "capture_stop_events": args.get("capture_stop_events", True),
            },
            False,
        )

    return tool_name, args, False


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

    def invoke(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        translated_name, translated_arguments, flatten_result = _translate_legacy_call(
            tool_name,
            arguments,
        )
        result = asyncio.run(integration_runtime.call_tool(translated_name, translated_arguments))
        payload = cast(dict[str, object], json.loads(result[0].text))
        if flatten_result:
            return _flatten_action_result(payload)
        return payload

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
        **kwargs: object,
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
def stop_session(call_gdb_tool):
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
