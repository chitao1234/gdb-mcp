"""Canonical stop kinds, derived from whatever reason a debugger reports.

Debuggers name the same event differently: GDB/MI says ``breakpoint-hit``,
``end-stepping-range`` or ``signal-received`` while DAP says ``breakpoint``,
``step`` or ``exception``. Callers that only want "why are we stopped" should not
have to know that, so every payload keeps the debugger's own ``stop_reason`` and
adds this canonical ``stop_kind`` next to it.

DAP reports a data breakpoint as ``breakpoint`` and only distinguishes it in the
event description, so a backend that cares about that distinction refines the
kind itself before publishing it.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, get_args

StopKind: TypeAlias = Literal[
    "breakpoint",
    "watchpoint",
    "step",
    "signal",
    "exception",
    "exit",
    "entry",
    "pause",
    "fork",
    "vfork",
    "exec",
    "thread_exit",
    "unknown",
]

STOP_KINDS: tuple[StopKind, ...] = get_args(StopKind)

# Checked in order, most specific first: `data breakpoint` has to win over
# `breakpoint`, and `watchpoint-trigger` over `trigger`.
_RULES: tuple[tuple[str, StopKind], ...] = (
    ("watchpoint", "watchpoint"),
    ("data breakpoint", "watchpoint"),
    ("breakpoint", "breakpoint"),
    ("stepping", "step"),
    ("function-finished", "step"),
    ("location-reached", "step"),
    ("step", "step"),
    ("thread-exit", "thread_exit"),
    ("vfork", "vfork"),
    ("fork", "fork"),
    ("exec", "exec"),
    # `exited-signalled` is a process that died from a signal, which is an exit
    # rather than the `signal-received` stop where the inferior is still alive.
    ("exit", "exit"),
    ("signal", "signal"),
    ("exception", "exception"),
    ("panic", "exception"),
    ("entry", "entry"),
    ("pause", "pause"),
    ("interrupt", "pause"),
)


def stop_kind_from_reason(reason: str | None) -> StopKind:
    """Map one debugger stop reason onto the canonical kind."""

    if not reason:
        return "unknown"
    lowered = reason.strip().lower()
    for needle, kind in _RULES:
        if needle in lowered:
            return kind
    return "unknown"
