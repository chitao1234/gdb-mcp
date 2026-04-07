"""Namespace-to-typed-input parsers for the MCP CLI client."""

from __future__ import annotations

import argparse
from typing import cast

from .inputs import (
    ContextManageAction,
    ContextManageInput,
    ContextQueryAction,
    ContextQueryInput,
    ExecutionManageAction,
    ExecutionManageInput,
    ExecutionWaitInput,
    ExecutionWaitUntil,
    InferiorFollowForkMode,
    InferiorManageAction,
    InferiorManageInput,
    InferiorQueryAction,
    InferiorQueryInput,
    SessionQueryAction,
    SessionQueryInput,
    SessionStartInput,
)
from .parsers import collapse_key_value_entries


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
