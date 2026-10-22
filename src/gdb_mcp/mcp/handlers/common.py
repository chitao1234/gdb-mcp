"""Shared handler protocols, envelopes, and payload helpers."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar

from pydantic import BaseModel

from ... import contracts as shared_contracts
from ...contracts import (
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
    ExecutionWaitUntil,
    SessionState,
)
from ...domain import (
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    ToolPayload,
    payload_to_mapping,
)
from ...domain.errors import DebuggerDecodeError
from ...session.backends import BackendOperation
from ...session.constants import DEFAULT_TIMEOUT_SEC
from ...session.registry import SessionRegistry
from ...session.service import SessionService
from ..schemas import (
    LocationAddressArgs,
    LocationAddressRangeArgs,
    LocationCurrentArgs,
    LocationFileLineArgs,
    LocationFileRangeArgs,
    LocationFunctionArgs,
)


class MemoryRangeArgsProtocol(Protocol):
    """Validated MCP range models used for bundle memory capture."""

    address: str
    count: int
    offset: int
    name: str | None


ToolArguments: TypeAlias = StructuredPayload


ToolResult: TypeAlias = OperationResult[ToolPayload]


LocationArgs: TypeAlias = (
    LocationCurrentArgs
    | LocationFunctionArgs
    | LocationAddressArgs
    | LocationAddressRangeArgs
    | LocationFileLineArgs
    | LocationFileRangeArgs
)


_MEMORY_RANGE_SHORTHAND_RE = re.compile(r"^(?P<address>.+):(?P<count>\d+)(?:@(?P<offset>\d+))?$")


ToolScope: TypeAlias = Literal["session", "registry"]


@dataclass(frozen=True)
class SessionToolInvocation:
    """Validated request bound to its session handler."""

    session_id: int
    action: str | None
    execute: Callable[[SessionService], ToolResult]
    operation: BackendOperation | None = None


@dataclass(frozen=True)
class RegistryToolInvocation:
    """Validated request bound to its registry handler."""

    action: str | None
    execute: Callable[[SessionRegistry], ToolResult]


ToolArgsT = TypeVar("ToolArgsT", bound=BaseModel)
PayloadT = TypeVar("PayloadT")
ToolInvocation: TypeAlias = SessionToolInvocation | RegistryToolInvocation
InvocationT_co = TypeVar("InvocationT_co", bound=ToolInvocation, covariant=True)


@dataclass(frozen=True)
class RegisteredToolSpec(Generic[InvocationT_co]):
    """Validate a request while keeping its concrete arguments in a closure."""

    model: type[BaseModel]
    scope: ToolScope
    prepare: Callable[[object], InvocationT_co]


@dataclass(frozen=True, slots=True)
class LocationSelection:
    """Typed inspection location resolved from one schema-discriminated selector."""

    function: str | None = None
    address: str | None = None
    start_address: str | None = None
    end_address: str | None = None
    file: str | None = None
    line: int | None = None
    start_line: int | None = None
    end_line: int | None = None


def session_tool_spec(
    model: type[ToolArgsT],
    handler: Callable[[SessionService, ToolArgsT], ToolResult],
    *,
    session_id: Callable[[ToolArgsT], int],
    action: Callable[[ToolArgsT], str | None] = lambda args: None,
    operation: Callable[[ToolArgsT], BackendOperation | None] = lambda args: None,
) -> RegisteredToolSpec[SessionToolInvocation]:
    """Wrap a ``(session, args)`` handler as a session-scoped tool spec."""

    def prepare(arguments: object) -> SessionToolInvocation:
        args = model.model_validate(arguments)
        backend_operation = operation(args)

        def execute(session: SessionService) -> ToolResult:
            if backend_operation is not None and not session.capabilities.supports(
                backend_operation
            ):
                return OperationError(
                    message=(
                        f"{session.capabilities.debugger} does not support "
                        f"operation {backend_operation!r}"
                    ),
                    code="unsupported_operation",
                    details={
                        "debugger": session.capabilities.debugger,
                        "operation": backend_operation,
                    },
                )
            try:
                return handler(session, args)
            except DebuggerDecodeError as exc:
                return OperationError(message=str(exc), code="protocol_error")

        return SessionToolInvocation(session_id(args), action(args), execute, backend_operation)

    return RegisteredToolSpec(model=model, scope="session", prepare=prepare)


def registry_tool_spec(
    model: type[ToolArgsT],
    handler: Callable[[ToolArgsT, SessionRegistry], ToolResult],
    *,
    action: Callable[[ToolArgsT], str | None] = lambda args: None,
) -> RegisteredToolSpec[RegistryToolInvocation]:
    """Wrap an ``(args, registry)`` handler as a registry-scoped tool spec."""

    def prepare(arguments: object) -> RegistryToolInvocation:
        args = model.model_validate(arguments)

        def execute(registry: SessionRegistry) -> ToolResult:
            return handler(args, registry)

        return RegistryToolInvocation(action(args), execute)

    return RegisteredToolSpec(model=model, scope="registry", prepare=prepare)


def _normalize_arguments(arguments: object) -> ToolArguments:
    """Normalize tool arguments into a dictionary for Pydantic validation."""

    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object")
    payload: ToolArguments = {}
    for key, value in arguments.items():
        if not isinstance(key, str):
            raise TypeError("Tool arguments must be a JSON object")
        payload[key] = payload_to_mapping(value)
    return payload


def _wrap_action_result(
    action: str | None, result: OperationResult[PayloadT]
) -> OperationResult[PayloadT]:
    """Wrap a tool result in the v2 action envelope."""

    if action is None:
        return result

    if isinstance(result, OperationError):
        details = dict(result.details)
        details.setdefault("action", action)
        return OperationError(
            message=result.message,
            code=result.code,
            fatal=result.fatal,
            details=details,
        )

    return OperationSuccess(result.value, warnings=result.warnings, action=action)


def execute_session_invocation(
    invocation: SessionToolInvocation,
    session: SessionService,
    *,
    session_id: int | None,
) -> ToolResult:
    """Apply shared session failure and action handling to one validated invocation."""

    if session.state in {SessionState.STOPPED, SessionState.CLOSING}:
        result: ToolResult = OperationError(
            message="Session is closing or stopped", code="session_closing"
        )
    else:
        result = invocation.execute(session)
    result = _annotate_session_failure(
        result,
        session,
        session_id=session_id,
    )
    return _wrap_action_result(invocation.action, result)


# GDB reports these conditions as free-form text and this server forwards the
# text unchanged. Classify the ones a caller can branch on, so an envelope is
# actionable without parsing the message.
_GDB_FAILURE_CODES: tuple[tuple[str, str], ...] = (
    ("no registers", "no_registers"),
    ("the program is not being run", "inferior_not_running"),
    ("you can't do that without a process to debug", "inferior_not_running"),
    ("while the selected thread is running", "inferior_running"),
    ("no stack", "no_stack_frame"),
    ("no frame selected", "no_stack_frame"),
    ("cannot access memory at address", "memory_unavailable"),
    ("no symbol table is loaded", "no_symbols_loaded"),
    ("no symbol", "symbol_unavailable"),
    ("optimized out", "value_unavailable"),
    ("timeout", "command_timeout"),
)


def _failure_code(message: str) -> str | None:
    """Return a branchable code for one free-form GDB failure message."""

    lowered = message.lower()
    for fragment, code in _GDB_FAILURE_CODES:
        if fragment in lowered:
            return code
    return None


def _session_state_details(session: SessionService) -> StructuredPayload:
    """Return the JSON-safe execution state of one live session."""

    snapshot = session.runtime.status_snapshot(debugger_alive=session.is_running)
    details: StructuredPayload = {"execution_state": snapshot.execution_state}
    for name, value in (
        ("stop_reason", snapshot.stop_reason),
        ("stop_kind", snapshot.stop_kind),
    ):
        if value is not None:
            details[name] = value
    if snapshot.exit_code is not None:
        details["exit_code"] = snapshot.exit_code
    return details


def _annotate_session_failure(
    result: OperationResult[PayloadT], session: SessionService, *, session_id: int | None
) -> OperationResult[PayloadT]:
    """Attach session state and a specific code to one session-scoped failure.

    A failure whose message is only ``The program is not being run.`` leaves the
    caller guessing at the session's state, so carry that state in the envelope
    and classify the message into a code the caller can branch on.
    """

    if not isinstance(result, OperationError):
        return result

    details = dict(result.details)
    if session_id is not None:
        details.setdefault("session_id", session_id)
    for name, value in _session_state_details(session).items():
        details.setdefault(name, value)

    code = result.code
    if code == "error":
        code = _failure_code(result.message) or code

    return OperationError(
        message=result.message,
        code=code,
        fatal=result.fatal,
        details=details,
    )


def _workflow_step_validation_error(
    tool_name: str,
    issue: shared_contracts.WorkflowStepValidationIssue,
    *,
    index: int,
) -> OperationError:
    if issue.kind == "session_id_not_allowed":
        return OperationError(
            message=(
                f"Batch step {index} ({tool_name}) must not include session_id. "
                f"It is inherited from {TOOL_WORKFLOW_BATCH}."
            ),
            code="validation_error",
        )
    if issue.kind == "session_query_list_not_allowed":
        return OperationError(
            message=(
                f"{TOOL_SESSION_QUERY}(action={shared_contracts.ACTION_LIST}) "
                f"is not valid inside {TOOL_WORKFLOW_BATCH}"
            ),
            code="unsupported_combination",
        )
    if issue.kind == "session_manage_not_allowed":
        return OperationError(
            message=f"{TOOL_SESSION_MANAGE} is not valid inside {TOOL_WORKFLOW_BATCH}",
            code="unsupported_combination",
        )
    return OperationError(
        message=f"Unsupported batch step tool: {tool_name}",
        code="unknown_tool",
    )


def _execution_wait_policy(
    wait_until: ExecutionWaitUntil,
    timeout_sec: int | None,
) -> tuple[int, bool]:
    """Translate a flattened execution wait policy into service-layer arguments."""

    resolved_timeout = timeout_sec if timeout_sec is not None else DEFAULT_TIMEOUT_SEC
    return resolved_timeout, wait_until == "stop"


def _normalize_run_args(args: list[str] | str | None) -> list[str] | None | OperationError:
    """Normalize run-argument input into argv list form."""

    if args is None:
        return None
    if isinstance(args, str):
        try:
            return shlex.split(args)
        except ValueError as exc:
            return OperationError(
                message=f"Invalid args string: {exc}",
                code="validation_error",
            )
    return list(args)


def _memory_capture_ranges(
    memory_ranges: Sequence[MemoryRangeArgsProtocol | str],
) -> list[MemoryCaptureRange] | OperationError:
    """Convert validated memory-range models into typed internal requests."""

    normalized: list[MemoryCaptureRange] = []
    for index, memory_range in enumerate(memory_ranges):
        if isinstance(memory_range, str):
            parsed = _parse_memory_range_shorthand(memory_range, index=index)
            if isinstance(parsed, OperationError):
                return parsed
            normalized.append(parsed)
            continue

        normalized.append(
            MemoryCaptureRange(
                address=memory_range.address,
                count=memory_range.count,
                offset=memory_range.offset,
                name=memory_range.name,
            )
        )

    return normalized


def _parse_memory_range_shorthand(value: str, *, index: int) -> MemoryCaptureRange | OperationError:
    """Parse '<address>:<count>' (optional '@<offset>') memory-range shorthand."""

    text = value.strip()
    if not text:
        return OperationError(
            message=f"Invalid memory_ranges[{index}]: empty shorthand string",
            code="validation_error",
        )

    match = _MEMORY_RANGE_SHORTHAND_RE.match(text)
    if match is None:
        return OperationError(
            message=(
                f"Invalid memory_ranges[{index}] shorthand: {value!r}. "
                "Expected '<address>:<count>' or '<address>:<count>@<offset>'."
            ),
            code="validation_error",
        )

    address = match.group("address").strip()
    if not address:
        return OperationError(
            message=f"Invalid memory_ranges[{index}] shorthand: missing address expression",
            code="validation_error",
        )

    count = int(match.group("count"))
    offset_text = match.group("offset")
    offset = int(offset_text) if offset_text is not None else 0
    return MemoryCaptureRange(address=address, count=count, offset=offset)
