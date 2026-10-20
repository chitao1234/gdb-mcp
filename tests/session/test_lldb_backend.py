"""Tests for the LLDB backend over a scripted Debug Adapter Protocol stream."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from gdb_mcp.domain import OperationResult, result_to_mapping
from gdb_mcp.session.lldb_backend import LldbDapBackend
from gdb_mcp.transport.dap_client import DapTransportError
from tests.support import dig, mapping, mapping_list, sequence, text


class FakeDapAdapter:
    """Scripted DAP adapter: answers requests and emits the events a flow needs."""

    def __init__(self, repl_outputs: dict[str, str] | None = None) -> None:
        self.repl_outputs = dict(repl_outputs or {})
        self._queue: deque[dict[str, object]] = deque()
        self._seq = 0
        self.commands: list[str] = []
        self.requests: list[dict[str, object]] = []
        self.closed = False

    def send(self, message: Mapping[str, Any]) -> None:
        self.commands.append(str(message.get("command")))
        self.requests.append(dict(message))
        self._answer(message)

    def receive(self) -> Mapping[str, Any]:
        if not self._queue:
            raise DapTransportError("the scripted adapter has nothing left to send")
        return self._queue.popleft()

    def close(self) -> None:
        self.closed = True

    def _respond(
        self,
        request: Mapping[str, Any],
        body: dict[str, object] | None = None,
    ) -> None:
        self._seq += 1
        self._queue.append(
            {
                "type": "response",
                "seq": self._seq,
                "request_seq": request.get("seq"),
                "success": True,
                "command": request.get("command"),
                "body": body or {},
            }
        )

    def _event(self, name: str, body: dict[str, object]) -> None:
        self._seq += 1
        self._queue.append({"type": "event", "seq": self._seq, "event": name, "body": body})

    def _answer(self, request: Mapping[str, Any]) -> None:
        command = request.get("command")
        arguments = request.get("arguments")
        kwargs = arguments if isinstance(arguments, dict) else {}

        if command == "initialize":
            self._respond(request, {"supportsConfigurationDoneRequest": True})
        elif command in {"launch", "attach"}:
            self._respond(request)
            self._event("initialized", {})
            if command == "attach":
                self._event("stopped", {"reason": "breakpoint", "threadId": 1})
        elif command == "configurationDone":
            self._respond(request)
            self._event("stopped", {"reason": "entry", "threadId": 1, "allThreadsStopped": True})
        elif command == "threads":
            self._respond(request, {"threads": [{"id": 1, "name": "Thread #1 probe"}]})
        elif command == "stackTrace":
            start = int(kwargs.get("startFrame", 0))
            if start >= 2:
                self._respond(request, {"stackFrames": []})
                return
            self._respond(
                request,
                {
                    "stackFrames": [
                        {
                            "id": 1000 + start,
                            "name": "main" if start == 0 else "caller",
                            "line": 9 + start,
                            "column": 5,
                            "source": {"name": "probe.c", "path": "/src/probe.c"},
                            "instructionPointerReference": "0x1000",
                        }
                    ]
                },
            )
        elif command == "scopes":
            self._respond(
                request,
                {
                    "scopes": [
                        {"name": "Locals", "variablesReference": 10, "presentationHint": "locals"},
                        {
                            "name": "Registers",
                            "variablesReference": 20,
                            "presentationHint": "registers",
                        },
                    ]
                },
            )
        elif command == "variables":
            reference = int(kwargs.get("variablesReference", 0))
            if reference == 10:
                self._respond(
                    request,
                    {
                        "variables": [
                            {"name": "total", "type": "int", "value": "0", "variablesReference": 0}
                        ]
                    },
                )
            elif reference == 20:
                self._respond(
                    request,
                    {
                        "variables": [
                            {"name": "General Purpose", "value": "", "variablesReference": 21}
                        ]
                    },
                )
            else:
                self._respond(
                    request,
                    {
                        "variables": [
                            {"name": "rax", "type": "unsigned long", "value": "0x1"},
                            {"name": "rip", "type": "unsigned long", "value": "0x1000"},
                        ]
                    },
                )
        elif command == "evaluate":
            expression = str(kwargs.get("expression", ""))
            result = self.repl_outputs.get(expression, f"result of {expression}")
            self._respond(request, {"result": result, "type": "int"})
        elif command in {"setBreakpoints", "setFunctionBreakpoints"}:
            requested = kwargs.get("breakpoints")
            first = requested[0] if isinstance(requested, list) and requested else {}
            line = first.get("line", 9) if isinstance(first, dict) else 9
            self._respond(
                request,
                {
                    "breakpoints": [
                        {
                            "id": 1,
                            "verified": True,
                            "line": line,
                            "source": {"name": "probe.c", "path": "/src/probe.c"},
                        }
                    ]
                },
            )
        elif command in {"continue", "next", "stepIn", "stepOut", "pause"}:
            self._respond(request)
            self._event(
                "stopped",
                {"reason": "breakpoint", "threadId": 1, "hitBreakpointIds": [1]},
            )
        elif command == "disconnect":
            self._respond(request)
        else:
            self._seq += 1
            self._queue.append(
                {
                    "type": "response",
                    "seq": self._seq,
                    "request_seq": request.get("seq"),
                    "success": False,
                    "message": f"unsupported request {command}",
                }
            )


def _backend(adapter: FakeDapAdapter) -> LldbDapBackend:
    backend = LldbDapBackend(
        os_module=__import__("os"),
        time_module=__import__("time"),
        stream_factory=lambda command, cwd=None: adapter,
        adapter_path="/usr/bin/lldb-dap",
    )
    return backend


def _started(adapter: FakeDapAdapter) -> tuple[LldbDapBackend, Mapping[str, object]]:
    backend = _backend(adapter)
    started = result_to_mapping(backend.start(program="/bin/probe"))
    assert started["status"] == "success", started
    return backend, started


class TestLldbStart:
    """Starting a session spawns the adapter and launches at the entry point."""

    def test_start_launches_at_the_entry_point(self) -> None:
        adapter = FakeDapAdapter()
        backend, started = _started(adapter)

        assert started["target_loaded"] is True
        assert backend.runtime.execution_state == "paused"
        assert backend.runtime.stop_reason == "entry"
        assert backend.runtime.stop_kind == "entry"
        assert backend.runtime.has_controller is True

    def test_start_sends_the_launch_configuration(self) -> None:
        adapter = FakeDapAdapter()
        backend = _backend(adapter)
        backend.start(
            program="/bin/probe",
            args=["--fast"],
            env={"MODE": "test"},
            working_dir="/tmp",
            init_commands=["settings set target.x86-disassembly-flavor intel"],
        )

        launch = next(request for request in adapter.requests if request["command"] == "launch")
        arguments = mapping(launch["arguments"])
        assert arguments["program"] == "/bin/probe"
        assert arguments["args"] == ["--fast"]
        assert arguments["env"] == {"MODE": "test"}
        assert arguments["cwd"] == "/tmp"
        assert arguments["stopOnEntry"] is True
        assert arguments["initCommands"] == ["settings set target.x86-disassembly-flavor intel"]

    @pytest.mark.parametrize(
        "start_with",
        [
            lambda backend: backend.start(program="/bin/probe", core="/tmp/core"),
            lambda backend: backend.start(program="/bin/probe", endpoint="tcp://127.0.0.1:4399"),
            lambda backend: backend.start(program="/bin/probe", replay="/tmp/trace"),
        ],
    )
    def test_start_rejects_modes_lldb_cannot_drive(
        self, start_with: Callable[[LldbDapBackend], OperationResult[object]]
    ) -> None:
        started = result_to_mapping(start_with(_backend(FakeDapAdapter())))

        assert started["status"] == "error"
        assert started["code"] == "unsupported_operation"

    def test_a_gdb_session_is_not_driven_by_this_backend(self) -> None:
        backend = _backend(FakeDapAdapter())
        started = result_to_mapping(backend.start(program="/bin/probe", debugger="gdb"))

        assert started["status"] == "error"
        assert started["code"] == "unsupported_operation"
        assert "drives lldb" in text(started["message"])


class TestLldbExecution:
    """Execution requests map onto DAP and record the adapter's stop events."""

    def test_run_stops_on_the_first_breakpoint(self) -> None:
        adapter = FakeDapAdapter()
        backend, _ = _started(adapter)
        backend.set_breakpoint("/src/probe.c:9")

        result = result_to_mapping(backend.run())

        assert result["status"] == "success"
        assert backend.runtime.stop_reason == "breakpoint"
        assert backend.runtime.stop_kind == "breakpoint"
        assert adapter.commands.count("continue") == 1

    def test_step_records_the_stop_event(self) -> None:
        adapter = FakeDapAdapter()
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.step())

        assert result["status"] == "success"
        assert adapter.commands.count("stepIn") == 1
        event = backend.runtime.last_stop_event
        assert event is not None
        assert event.breakpoint_number == "1"
        assert event.frame is not None
        assert event.frame.func == "main"

    def test_run_with_arguments_is_refused(self) -> None:
        backend, _ = _started(FakeDapAdapter())
        result = result_to_mapping(backend.run(args=["--other"]))

        assert result["status"] == "error"
        assert result["code"] == "unsupported_operation"

    def test_reverse_execution_is_refused(self) -> None:
        backend, _ = _started(FakeDapAdapter())
        result = result_to_mapping(backend.continue_execution(reverse=True))

        assert result["status"] == "error"
        assert result["code"] == "unsupported_operation"

    def test_stop_disconnects_the_adapter(self) -> None:
        adapter = FakeDapAdapter()
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.stop())

        assert result["status"] == "success"
        assert "disconnect" in adapter.commands
        assert adapter.closed is True
        assert backend.runtime.has_controller is False


class TestLldbEscapeHatch:
    """Raw LLDB commands run through the adapter's REPL."""

    def test_execute_command_uses_the_repl_context(self) -> None:
        adapter = FakeDapAdapter()
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.execute_command("image list"))

        assert result["status"] == "success"
        assert result["output"] == "result of image list"
        evaluate = next(request for request in adapter.requests if request["command"] == "evaluate")
        assert mapping(evaluate["arguments"])["context"] == "repl"

    def test_call_function_returns_the_result(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.call_function("strlen(str)"))

        assert result["status"] == "success"
        assert result["function_call"] == "strlen(str)"
        assert result["result"] == "result of strlen(str)"


class TestLldbInspection:
    """Threads, frames, and variables map onto the shared payload records."""

    def test_threads(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.get_threads())

        threads = mapping_list(result["threads"])
        assert threads[0]["id"] == "1"
        assert threads[0]["name"] == "Thread #1 probe"
        assert result["count"] == 1

    def test_backtrace(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.get_backtrace())

        frames = mapping_list(result["frames"])
        assert frames[0]["level"] == 0
        assert frames[0]["func"] == "main"
        assert frames[0]["file"] == "/src/probe.c"
        assert frames[0]["addr"] == "0x1000"

    def test_frame_info_uses_the_selected_frame(self) -> None:
        backend, _ = _started(FakeDapAdapter())
        backend.select_frame(1)

        result = result_to_mapping(backend.get_frame_info())

        assert dig(result, "frame", "level") == 1

    def test_variables_skip_the_register_scope(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.get_variables())

        variables = mapping_list(result["variables"])
        assert [variable["name"] for variable in variables] == ["total"]

    def test_registers_read_the_register_scope(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.get_registers())

        registers = mapping_list(result["registers"])
        assert [register["name"] for register in registers] == ["rax", "rip"]
        assert registers[0]["value"] == "0x1"

    def test_evaluate(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.evaluate_expression("total + 1"))

        assert result["expression"] == "total + 1"
        assert result["value"] == "result of total + 1"

    def test_select_frame_rejects_a_missing_frame(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.select_frame(3))

        assert result["status"] == "error"
        assert result["code"] == "not_found"


class TestLldbBreakpoints:
    """DAP confirms breakpoints per source, so the backend keeps the inventory."""

    def test_set_breakpoint_records_the_confirmation(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.set_breakpoint("/src/probe.c:9", condition="total > 3"))

        breakpoint = mapping(result["breakpoint"])
        assert breakpoint["number"] == "1"
        assert breakpoint["verified"] is True
        assert breakpoint["condition"] == "total > 3"
        assert breakpoint["original-location"] == "/src/probe.c:9"

    def test_list_get_and_delete(self) -> None:
        backend, _ = _started(FakeDapAdapter())
        backend.set_breakpoint("/src/probe.c:9")

        listed = result_to_mapping(backend.list_breakpoints())
        assert listed["count"] == 1
        assert dig(result_to_mapping(backend.get_breakpoint(1)), "breakpoint", "number") == "1"

        deleted = result_to_mapping(backend.delete_breakpoint(1))
        assert deleted["status"] == "success"
        assert result_to_mapping(backend.list_breakpoints())["count"] == 0

    def test_disable_keeps_the_breakpoint(self) -> None:
        backend, _ = _started(FakeDapAdapter())
        backend.set_breakpoint("/src/probe.c:9")

        result_to_mapping(backend.disable_breakpoint(1))

        breakpoint = mapping(result_to_mapping(backend.get_breakpoint(1))["breakpoint"])
        assert breakpoint["enabled"] is False

    def test_a_breakpoint_by_address_is_refused(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.set_breakpoint("*0x401000"))

        assert result["status"] == "error"
        assert result["code"] == "unsupported_operation"


class TestLldbUnsupportedOperations:
    """Operations with no DAP equivalent answer instead of pretending."""

    @pytest.mark.parametrize(
        "operation",
        [
            "set_catchpoint",
            "add_inferior",
            "remove_inferior",
            "set_follow_fork_mode",
            "set_detach_on_fork",
        ],
    )
    def test_unsupported_operations_report_their_code(self, operation: str) -> None:
        backend, _ = _started(FakeDapAdapter())
        call = {
            "set_catchpoint": lambda: backend.set_catchpoint("throw"),
            "add_inferior": lambda: backend.add_inferior(executable="/bin/true"),
            "remove_inferior": lambda: backend.remove_inferior(1),
            "set_follow_fork_mode": lambda: backend.set_follow_fork_mode("child"),
            "set_detach_on_fork": lambda: backend.set_detach_on_fork(True),
        }[operation]

        result = result_to_mapping(call())

        assert result["status"] == "error"
        assert result["code"] == "unsupported_operation"

    def test_a_read_watchpoint_points_at_the_escape_hatch(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.set_watchpoint("total", access="read"))

        assert result["status"] == "error"
        assert result["code"] == "unsupported_operation"
        assert "gdb_execute_command" in text(result["message"])

    def test_capabilities_match_the_implemented_operations(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        assert backend.capabilities.debugger == "lldb"
        assert backend.capabilities.supports("set_breakpoint") is True
        assert backend.capabilities.supports("set_watchpoint") is True
        assert backend.capabilities.supports("read_memory") is True
        assert backend.capabilities.supports("disassemble") is True
        assert backend.capabilities.supports("source") is True
        assert backend.capabilities.supports("set_catchpoint") is False
        assert backend.capabilities.supports("add_inferior") is False

    def test_inferiors_describe_the_one_program(self) -> None:
        backend, _ = _started(FakeDapAdapter())

        result = result_to_mapping(backend.list_inferiors())

        assert result["count"] == 1
        assert mapping_list(result["inferiors"])[0]["display"] == "/bin/probe"
        assert result["current_inferior_id"] == 1
        assert sequence(result["inferiors"])


_MEMORY_TEXT = (
    '(lldb) memory read -c 8 -s 1 -f x "0x1000"\n'
    "0x1000: 0x01 0x00 0x00 0x00 0x02 0x00 0x00 0x00\n"
)
_DISASSEMBLY_TEXT = (
    "(lldb) disassemble -n describe -c 3\n"
    "probe_c`describe:\n"
    "->  0x5555555551a0 <+31>: movl   $0x1, -0x14(%rbp)\n"
    "    0x5555555551a7 <+38>: movl   $0x2, -0x10(%rbp)\n"
)
_DISASSEMBLY_MIXED_TEXT = (
    '(lldb) disassemble -m -n "describe" -c 6\n'
    "\n** 8   \tint describe(struct point p) {\n"
    "\nprobe_c`describe:\n"
    "    0x555555555181 <+0>:  endbr64 \n"
    "    0x555555555185 <+4>:  pushq  %rbp\n"
    "\n   6   \tint add(int a, int b) { return a + b; }\n"
    "   7   \t\n"
    "** 8   \tint describe(struct point p) {\n"
    "\n    0x555555555189 <+8>:  subq   $0x30, %rsp\n"
    "\n-> 9   \t    int nums[3] = {1, 2, 3};\n"
    "\n->  0x5555555551a0 <+31>: movl   $0x1, -0x14(%rbp)\n"
    "    0x5555555551a7 <+38>: movl   $0x2, -0x10(%rbp)\n"
)
_SOURCE_TEXT = (
    "(lldb) source list -f probe.c -l 8 -c 2\n"
    "   8   \tint describe(struct point p) {\n"
    "   9   \t    int nums[3] = {1, 2, 3};\n"
    "   10  \t    int total = nums[0] + nums[1] + nums[2];\n"
)
_WATCHPOINT_TEXT = (
    "(lldb) watchpoint set variable p\n"
    "Watchpoint created: Watchpoint 1: addr = 0x7fffffffdbf8 size = 8 state = enabled type = m\n"
    "    declare @ '/src/probe.c:8:27'\n"
    "    watchpoint spec = 'p'\n"
)


class TestLldbParsedCommands:
    """LLDB reports these results as text, so the backend parses them."""

    def test_memory_read_parses_the_hex_dump(self) -> None:
        adapter = FakeDapAdapter({_MEMORY_TEXT.splitlines()[0][7:]: _MEMORY_TEXT})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.read_memory("0x1000", 8))

        assert result["status"] == "success"
        assert result["captured_bytes"] == 8
        assert result["block_count"] == 1
        block = mapping_list(result["blocks"])[0]
        assert block["begin"] == "0x1000"
        assert block["end"] == "0x1008"
        assert block["contents"] == "0100000002000000"
        assert "warnings" not in result

    def test_memory_read_reports_a_partial_read(self) -> None:
        text = '(lldb) memory read -c 8 -s 1 -f x "0x1000"\n0x1000: 0x01 0x00\n'
        adapter = FakeDapAdapter({_MEMORY_TEXT.splitlines()[0][7:]: text})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.read_memory("0x1000", 8))

        assert result["captured_bytes"] == 2
        warnings = [str(item) for item in sequence(result["warnings"])]
        assert "Partial read: captured 2 of 8 bytes" in warnings

    def test_memory_read_reports_a_command_error(self) -> None:
        adapter = FakeDapAdapter(
            {
                'memory read -c 8 -s 1 -f x "0x1"': '(lldb) memory read -c 8 -s 1 -f x "0x1"\n'
                "error: memory read failed for 0x0\n"
            }
        )
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.read_memory("0x1", 8))

        assert result["status"] == "error"
        assert result["code"] == "memory_unavailable"
        assert "memory read failed" in text(result["message"])

    def test_a_partial_read_keeps_its_data_and_reports_the_error(self) -> None:
        command = 'memory read -c 8 -s 1 -f x "0x1000"'
        text_with_error = (
            '(lldb) memory read -c 8 -s 1 -f x "0x1000"\n'
            "0x1000: 0x01 0x00\n"
            "error: memory read failed for 0x0\n"
        )
        adapter = FakeDapAdapter({command: text_with_error})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.read_memory("0x1000", 8))

        assert result["status"] == "success"
        assert result["captured_bytes"] == 2
        warnings = [str(item) for item in sequence(result["warnings"])]
        assert "Partial read: captured 2 of 8 bytes" in warnings
        assert "memory read failed for 0x0" in warnings

    def test_an_escape_hatch_command_that_fails_is_reported(self) -> None:
        adapter = FakeDapAdapter(
            {
                "nosuchcommand": "(lldb) nosuchcommand\nerror: 'nosuchcommand' is not a valid command.\n"
            }
        )
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.execute_command("nosuchcommand"))

        assert result["status"] == "error"
        assert result["code"] == "error"
        assert "not a valid command" in text(result["message"])

    def test_an_escape_hatch_failure_keeps_the_whole_output(self) -> None:
        """A failed command's message carries everything it printed, caret lines included."""

        output = (
            "(lldb) expression -- nosuchvar\n"
            "error: <user expression 0>:1:1: use of undeclared identifier 'nosuchvar'\n"
            "    1 | nosuchvar\n"
            "      | ^\n"
        )
        adapter = FakeDapAdapter({"expression -- nosuchvar": output})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.execute_command("expression -- nosuchvar"))

        assert result["status"] == "error"
        assert "use of undeclared identifier" in text(result["message"])
        assert "1 | nosuchvar" in text(result["message"])
        assert dig(result, "details", "command") == "expression -- nosuchvar"

    def test_an_escape_hatch_command_with_output_stays_successful(self) -> None:
        adapter = FakeDapAdapter(
            {
                "image list": "(lldb) image list\n[  0] abc 0x1000 /bin/probe\n"
                "warning: something small\n"
            }
        )
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.execute_command("image list"))

        assert result["status"] == "success"
        assert "/bin/probe" in text(result["output"])

    def test_memory_read_applies_the_offset(self) -> None:
        command = 'memory read -c 8 -s 1 -f x "(0x1000) + 4"'
        adapter = FakeDapAdapter({command: _MEMORY_TEXT})
        backend, _ = _started(adapter)

        backend.read_memory("0x1000", 8, offset=4)

        expressions = [
            mapping(request["arguments"])["expression"]
            for request in adapter.requests
            if request["command"] == "evaluate"
        ]
        assert command in expressions

    def test_disassembly_parses_instructions(self) -> None:
        adapter = FakeDapAdapter({'disassemble -m -n "describe" -c 3': _DISASSEMBLY_TEXT})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.disassemble(function="describe", instruction_count=3))

        assert result["status"] == "success"
        assert result["function"] == "describe"
        assert result["count"] == 2
        first = mapping_list(result["instructions"])[0]
        assert first["address"] == "0x5555555551a0"
        assert first["offset"] == 31
        assert first["instruction"] == "movl   $0x1, -0x14(%rbp)"
        assert first["is_current"] is True
        assert first["function"] == "describe"
        assert result["start_address"] == "0x5555555551a0"
        assert result["end_address"] == "0x5555555551a8"

    def test_disassembly_mixed_mode_attaches_the_source_line(self) -> None:
        adapter = FakeDapAdapter({'disassemble -m -n "describe" -c 6': _DISASSEMBLY_MIXED_TEXT})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.disassemble(function="describe", instruction_count=6))

        instructions = mapping_list(result["instructions"])
        # `**` marks the instructions' source line; `-> N` marks the stop's own line,
        # and unprefixed context lines must not re-attribute what follows.
        assert [instruction["line"] for instruction in instructions] == [8, 8, 8, 9, 9]
        assert [instruction["is_current"] for instruction in instructions] == [
            False,
            False,
            False,
            True,
            False,
        ]
        assert instructions[0]["instruction"] == "endbr64"

    def test_source_context_parses_a_window(self) -> None:
        command = "source list -f probe.c -l 8 -c 2"
        adapter = FakeDapAdapter({command: _SOURCE_TEXT})
        backend, _ = _started(adapter)

        result = result_to_mapping(
            backend.get_source_context(file="probe.c", start_line=8, end_line=10)
        )

        assert result["status"] == "success"
        assert result["scope"] == "file_range"
        assert result["start_line"] == 8
        assert result["end_line"] == 10
        assert result["count"] == 3
        lines = mapping_list(result["lines"])
        assert lines[0]["text"] == "int describe(struct point p) {"
        assert lines[1]["line_number"] == 9

    def test_source_context_reports_missing_source(self) -> None:
        command = "source list -n nosuch -c 10"
        adapter = FakeDapAdapter(
            {
                command: "(lldb) source list -n nosuch -c 10\n"
                'error: Could not find function named: "nosuch".\n'
            }
        )
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.get_source_context(function="nosuch"))

        assert result["status"] == "error"
        assert result["code"] == "not_found"
        assert "Could not find function" in text(result["message"])

    def test_watchpoint_set_parses_the_creation(self) -> None:
        adapter = FakeDapAdapter({'watchpoint set variable "p"': _WATCHPOINT_TEXT})
        backend, _ = _started(adapter)

        result = result_to_mapping(backend.set_watchpoint("p"))

        breakpoint = mapping(result["breakpoint"])
        assert breakpoint["type"] == "watchpoint"
        assert breakpoint["number"] == "1"
        assert breakpoint["what"] == "p"
        assert breakpoint["addr"] == "0x7fffffffdbf8"
        assert breakpoint["file"] == "/src/probe.c"
        assert breakpoint["enabled"] is True

        listed = result_to_mapping(backend.list_breakpoints())
        assert listed["count"] == 1

    def test_watchpoint_disable_and_delete_use_the_native_number(self) -> None:
        adapter = FakeDapAdapter(
            {
                'watchpoint set variable "p"': _WATCHPOINT_TEXT,
                "watchpoint disable 1": "Watchpoint 1 disabled\n",
                "watchpoint enable 1": "Watchpoint 1 enabled\n",
                "watchpoint delete 1": "Watchpoint 1 deleted\n",
            }
        )
        backend, _ = _started(adapter)
        backend.set_watchpoint("p")

        result_to_mapping(backend.disable_breakpoint(1))
        result_to_mapping(backend.enable_breakpoint(1))
        result_to_mapping(backend.delete_breakpoint(1))

        expressions = [
            mapping(request["arguments"])["expression"]
            for request in adapter.requests
            if request["command"] == "evaluate"
        ]
        assert "watchpoint disable 1" in expressions
        assert "watchpoint enable 1" in expressions
        assert "watchpoint delete 1" in expressions
        assert result_to_mapping(backend.list_breakpoints())["count"] == 0

    def test_disabling_a_breakpoint_pushes_the_remaining_set(self) -> None:
        adapter = FakeDapAdapter()
        backend, _ = _started(adapter)
        backend.set_breakpoint("/src/probe.c:9")
        backend.set_breakpoint("/src/probe.c:10")

        result_to_mapping(backend.disable_breakpoint(1))

        pushes = [request for request in adapter.requests if request["command"] == "setBreakpoints"]
        assert mapping(pushes[-1]["arguments"])["breakpoints"] == [{"line": 10}]

    def test_deleting_the_last_breakpoint_clears_the_source(self) -> None:
        adapter = FakeDapAdapter()
        backend, _ = _started(adapter)
        backend.set_breakpoint("/src/probe.c:9")

        result_to_mapping(backend.delete_breakpoint(1))

        pushes = [request for request in adapter.requests if request["command"] == "setBreakpoints"]
        assert mapping(pushes[-1]["arguments"])["breakpoints"] == []
