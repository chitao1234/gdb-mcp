"""Published output schemas for the tool surface.

Every success uses the same envelope: ``status`` and a typed ``result`` payload,
with an ``action`` discriminator on action-based tools. The payload schema is
generated from the type the session layer returns, so it cannot drift from the
code that produces it, and then normalized like a request schema.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ...contracts import (
    ACTION_BACKTRACE,
    ACTION_CONTINUE,
    ACTION_CREATE,
    ACTION_CURRENT,
    ACTION_DELETE,
    ACTION_DISABLE,
    ACTION_DISASSEMBLY,
    ACTION_ENABLE,
    ACTION_EVALUATE,
    ACTION_FINISH,
    ACTION_FRAME,
    ACTION_GET,
    ACTION_INTERRUPT,
    ACTION_LIST,
    ACTION_MEMORY,
    ACTION_NEXT,
    ACTION_REGISTERS,
    ACTION_REMOVE,
    ACTION_RUN,
    ACTION_SELECT,
    ACTION_SELECT_FRAME,
    ACTION_SELECT_THREAD,
    ACTION_SET_DETACH_ON_FORK,
    ACTION_SET_FOLLOW_FORK_MODE,
    ACTION_SOURCE,
    ACTION_STATUS,
    ACTION_STEP,
    ACTION_STOP,
    ACTION_THREADS,
    ACTION_UPDATE,
    ACTION_VARIABLES,
    ACTION_WAIT_FOR_STOP,
    TOOL_ATTACH_PROCESS,
    TOOL_BREAKPOINT_MANAGE,
    TOOL_BREAKPOINT_QUERY,
    TOOL_CALL_FUNCTION,
    TOOL_CAPTURE_BUNDLE,
    TOOL_CONTEXT_MANAGE,
    TOOL_CONTEXT_QUERY,
    TOOL_EXECUTE_COMMAND,
    TOOL_EXECUTION_MANAGE,
    TOOL_INFERIOR_MANAGE,
    TOOL_INFERIOR_QUERY,
    TOOL_INSPECT_QUERY,
    TOOL_RUN_UNTIL_FAILURE,
    TOOL_SESSION_CONNECT,
    TOOL_SESSION_MANAGE,
    TOOL_SESSION_QUERY,
    TOOL_SESSION_START,
    TOOL_WORKFLOW_BATCH,
)
from ...domain import (
    BacktraceInfo,
    BatchExecutionInfo,
    BreakpointInfo,
    BreakpointListInfo,
    CaptureBundleInfo,
    CommandExecutionInfo,
    DetachOnForkInfo,
    DisassemblyInfo,
    ExpressionValueInfo,
    FinishInfo,
    FollowForkModeInfo,
    FrameInfo,
    FrameSelectionInfo,
    FunctionCallInfo,
    InferiorAddInfo,
    InferiorCurrentInfo,
    InferiorListInfo,
    InferiorRemoveInfo,
    InferiorSelectionInfo,
    MemoryReadInfo,
    MessageResult,
    RegistersInfo,
    RunUntilFailureInfo,
    SessionListInfo,
    SessionMessage,
    SessionStartInfo,
    SessionStatusSnapshot,
    SourceContextInfo,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
    WaitForStopInfo,
)
from ..schema_normalizer import Schema, freeze_schema, normalize_schema, thaw_schema


class ToolSuccessEnvelope(BaseModel):
    """The fields every successful tool result carries."""

    model_config = ConfigDict(extra="allow")

    status: Literal["success"]
    warnings: list[str] = Field(default_factory=list)


class ToolErrorEnvelope(BaseModel):
    """The fields every failed tool result carries."""

    model_config = ConfigDict(extra="allow")

    status: Literal["error"]
    code: str
    message: str
    fatal: bool | None = None
    details: dict[str, object] | None = None
    action: str | None = None
    tool: str | None = None


#: The payload type of each success shape, keyed by the action that produces it,
#: or ``None`` for the tools that are not action-based. These are the types the
#: session layer returns, so the published schema follows the code.
TOOL_OUTPUT_PAYLOADS: dict[str, tuple[tuple[str | None, type[object]], ...]] = {
    TOOL_SESSION_START: ((None, SessionStartInfo),),
    TOOL_SESSION_CONNECT: ((None, SessionStartInfo),),
    TOOL_SESSION_QUERY: (
        (ACTION_LIST, SessionListInfo),
        (ACTION_STATUS, SessionStatusSnapshot),
    ),
    TOOL_SESSION_MANAGE: ((ACTION_STOP, SessionMessage),),
    TOOL_INFERIOR_QUERY: (
        (ACTION_LIST, InferiorListInfo),
        (ACTION_CURRENT, InferiorCurrentInfo),
    ),
    TOOL_INFERIOR_MANAGE: (
        (ACTION_CREATE, InferiorAddInfo),
        (ACTION_REMOVE, InferiorRemoveInfo),
        (ACTION_SELECT, InferiorSelectionInfo),
        (ACTION_SET_FOLLOW_FORK_MODE, FollowForkModeInfo),
        (ACTION_SET_DETACH_ON_FORK, DetachOnForkInfo),
    ),
    TOOL_EXECUTION_MANAGE: (
        (ACTION_RUN, CommandExecutionInfo),
        (ACTION_CONTINUE, CommandExecutionInfo),
        (ACTION_INTERRUPT, MessageResult),
        (ACTION_STEP, CommandExecutionInfo),
        (ACTION_NEXT, CommandExecutionInfo),
        (ACTION_FINISH, FinishInfo),
        (ACTION_WAIT_FOR_STOP, WaitForStopInfo),
    ),
    TOOL_BREAKPOINT_QUERY: (
        (ACTION_LIST, BreakpointListInfo),
        (ACTION_GET, BreakpointInfo),
    ),
    TOOL_BREAKPOINT_MANAGE: (
        (ACTION_CREATE, BreakpointInfo),
        (ACTION_UPDATE, BreakpointInfo),
        (ACTION_DELETE, SessionMessage),
        (ACTION_ENABLE, SessionMessage),
        (ACTION_DISABLE, SessionMessage),
    ),
    TOOL_CONTEXT_QUERY: (
        (ACTION_THREADS, ThreadListInfo),
        (ACTION_BACKTRACE, BacktraceInfo),
        (ACTION_FRAME, FrameInfo),
    ),
    TOOL_CONTEXT_MANAGE: (
        (ACTION_SELECT_THREAD, ThreadSelectionInfo),
        (ACTION_SELECT_FRAME, FrameSelectionInfo),
    ),
    TOOL_INSPECT_QUERY: (
        (ACTION_EVALUATE, ExpressionValueInfo),
        (ACTION_VARIABLES, VariablesInfo),
        (ACTION_REGISTERS, RegistersInfo),
        (ACTION_MEMORY, MemoryReadInfo),
        (ACTION_DISASSEMBLY, DisassemblyInfo),
        (ACTION_SOURCE, SourceContextInfo),
    ),
    TOOL_WORKFLOW_BATCH: ((None, BatchExecutionInfo),),
    TOOL_CAPTURE_BUNDLE: ((None, CaptureBundleInfo),),
    TOOL_RUN_UNTIL_FAILURE: ((None, RunUntilFailureInfo),),
    TOOL_EXECUTE_COMMAND: ((None, CommandExecutionInfo),),
    TOOL_ATTACH_PROCESS: ((None, CommandExecutionInfo),),
    TOOL_CALL_FUNCTION: ((None, FunctionCallInfo),),
}


def public_output_schema(tool_name: str) -> Schema:
    """Return a fresh, published JSON Schema for one tool's output."""

    return cast(Schema, thaw_schema(_cached_output_schema(tool_name)))


@functools.cache
def _cached_output_schema(tool_name: str) -> object:
    """Return the immutable schema for one tool, computed once per process."""

    return freeze_schema(build_output_schema(tool_name))


def build_output_schema(tool_name: str) -> Schema:
    """Build the published JSON Schema of one tool's structured output."""

    defs: dict[str, object] = {}
    variants = [
        _success_variant(action, payload, defs)
        for action, payload in TOOL_OUTPUT_PAYLOADS[tool_name]
    ]
    variants.append(_model_schema(ToolErrorEnvelope))

    raw: Schema = {"anyOf": variants}
    if defs:
        raw["$defs"] = defs
    return normalize_schema(raw)


def _success_variant(
    action: str | None,
    payload_type: type[object],
    defs: dict[str, object],
) -> Schema:
    """Build one success alternative: the envelope carrying one payload."""

    payload = TypeAdapter(payload_type).json_schema()
    _merge_defs(defs, payload.pop("$defs", {}))

    properties = _properties(_model_schema(ToolSuccessEnvelope))
    required: list[str] = ["status"]
    if action is not None:
        properties["action"] = {"const": action}
        required.append("action")
    properties["result"] = payload
    required.append("result")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }


def _model_schema(model: type[BaseModel]) -> Schema:
    """Return the generated schema of one envelope model."""

    return model.model_json_schema()


def _merge_defs(target: dict[str, object], source: object) -> None:
    """Collect the definitions one payload schema references.

    Payload schemas are assembled into one document, so their definitions share
    a single root; a name that two payloads define differently would silently
    publish one of them, so it is an error instead.
    """

    if not isinstance(source, Mapping):
        return
    for name, definition in source.items():
        if not isinstance(name, str):
            continue
        existing = target.get(name)
        if existing is None:
            target[name] = definition
        elif json.dumps(existing, sort_keys=True) != json.dumps(definition, sort_keys=True):
            raise ValueError(f"Conflicting schema definitions for {name!r}")


def _properties(schema: Schema) -> dict[str, object]:
    """Return one schema's property map, or an empty one."""

    properties = schema.get("properties")
    return dict(properties) if isinstance(properties, Mapping) else {}
