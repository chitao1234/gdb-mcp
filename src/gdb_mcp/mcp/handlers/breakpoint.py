"""Breakpoint query and mutation handlers."""

from __future__ import annotations


from ... import contracts as shared_contracts
from ...domain import (
    OperationError,
    OperationSuccess,
)
from ...session.service import SessionService
from ..schemas import (
    BreakpointCatchCreateArgs,
    BreakpointManageArgs,
    BreakpointManageCreateAction,
    BreakpointManageNumberAction,
    BreakpointManageUpdateAction,
    BreakpointCodeCreateArgs,
    BreakpointQueryArgs,
    BreakpointQueryGetAction,
    BreakpointQueryListAction,
    BreakpointWatchCreateArgs,
)
from .common import (
    ToolResult,
    _unwrap_action_args,
)


def _handle_breakpoint_query(session: SessionService, args: BreakpointQueryArgs) -> ToolResult:
    """Route v2 breakpoint query actions to the breakpoint service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, BreakpointQueryListAction):
        result = session.list_breakpoints()
        if isinstance(result, OperationError):
            return result

        list_query = action_args.query
        kinds = set(list_query.kinds)
        enabled_filter = list_query.enabled
        if not kinds and enabled_filter is None:
            return result

        filtered_breakpoints = []
        for breakpoint_info in result.value.breakpoints:
            breakpoint_type = str(breakpoint_info.get("type", "")).lower()
            breakpoint_kind = "code"
            if "watch" in breakpoint_type:
                breakpoint_kind = "watch"
            elif "catch" in breakpoint_type:
                breakpoint_kind = "catch"

            if kinds and breakpoint_kind not in kinds:
                continue

            enabled_value = breakpoint_info.get("enabled")
            is_enabled = enabled_value in {True, "y", "Y", "1", 1}
            if enabled_filter is not None and is_enabled != enabled_filter:
                continue

            filtered_breakpoints.append(breakpoint_info)

        return OperationSuccess(
            {
                "breakpoints": filtered_breakpoints,
                "count": len(filtered_breakpoints),
            }
        )

    if isinstance(action_args, BreakpointQueryGetAction):
        get_query = action_args.query
        return session.get_breakpoint(get_query.number)

    return OperationError(
        message=f"Unsupported breakpoint query action: {type(action_args).__name__}",
        code="validation_error",
    )


def _handle_breakpoint_manage(session: SessionService, args: BreakpointManageArgs) -> ToolResult:
    """Route v2 breakpoint mutation actions to the breakpoint service."""

    action_args = _unwrap_action_args(args)
    if isinstance(action_args, BreakpointManageCreateAction):
        payload = action_args.breakpoint
        if isinstance(payload, BreakpointCodeCreateArgs):
            return session.set_breakpoint(
                location=payload.location,
                condition=payload.condition,
                temporary=payload.temporary,
            )
        if isinstance(payload, BreakpointWatchCreateArgs):
            return session.set_watchpoint(
                expression=payload.expression,
                access=payload.access,
            )
        if isinstance(payload, BreakpointCatchCreateArgs):
            return session.set_catchpoint(
                payload.event,
                argument=payload.argument,
                temporary=payload.temporary,
            )
        return OperationError(
            message=f"Unsupported breakpoint create payload: {type(payload).__name__}",
            code="validation_error",
        )

    if isinstance(action_args, BreakpointManageUpdateAction):
        selector = action_args.breakpoint
        changes = action_args.changes
        return session.update_breakpoint(
            selector.number,
            condition=changes.condition,
            clear_condition=changes.clear_condition,
        )

    if not isinstance(action_args, BreakpointManageNumberAction):
        return OperationError(
            message=f"Unsupported breakpoint manage action: {type(action_args).__name__}",
            code="validation_error",
        )

    number = action_args.breakpoint.number
    if action_args.action == shared_contracts.ACTION_DELETE:
        return session.delete_breakpoint(number=number)
    if action_args.action == shared_contracts.ACTION_ENABLE:
        return session.enable_breakpoint(number=number)
    if action_args.action == shared_contracts.ACTION_DISABLE:
        return session.disable_breakpoint(number=number)

    return OperationError(
        message=f"Unsupported breakpoint manage action: {type(action_args).__name__}",
        code="validation_error",
    )
