"""Inspection request models (evaluate/registers/memory/disassembly/source)."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    Field,
    RootModel,
    field_validator,
)

from ... import contracts as shared_contracts
from ...contracts import (
    DisassemblyMode,
    RegisterValueFormat,
)

from .common import (
    SessionId,
    StrictArgsModel,
    _coerce_int_like,
    _normalize_optional_text,
)
from .selectors import ThreadFrameContextArgs
from .breakpoint import LocationSelectorArgs


class InspectEvaluateQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(
        None, description="Optional thread/frame override"
    )
    expression: str = Field(..., description="Expression to evaluate")

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="expression")
        if normalized is None:
            raise ValueError("expression is required")
        return normalized


class InspectVariablesQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(
        None, description="Optional thread/frame override"
    )


class InspectRegistersQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(
        None, description="Optional thread/frame override"
    )
    register_numbers: list[int | str] = Field(
        default_factory=list, description="Optional register-number selectors"
    )
    register_names: list[str] = Field(
        default_factory=list, description="Optional register-name selectors"
    )
    include_vector_registers: bool = Field(
        True, description="Whether to include vector/SIMD registers"
    )
    max_registers: int | None = Field(None, gt=0, description="Optional maximum register count")
    value_format: RegisterValueFormat = Field("hex", description="Value rendering mode")

    @field_validator("register_numbers")
    @classmethod
    def validate_register_numbers(cls, value: list[int | str]) -> list[int]:
        normalized: list[int] = []
        for index, raw_number in enumerate(value):
            normalized_number = _coerce_int_like(
                raw_number,
                field_name=f"register_numbers[{index}]",
                minimum=0,
                allow_none=False,
            )
            if normalized_number is None:
                raise ValueError(f"register_numbers[{index}] is required")
            normalized.append(normalized_number)
        return normalized

    @field_validator("register_names")
    @classmethod
    def validate_register_names(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for index, register_name in enumerate(value):
            text = register_name.strip()
            if not text:
                raise ValueError(f"register_names[{index}] must be a non-empty string")
            normalized.append(text)
        return normalized


class InspectMemoryQueryArgs(StrictArgsModel):
    address: str = Field(..., description="Address expression to read from")
    count: int = Field(..., gt=0, description="Number of addressable memory units to read")
    offset: int = Field(0, ge=0, description="Optional offset relative to address")

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="address")
        if normalized is None:
            raise ValueError("address is required")
        return normalized


class InspectDisassemblyQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(
        None, description="Optional thread/frame override"
    )
    location: LocationSelectorArgs
    instruction_count: int = Field(32, gt=0, description="Upper bound on returned instructions")
    mode: DisassemblyMode = Field(
        "mixed",
        description="Whether to request assembly only or mixed source/assembly output",
    )


class InspectSourceQueryArgs(StrictArgsModel):
    context: ThreadFrameContextArgs | None = Field(
        None, description="Optional thread/frame override"
    )
    location: LocationSelectorArgs
    context_before: int = Field(5, ge=0, description="Lines before the focal location")
    context_after: int = Field(5, ge=0, description="Lines after the focal location")


class InspectEvaluateAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionEvaluateName = Field(..., description="Evaluate one expression")
    query: InspectEvaluateQueryArgs


class InspectVariablesAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionVariablesName = Field(
        ...,
        description="Inspect variables in one context",
    )
    query: InspectVariablesQueryArgs = Field(
        default_factory=lambda: InspectVariablesQueryArgs.model_validate({})
    )


class InspectRegistersAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionRegistersName = Field(
        ...,
        description="Inspect registers in one context",
    )
    query: InspectRegistersQueryArgs = Field(
        default_factory=lambda: InspectRegistersQueryArgs.model_validate({})
    )


class InspectMemoryAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionMemoryName = Field(..., description="Read target memory")
    query: InspectMemoryQueryArgs


class InspectDisassemblyAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionDisassemblyName = Field(
        ...,
        description="Inspect disassembly for one location",
    )
    query: InspectDisassemblyQueryArgs


class InspectSourceAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionSourceName = Field(
        ...,
        description="Inspect source context for one location",
    )
    query: InspectSourceQueryArgs


class InspectQueryArgs(
    RootModel[
        Annotated[
            InspectEvaluateAction
            | InspectVariablesAction
            | InspectRegistersAction
            | InspectMemoryAction
            | InspectDisassemblyAction
            | InspectSourceAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for read-only inspection operations."""
