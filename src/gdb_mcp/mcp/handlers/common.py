"""Shared handler protocols, envelopes, and payload helpers."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar, cast, runtime_checkable

from pydantic import BaseModel, RootModel

from ... import contracts as shared_contracts
from ...contracts import (
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
    ExecutionWaitUntil,
)
from ...domain import (
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
)
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


class SessionArgsProtocol(Protocol):
    """Validated MCP argument models that carry a session_id."""

    session_id: int


class MemoryRangeArgsProtocol(Protocol):
    """Validated MCP range models used for bundle memory capture."""

    address: str
    count: int
    offset: int
    name: str | None


@runtime_checkable
class ActionArgsProtocol(Protocol):
    """Validated MCP argument models that expose an action selector.

    The selector is read-only here on purpose: every action model narrows
    ``action`` to its own literal, and a read-only property keeps that narrowing
    assignable to this protocol.
    """

    @property
    def action(self) -> str: ...


ToolArguments: TypeAlias = StructuredPayload


ToolResult: TypeAlias = OperationResult[object]


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
class ToolContext:
    """Invocation context handed to a tool handler.

    Session-scoped handlers read ``session``; registry-scoped handlers read
    ``registry``. Batch execution builds a context with ``session`` only.
    """

    registry: SessionRegistry | None = None
    session: SessionService | None = None


ToolArgsT = TypeVar("ToolArgsT", bound=BaseModel)


@dataclass(frozen=True)
class ToolSpec(Generic[ToolArgsT]):
    """Declarative definition for one MCP tool, kept with its request model."""

    model: type[ToolArgsT]
    handler: Callable[[ToolContext, ToolArgsT], ToolResult]
    scope: ToolScope


@dataclass(frozen=True)
class RegisteredToolSpec:
    """Type-erased spec held by the tool registry and dispatcher."""

    model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], ToolResult]
    scope: ToolScope

    def validate(self, arguments: object) -> BaseModel:
        """Validate raw tool arguments against this spec's request model."""

        return self.model.model_validate(arguments)

    def invoke(self, context: ToolContext, args: BaseModel) -> ToolResult:
        """Invoke the handler with already-validated arguments."""

        return self.handler(context, args)


def _register(spec: ToolSpec[ToolArgsT]) -> RegisteredToolSpec:
    def invoke(context: ToolContext, args: BaseModel) -> ToolResult:
        if not isinstance(args, spec.model):
            raise TypeError(f"{spec.model.__name__} spec invoked with {type(args).__name__}")
        return spec.handler(context, args)

    return RegisteredToolSpec(model=spec.model, handler=invoke, scope=spec.scope)


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
) -> RegisteredToolSpec:
    """Wrap a ``(session, args)`` handler as a session-scoped tool spec."""

    def invoke(context: ToolContext, args: ToolArgsT) -> ToolResult:
        session = context.session
        if session is None:
            raise RuntimeError("session-scoped tool invoked without a session")
        return handler(session, args)

    return _register(ToolSpec(model=model, handler=invoke, scope="session"))


def registry_tool_spec(
    model: type[ToolArgsT],
    handler: Callable[[ToolArgsT, SessionRegistry], ToolResult],
) -> RegisteredToolSpec:
    """Wrap an ``(args, registry)`` handler as a registry-scoped tool spec."""

    def invoke(context: ToolContext, args: ToolArgsT) -> ToolResult:
        registry = context.registry
        if registry is None:
            raise RuntimeError("registry-scoped tool invoked without a registry")
        return handler(args, registry)

    return _register(ToolSpec(model=model, handler=invoke, scope="registry"))


def context_tool_spec(
    model: type[ToolArgsT],
    handler: Callable[[ToolContext, ToolArgsT], ToolResult],
) -> RegisteredToolSpec:
    """Register a handler that needs the full context (for example action-polymorphic tools)."""

    return _register(ToolSpec(model=model, handler=handler, scope="registry"))


def _normalize_arguments(arguments: object) -> ToolArguments:
    """Normalize tool arguments into a dictionary for Pydantic validation."""

    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object")
    return cast(ToolArguments, arguments)


def _unwrap_action_args(args: BaseModel) -> BaseModel:
    """Return the discriminated action payload for root-model tool schemas."""

    if isinstance(args, RootModel):
        return cast(BaseModel, args.root)
    return args


def _unsupported_action(scope: str, action_args: BaseModel) -> OperationError:
    """Build the standard validation error for an unhandled action payload."""

    return OperationError(
        message=f"Unsupported {scope}: {type(action_args).__name__}",
        code="validation_error",
    )


def _wrap_action_result(action: str, result: ToolResult) -> ToolResult:
    """Wrap a tool result in the v2 action envelope."""

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


def _wrap_action_result_for(validated_args: BaseModel, result: ToolResult) -> ToolResult:
    """Wrap one raw tool result in the action envelope when the request has an action."""

    action_args = _unwrap_action_args(validated_args)
    if isinstance(action_args, ActionArgsProtocol):
        return _wrap_action_result(action_args.action, result)
    return result


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

    runtime = getattr(session, "runtime", None)
    details: StructuredPayload = {}
    for name in ("execution_state", "stop_reason", "stop_kind"):
        value = getattr(runtime, name, None)
        if isinstance(value, str):
            details[name] = value
    exit_code = getattr(runtime, "exit_code", None)
    if isinstance(exit_code, int):
        details["exit_code"] = exit_code
    return details


def _annotate_session_failure(
    result: ToolResult, session: SessionService, *, session_id: int
) -> ToolResult:
    """Attach session state and a specific code to one session-scoped failure.

    A failure whose message is only ``The program is not being run.`` leaves the
    caller guessing at the session's state, so carry that state in the envelope
    and classify the message into a code the caller can branch on.
    """

    if not isinstance(result, OperationError):
        return result

    details = dict(result.details)
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
                address=str(memory_range.address),
                count=int(memory_range.count),
                offset=int(memory_range.offset),
                name=str(memory_range.name) if memory_range.name is not None else None,
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
