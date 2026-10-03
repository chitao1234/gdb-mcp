"""Breakpoint request models and location selectors."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    Field,
    RootModel,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ... import contracts as shared_contracts
from ...contracts import (
    BreakpointAccess,
    BreakpointEvent,
    BreakpointKind,
    BreakpointManageNumberActionName,
)

from .common import (
    SessionId,
    StrictArgsModel,
    _normalize_optional_text,
)
from .selectors import BreakpointSelectorArgs


class BreakpointCodeCreateArgs(StrictArgsModel):
    kind: shared_contracts.BreakpointKindCodeName = Field(
        ...,
        description="Create a code breakpoint",
    )
    location: str = Field(..., description="Function, file:line, or *address")
    condition: str | None = Field(None, description="Optional breakpoint condition")
    temporary: bool = Field(False, description="Whether the breakpoint is temporary")

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="location")
        if normalized is None:
            raise ValueError("location is required")
        return normalized


class BreakpointWatchCreateArgs(StrictArgsModel):
    kind: shared_contracts.BreakpointKindWatchName = Field(
        ...,
        description="Create a watchpoint",
    )
    expression: str = Field(..., description="Expression to watch")
    access: BreakpointAccess = Field(
        "write",
        description="Whether to stop on writes only, reads only, or any access",
    )

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="expression")
        if normalized is None:
            raise ValueError("expression is required")
        return normalized


class BreakpointCatchCreateArgs(StrictArgsModel):
    kind: shared_contracts.BreakpointKindCatchName = Field(
        ...,
        description="Create a catchpoint",
    )
    event: BreakpointEvent = Field(..., description="Debugger event kind to catch")
    argument: str | None = Field(
        None,
        description="Optional event filter such as a syscall name or signal name.",
    )
    temporary: bool = Field(False, description="Use a temporary catchpoint")

    @field_validator("argument")
    @classmethod
    def validate_argument(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="argument")


BreakpointCreateArgs = Annotated[
    BreakpointCodeCreateArgs | BreakpointWatchCreateArgs | BreakpointCatchCreateArgs,
    Field(discriminator="kind"),
]


class BreakpointUpdateChangesArgs(StrictArgsModel):
    condition: str | None = Field(None, description="New condition to set on the breakpoint")
    clear_condition: bool = Field(False, description="Remove the existing breakpoint condition")

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="condition")

    @model_validator(mode="after")
    def validate_requested_change(self) -> "BreakpointUpdateChangesArgs":
        if self.condition is None and self.clear_condition is False:
            raise ValueError("At least one breakpoint change is required")
        if self.condition is not None and self.clear_condition:
            raise ValueError("condition and clear_condition are mutually exclusive")
        return self


class BreakpointListQueryArgs(StrictArgsModel):
    kinds: list[BreakpointKind] = Field(
        default_factory=list,
        description="Optional breakpoint kinds to include",
    )
    enabled: bool | None = Field(None, description="Optional enabled-state filter")


class BreakpointGetQueryArgs(StrictArgsModel):
    number: int = Field(..., gt=0, description="Breakpoint number")


class BreakpointManageCreateAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionCreateName = Field(
        ...,
        description="Create a breakpoint/watchpoint/catchpoint",
    )
    breakpoint: BreakpointCreateArgs


class BreakpointManageUpdateAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionUpdateName = Field(
        ...,
        description="Update one existing breakpoint",
    )
    breakpoint: BreakpointSelectorArgs
    changes: BreakpointUpdateChangesArgs


class BreakpointManageNumberAction(StrictArgsModel):
    session_id: SessionId
    action: BreakpointManageNumberActionName = Field(
        ...,
        description="Mutate one existing breakpoint",
    )
    breakpoint: BreakpointSelectorArgs


class BreakpointManageArgs(
    RootModel[
        Annotated[
            BreakpointManageCreateAction
            | BreakpointManageUpdateAction
            | BreakpointManageNumberAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for breakpoint mutations."""


class BreakpointQueryListAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionListName = Field(..., description="List all breakpoints")
    query: BreakpointListQueryArgs = Field(
        default_factory=lambda: BreakpointListQueryArgs.model_validate({})
    )


class BreakpointQueryGetAction(StrictArgsModel):
    session_id: SessionId
    action: shared_contracts.ActionGetName = Field(..., description="Fetch one breakpoint")
    query: BreakpointGetQueryArgs


class BreakpointQueryArgs(
    RootModel[
        Annotated[
            BreakpointQueryListAction | BreakpointQueryGetAction,
            Field(discriminator="action"),
        ]
    ]
):
    """Public v2 request model for breakpoint queries."""


class LocationCurrentArgs(StrictArgsModel):
    kind: shared_contracts.LocationKindCurrentName = Field(
        ...,
        description="Use the current selected location",
    )


class LocationFunctionArgs(StrictArgsModel):
    kind: shared_contracts.LocationKindFunctionName = Field(..., description="Resolve one function")
    function: str = Field(..., description="Function name selector")

    @field_validator("function")
    @classmethod
    def validate_function(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="function")
        if normalized is None:
            raise ValueError("function is required")
        return normalized


class LocationAddressArgs(StrictArgsModel):
    kind: shared_contracts.LocationKindAddressName = Field(..., description="Resolve one address")
    address: str = Field(..., description="Address selector")

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="address")
        if normalized is None:
            raise ValueError("address is required")
        return normalized


class LocationAddressRangeArgs(StrictArgsModel):
    kind: shared_contracts.LocationKindAddressRangeName = Field(
        ...,
        description="Resolve an address range",
    )
    start_address: str = Field(..., description="Start of address range")
    end_address: str = Field(..., description="End of address range")

    @field_validator("start_address", "end_address")
    @classmethod
    def validate_range_address(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "address"
        normalized = _normalize_optional_text(value, field_name=field_name)
        if normalized is None:
            raise ValueError(f"{field_name} is required")
        return normalized


class LocationFileLineArgs(StrictArgsModel):
    kind: shared_contracts.LocationKindFileLineName = Field(
        ...,
        description="Resolve one source file line",
    )
    file: str = Field(..., description="Source file selector")
    line: int = Field(..., gt=0, description="Source line selector")

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="file")
        if normalized is None:
            raise ValueError("file is required")
        return normalized


class LocationFileRangeArgs(StrictArgsModel):
    kind: shared_contracts.LocationKindFileRangeName = Field(
        ...,
        description="Resolve one explicit source file range",
    )
    file: str = Field(..., description="Source file selector")
    start_line: int = Field(..., gt=0, description="Start line of the range")
    end_line: int = Field(..., gt=0, description="End line of the range")

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        normalized = _normalize_optional_text(value, field_name="file")
        if normalized is None:
            raise ValueError("file is required")
        return normalized

    @model_validator(mode="after")
    def validate_range(self) -> "LocationFileRangeArgs":
        if self.start_line > self.end_line:
            raise ValueError("start_line must be <= end_line")
        return self


LocationSelectorArgs = Annotated[
    LocationCurrentArgs
    | LocationFunctionArgs
    | LocationAddressArgs
    | LocationAddressRangeArgs
    | LocationFileLineArgs
    | LocationFileRangeArgs,
    Field(discriminator="kind"),
]
