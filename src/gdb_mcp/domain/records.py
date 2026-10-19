"""Typed record models over debugger payloads.

A record mirrors what the debugger reported, so ``extra="allow"`` keeps every
field a debugger sends, and the declared fields state the types this server
requires: the adapters normalize what GDB/MI spells loosely (``"y"``, ``"3"``) into
those types before a record is built, so :meth:`RecordModel.from_payload` can
validate honestly instead of accepting anything.

Fields are read as attributes (``frame.level``); anything the debugger sent that
is not declared stays available through ``model_extra``. Dumping uses
``by_alias=True, exclude_unset=True`` so a record round-trips to exactly the
payload it was built from.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Self

from .stop_kinds import StopKind


class RecordModel(BaseModel):
    """Base class for one debugger payload record."""

    model_config = ConfigDict(
        extra="allow",
        strict=True,
        validate_by_name=True,
        validate_by_alias=True,
    )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Validate a payload into a record, keeping fields we do not declare."""

        return cls.model_validate(dict(payload))

    def to_payload(self) -> dict[str, object]:
        """Return the payload this record was built from."""

        return self.model_dump(by_alias=True, exclude_unset=True)


class FrameRecord(RecordModel):
    """One stack frame."""

    level: int | None = None
    addr: str | None = None
    func: str | None = None
    file: str | None = None
    fullname: str | None = None


class ThreadRecord(RecordModel):
    """One thread (or, for Go backends, one goroutine)."""

    id: str | None = None
    name: str | None = None
    state: str | None = None
    target_id: str | None = Field(None, alias="target-id")


class BreakpointRecord(RecordModel):
    """One breakpoint, watchpoint, or catchpoint."""

    number: str | None = None
    type: str | None = None
    enabled: bool | None = None
    times: int | None = None
    verified: bool | None = None
    condition: str | None = None
    func: str | None = None
    file: str | None = None
    fullname: str | None = None
    original_location: str | None = Field(None, alias="original-location")


class VariableRecord(RecordModel):
    """One variable, argument, or child of an aggregate."""

    name: str | None = None
    type: str | None = None
    value: str | None = None
    arg: str | None = None
    variables_reference: int | None = Field(None, alias="variablesReference")


class RegisterRecord(RecordModel):
    """One register value."""

    number: int | None = None
    name: str | None = None
    value: str | None = None


class MemoryBlockRecord(RecordModel):
    """One readable stretch of memory."""

    begin: str | None = None
    offset: str | None = None
    end: str | None = None
    contents: str | None = None


class SourceLineRecord(RecordModel):
    """One line of source text."""

    line_number: int | None = None
    text: str | None = None
    is_current: bool | None = None


class DisassemblyInstructionRecord(RecordModel):
    """One disassembled instruction."""

    address: str | None = None
    instruction: str | None = None
    opcodes: str | None = None
    function: str | None = None
    offset: int | None = None
    file: str | None = None
    fullname: str | None = None
    line: int | None = None
    is_current: bool | None = None


class InferiorRecord(RecordModel):
    """One inferior from the inventory."""

    inferior_id: int | None = None
    is_current: bool | None = None
    display: str | None = None
    description: str | None = None
    connection: str | None = None
    executable: str | None = None
    execution_state: str | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None


class InferiorStateRecord(RecordModel):
    """Execution state of one inferior."""

    inferior_id: int | None = None
    is_current: bool | None = None
    execution_state: str | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    exit_code: int | None = None
