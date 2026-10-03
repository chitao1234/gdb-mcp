"""Shared handler protocols, envelopes, and payload helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias, TypeVar, cast

from pydantic import BaseModel, RootModel

from ... import contracts as shared_contracts
from ...contracts import (
    ExecutionWaitUntil,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_WORKFLOW_BATCH,
)
from ...domain import (
    MemoryCaptureRange,
    OperationError,
    OperationResult,
    OperationSuccess,
    StructuredPayload,
    payload_to_mapping,
)
from ...session.constants import DEFAULT_TIMEOUT_SEC
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


SessionToolArgsT = TypeVar("SessionToolArgsT", bound=BaseModel)


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


@dataclass(frozen=True)
class SessionToolSpec:
    """Declarative definition for one session-scoped MCP tool."""

    model: type[BaseModel]
    handler: Callable[[SessionService, BaseModel], ToolResult]


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
    model: type[SessionToolArgsT],
    handler: Callable[[SessionService, SessionToolArgsT], ToolResult],
) -> SessionToolSpec:
    """Wrap a typed handler for storage in the session tool registry."""

    def invoke(session: SessionService, args: BaseModel) -> ToolResult:
        return handler(session, cast(SessionToolArgsT, args))

    return SessionToolSpec(model=model, handler=invoke)


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


def _wrap_action_result(action: str, result: ToolResult) -> ToolResult:
    """Wrap a tool result in the v2 action envelope."""

    if isinstance(result, OperationError):
        details_payload = payload_to_mapping(result.details)
        details: StructuredPayload = (
            dict(details_payload) if isinstance(details_payload, dict) else {}
        )
        details.setdefault("action", action)
        return OperationError(
            message=result.message,
            code=result.code,
            fatal=result.fatal,
            details=details,
        )

    return OperationSuccess(
        {
            "action": action,
            "result": payload_to_mapping(result.value),
        },
        warnings=result.warnings,
    )


def _wrap_action_result_for(validated_args: BaseModel, result: ToolResult) -> ToolResult:
    """Wrap one raw tool result in the action envelope when the request has an action."""

    action = getattr(_unwrap_action_args(validated_args), "action", None)
    if not isinstance(action, str):
        return result
    return _wrap_action_result(action, result)


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
