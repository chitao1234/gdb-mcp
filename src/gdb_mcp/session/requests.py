"""Normalized requests carried between session collaborators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, TypeAlias

from .constants import DEFAULT_TIMEOUT_SEC


@dataclass(frozen=True, slots=True)
class ExecutionOptions:
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    wait_for_stop: bool = True


DEFAULT_EXECUTION_OPTIONS = ExecutionOptions()


@dataclass(frozen=True, slots=True)
class CurrentLocation:
    kind: ClassVar[Literal["current"]] = "current"


@dataclass(frozen=True, slots=True)
class FunctionLocation:
    function: str
    kind: ClassVar[Literal["function"]] = "function"


@dataclass(frozen=True, slots=True)
class AddressLocation:
    address: str
    kind: ClassVar[Literal["address"]] = "address"


@dataclass(frozen=True, slots=True)
class AddressRangeLocation:
    start_address: str
    end_address: str
    kind: ClassVar[Literal["address_range"]] = "address_range"


@dataclass(frozen=True, slots=True)
class FileLineLocation:
    file: str
    line: int
    kind: ClassVar[Literal["file_line"]] = "file_line"


@dataclass(frozen=True, slots=True)
class FileRangeLocation:
    file: str
    start_line: int
    end_line: int
    kind: ClassVar[Literal["file_range"]] = "file_range"


SourceLocation: TypeAlias = (
    CurrentLocation | FunctionLocation | AddressLocation | FileLineLocation | FileRangeLocation
)
DisassemblyLocation: TypeAlias = SourceLocation | AddressRangeLocation


@dataclass(frozen=True, slots=True)
class DisassemblyRequest:
    location: DisassemblyLocation = CurrentLocation()
    thread_id: int | None = None
    frame: int | None = None
    instruction_count: int = 32
    mode: Literal["assembly", "mixed"] = "mixed"


@dataclass(frozen=True, slots=True)
class SourceContextRequest:
    location: SourceLocation = CurrentLocation()
    thread_id: int | None = None
    frame: int | None = None
    context_before: int = 5
    context_after: int = 5
