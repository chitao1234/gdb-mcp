"""Backend-specific validation of debugger startup arguments."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import DEBUGGER_DELVE, DEBUGGER_GDB, DEBUGGER_LLDB

# GDB options that would fight this server's own target wiring or replace the MI
# interpreter it speaks, plus the value-taking options whose operand is allowed
# to be a bare word (for example ``-iex "set X"``).
_GDB_ARG_CONFLICTING_OPTIONS = frozenset(
    {"-i", "--interpreter", "--args", "-c", "--core", "-p", "--pid", "--attach"}
)
_GDB_ARG_CONFLICTING_PREFIXES = ("--interpreter=", "--core=", "--pid=")
_GDB_ARG_CONFLICTING_REASONS = {
    "-i": "would replace the MI interpreter this server speaks",
    "--interpreter": "would replace the MI interpreter this server speaks",
    "--args": "conflicts with the 'program' and 'args' parameters",
    "-c": "conflicts with the 'core' parameter",
    "--core": "conflicts with the 'core' parameter",
    "-p": "conflicts with gdb_attach_process",
    "--pid": "conflicts with gdb_attach_process",
    "--attach": "conflicts with gdb_attach_process",
}
_GDB_ARG_VALUE_OPTIONS = frozenset(
    {
        "-iex",
        "--init-eval-command",
        "-ix",
        "--init-command",
        "-ex",
        "--eval-command",
        "-x",
        "--command",
        "-d",
        "--directory",
        "-cd",
        "--cwd",
        "-s",
        "-symbols",
        "--symbols",
        "-tty",
        "--tty",
    }
)


def debugger_args_problems(values: Sequence[str], debugger: str = DEBUGGER_GDB) -> list[str]:
    """Return one message per ``debugger_args`` entry this server cannot pass through."""

    problems: list[str] = []
    if debugger == DEBUGGER_LLDB:
        return ["debugger_args is not supported by the LLDB SB API backend"] if values else []
    if debugger == DEBUGGER_DELVE:
        reserved = {
            "--headless",
            "--api-version",
            "--listen",
            "-l",
            "--accept-multiclient",
            "--continue",
            "--init",
            "--wd",
            "--",
            "--help",
            "-h",
            "--version",
            "--log-dest",
        }
        value_options = {
            "--backend",
            "--log-output",
            "--redirect",
            "-r",
            "--tty",
        }
        previous = ""
        for index, entry in enumerate(values):
            option = entry.split("=", 1)[0]
            if not entry.strip():
                problems.append(f"debugger_args[{index}] must not be empty")
            elif option in reserved or entry.startswith("-l"):
                problems.append(f"debugger_args[{index}] {entry!r} conflicts with session startup")
            elif not entry.startswith("-") and previous not in value_options:
                problems.append(
                    f"debugger_args[{index}] {entry!r} must be an option or its operand"
                )
            previous = entry
        return problems
    previous = ""
    for index, raw in enumerate(values):
        entry = raw.strip()
        if not entry:
            problems.append(f"debugger_args[{index}] must not be empty")
        elif entry in _GDB_ARG_CONFLICTING_OPTIONS:
            problems.append(
                f"debugger_args[{index}] {entry!r} {_GDB_ARG_CONFLICTING_REASONS[entry]}"
            )
        elif entry.startswith(_GDB_ARG_CONFLICTING_PREFIXES):
            option = entry.split("=", 1)[0]
            problems.append(
                f"debugger_args[{index}] {entry!r} {_GDB_ARG_CONFLICTING_REASONS[option]}"
            )
        elif not entry.startswith("-") and previous not in _GDB_ARG_VALUE_OPTIONS:
            problems.append(
                f"debugger_args[{index}] {entry!r} is a bare argument: pass it as the operand of "
                "a preceding option such as '-iex' or '-d', or use 'program'/'args'"
            )
        previous = entry
    return problems
