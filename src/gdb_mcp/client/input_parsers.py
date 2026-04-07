"""Namespace-to-typed-input parsers for the MCP CLI client."""

from __future__ import annotations

import argparse
from typing import cast

from .inputs import (
    BreakpointAccess,
    BreakpointCreateInput,
    BreakpointEvent,
    BreakpointKind,
    BreakpointManageAction,
    BreakpointManageInput,
    BreakpointQueryAction,
    BreakpointQueryInput,
    ContextManageAction,
    ContextManageInput,
    ContextQueryAction,
    ContextQueryInput,
    DisassemblyMode,
    ExecutionManageAction,
    ExecutionManageInput,
    ExecutionWaitInput,
    ExecutionWaitUntil,
    InferiorFollowForkMode,
    InferiorManageAction,
    InferiorManageInput,
    InferiorQueryAction,
    InferiorQueryInput,
    InspectQueryAction,
    InspectQueryInput,
    LocationInput,
    LocationKind,
    RegisterValueFormat,
    SessionQueryAction,
    SessionQueryInput,
    SessionStartInput,
)
from .parsers import collapse_key_value_entries

_LOCATION_FIELD_NAMES = (
    "location_kind",
    "function",
    "address",
    "start_address",
    "end_address",
    "file",
    "line",
    "start_line",
    "end_line",
)


def parse_session_start_input(namespace: argparse.Namespace) -> SessionStartInput:
    """Parse one session-start namespace into a typed input object."""

    return SessionStartInput(
        program=namespace.program,
        args=tuple(namespace.args),
        init_commands=tuple(namespace.init_commands),
        env=collapse_key_value_entries(namespace.env),
        core=namespace.core,
        gdb_path=namespace.gdb_path,
        working_dir=namespace.working_dir,
    )


def parse_session_query_input(namespace: argparse.Namespace) -> SessionQueryInput:
    """Parse one session-query namespace into a typed input object."""

    return SessionQueryInput(
        action=cast(SessionQueryAction, namespace.action),
        session_id=namespace.__dict__.get("session_id"),
    )


def parse_inferior_query_input(namespace: argparse.Namespace) -> InferiorQueryInput:
    """Parse one inferior-query namespace into a typed input object."""

    return InferiorQueryInput(
        action=cast(InferiorQueryAction, namespace.action),
        session_id=namespace.session_id,
    )


def parse_inferior_manage_input(namespace: argparse.Namespace) -> InferiorManageInput:
    """Parse one inferior-manage namespace into a typed input object."""

    return InferiorManageInput(
        action=cast(InferiorManageAction, namespace.action),
        session_id=namespace.session_id,
        executable=namespace.__dict__.get("executable"),
        make_current=namespace.__dict__.get("make_current"),
        inferior_id=namespace.__dict__.get("inferior_id"),
        mode=cast(InferiorFollowForkMode | None, namespace.__dict__.get("mode")),
        enabled=namespace.__dict__.get("enabled"),
    )


def parse_execution_manage_input(namespace: argparse.Namespace) -> ExecutionManageInput:
    """Parse one execution-manage namespace into a typed input object."""

    wait_until = cast(ExecutionWaitUntil | None, namespace.__dict__.get("wait_until"))
    wait_timeout_sec = namespace.__dict__.get("wait_timeout_sec")
    wait = (
        ExecutionWaitInput(until=wait_until, timeout_sec=wait_timeout_sec)
        if wait_until is not None or wait_timeout_sec is not None
        else None
    )
    return ExecutionManageInput(
        action=cast(ExecutionManageAction, namespace.action),
        session_id=namespace.session_id,
        args=tuple(namespace.__dict__.get("args", ())),
        wait=wait,
        timeout_sec=namespace.__dict__.get("timeout_sec"),
        stop_reasons=tuple(namespace.__dict__.get("stop_reasons", ())),
    )


def parse_context_query_input(namespace: argparse.Namespace) -> ContextQueryInput:
    """Parse one context-query namespace into a typed input object."""

    return ContextQueryInput(
        action=cast(ContextQueryAction, namespace.action),
        session_id=namespace.session_id,
        thread_id=namespace.__dict__.get("thread_id"),
        frame=namespace.__dict__.get("frame"),
        max_frames=namespace.__dict__.get("max_frames"),
    )


def parse_context_manage_input(namespace: argparse.Namespace) -> ContextManageInput:
    """Parse one context-manage namespace into a typed input object."""

    return ContextManageInput(
        action=cast(ContextManageAction, namespace.action),
        session_id=namespace.session_id,
        thread_id=namespace.__dict__.get("thread_id"),
        frame=namespace.__dict__.get("frame"),
    )


def _parse_location_input(namespace: argparse.Namespace) -> LocationInput | None:
    location_kind = namespace.__dict__.get("location_kind")
    if location_kind is None:
        return None

    return LocationInput(
        kind=cast(LocationKind, location_kind.replace("-", "_")),
        function=namespace.__dict__.get("function"),
        address=namespace.__dict__.get("address"),
        start_address=namespace.__dict__.get("start_address"),
        end_address=namespace.__dict__.get("end_address"),
        file=namespace.__dict__.get("file"),
        line=namespace.__dict__.get("line"),
        start_line=namespace.__dict__.get("start_line"),
        end_line=namespace.__dict__.get("end_line"),
    )


def parse_breakpoint_query_input(namespace: argparse.Namespace) -> BreakpointQueryInput:
    """Parse one breakpoint-query namespace into a typed input object."""

    return BreakpointQueryInput(
        action=cast(BreakpointQueryAction, namespace.action),
        session_id=namespace.session_id,
        number=namespace.__dict__.get("number"),
        kinds=tuple(cast(tuple[BreakpointKind, ...], tuple(namespace.__dict__.get("kinds", ())))),
        enabled=namespace.__dict__.get("enabled"),
    )


def parse_breakpoint_manage_input(namespace: argparse.Namespace) -> BreakpointManageInput:
    """Parse one breakpoint-manage namespace into a typed input object."""

    namespace_fields = namespace.__dict__
    breakpoint_kind = cast(BreakpointKind | None, namespace.__dict__.get("breakpoint_kind"))
    temporary_explicit = "temporary" in namespace_fields
    has_breakpoint_fields = (
        breakpoint_kind is not None
        or namespace_fields.get("location") is not None
        or namespace_fields.get("expression") is not None
        or namespace_fields.get("access") is not None
        or namespace_fields.get("event") is not None
        or namespace_fields.get("argument") is not None
        or temporary_explicit
    )
    breakpoint = (
        BreakpointCreateInput(
            kind=breakpoint_kind or "code",
            location=namespace_fields.get("location"),
            expression=namespace_fields.get("expression"),
            access=cast(BreakpointAccess | None, namespace_fields.get("access")),
            event=cast(BreakpointEvent | None, namespace_fields.get("event")),
            argument=namespace_fields.get("argument"),
            condition=namespace_fields.get("condition"),
            temporary=bool(namespace_fields.get("temporary", False)),
            kind_explicit=breakpoint_kind is not None,
            temporary_explicit=temporary_explicit,
        )
        if namespace.action == "create" or has_breakpoint_fields
        else None
    )

    return BreakpointManageInput(
        action=cast(BreakpointManageAction, namespace.action),
        session_id=namespace.session_id,
        breakpoint=breakpoint,
        number=namespace_fields.get("number"),
        condition=namespace_fields.get("condition"),
        clear_condition=namespace_fields.get("clear_condition"),
    )


def parse_inspect_query_input(namespace: argparse.Namespace) -> InspectQueryInput:
    """Parse one inspect-query namespace into a typed input object."""

    namespace_fields = namespace.__dict__
    action = cast(InspectQueryAction, namespace.action)
    location_fields = tuple(
        field_name
        for field_name in _LOCATION_FIELD_NAMES
        if field_name in namespace_fields and not (field_name == "address" and action == "memory")
    )
    return InspectQueryInput(
        action=action,
        session_id=namespace.session_id,
        thread_id=namespace_fields.get("thread_id"),
        frame=namespace_fields.get("frame"),
        expression=namespace_fields.get("expression"),
        register_numbers=tuple(namespace_fields.get("register_numbers", ())),
        register_names=tuple(namespace_fields.get("register_names", ())),
        include_vector_registers=namespace_fields.get("include_vector_registers"),
        max_registers=namespace_fields.get("max_registers"),
        value_format=cast(RegisterValueFormat | None, namespace_fields.get("value_format")),
        memory_address=namespace_fields.get("address") if action == "memory" else None,
        count=namespace_fields.get("count"),
        offset=namespace_fields.get("offset"),
        location=_parse_location_input(namespace),
        instruction_count=namespace_fields.get("instruction_count"),
        mode=cast(DisassemblyMode | None, namespace_fields.get("mode")),
        context_before=namespace_fields.get("context_before"),
        context_after=namespace_fields.get("context_after"),
        location_fields=location_fields,
    )
