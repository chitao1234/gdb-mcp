"""Typed reads from values already parsed by argparse."""

from __future__ import annotations

from typing import Literal, Protocol, TypeAlias, TypeVar, cast, overload

from .parsers import CliUsageError, StepEvent

ChoiceT = TypeVar("ChoiceT", bound=str)

TextOption: TypeAlias = Literal[
    "access",
    "action",
    "address",
    "argument",
    "breakpoint_kind",
    "bundle_name",
    "capture_bundle_name",
    "capture_bundle_name_prefix",
    "capture_output_dir",
    "command",
    "condition",
    "core",
    "debugger",
    "debugger_path",
    "end_address",
    "endpoint",
    "event",
    "executable",
    "expression",
    "failure_result_text_regex",
    "file",
    "function",
    "function_call",
    "location",
    "location_kind",
    "mode",
    "output_dir",
    "program",
    "rr_replay",
    "start_address",
    "startup_core",
    "startup_debugger",
    "startup_debugger_path",
    "startup_program",
    "startup_working_dir",
    "value_format",
    "value",
    "wait_until",
    "working_dir",
]
IntegerOption: TypeAlias = Literal[
    "capture_max_frames",
    "context_after",
    "context_before",
    "count",
    "end_line",
    "frame",
    "inferior_id",
    "instruction_count",
    "line",
    "max_frames",
    "max_iterations",
    "max_registers",
    "number",
    "offset",
    "pid",
    "run_timeout_sec",
    "session_id",
    "start_line",
    "thread_id",
    "timeout_sec",
]
FlagOption: TypeAlias = Literal[
    "include_backtraces",
    "include_frame",
    "include_registers",
    "include_stop_history",
    "include_threads",
    "include_transcript",
    "include_variables",
    "reverse",
]
OptionalFlagOption: TypeAlias = Literal[
    "capture_enabled",
    "capture_include_backtraces",
    "capture_include_frame",
    "capture_include_registers",
    "capture_include_stop_history",
    "capture_include_threads",
    "capture_include_transcript",
    "capture_include_variables",
    "capture_stop_events",
    "clear_condition",
    "enabled",
    "fail_fast",
    "failure_on_error",
    "failure_on_timeout",
    "include_vector_registers",
    "make_current",
    "temporary",
]
TextListOption: TypeAlias = Literal[
    "args",
    "capture_expressions",
    "capture_memory_ranges",
    "debugger_args",
    "expressions",
    "failure_execution_states",
    "failure_stop_reasons",
    "init_commands",
    "kinds",
    "memory_ranges",
    "register_names",
    "run_args",
    "startup_args",
    "startup_init_commands",
    "stop_reasons",
]
IntegerListOption: TypeAlias = Literal["failure_exit_codes", "register_numbers"]
KeyValueOption: TypeAlias = Literal["env", "startup_env"]
EventOption: TypeAlias = Literal["step_events", "setup_step_events"]


class NamespaceLike(Protocol):
    """A parsed namespace carrying its options in __dict__."""

    __dict__: dict[str, object]


class ParsedOptions(Protocol):
    """The field types established by the CLI's argparse configuration."""

    @overload
    def get(self, name: TextOption) -> str | None: ...

    @overload
    def get(self, name: IntegerOption) -> int | None: ...

    @overload
    def get(self, name: FlagOption | OptionalFlagOption) -> bool | None: ...

    @overload
    def get(self, name: TextListOption) -> list[str] | None: ...

    @overload
    def get(self, name: IntegerListOption) -> list[int] | None: ...

    @overload
    def get(self, name: KeyValueOption) -> list[tuple[str, str]] | None: ...

    @overload
    def get(self, name: EventOption) -> list[StepEvent] | None: ...

    @overload
    def get(self, name: str) -> object: ...

    def __getitem__(self, name: FlagOption) -> bool: ...


def snapshot(namespace: NamespaceLike) -> ParsedOptions:
    """Give argparse's untyped namespace its declared option types at the boundary."""

    return cast(ParsedOptions, vars(namespace))


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def text(attributes: ParsedOptions, name: TextOption) -> str:
    """Return required text, including flags relaxed by facade shorthands."""

    value = attributes.get(name)
    if value is None:
        raise CliUsageError(f"{_flag(name)} is required")
    return value


def optional_text(attributes: ParsedOptions, name: TextOption) -> str | None:
    """Return a text option, or None when it was not provided."""

    return attributes.get(name)


def integer(attributes: ParsedOptions, name: IntegerOption) -> int:
    """Return a required integer, including relaxed positional shorthands."""

    value = attributes.get(name)
    if value is None:
        raise CliUsageError(f"{_flag(name)} is required")
    return value


def optional_integer(attributes: ParsedOptions, name: IntegerOption) -> int | None:
    """Return an integer option, or None when it was not provided."""

    return attributes.get(name)


def boolean(attributes: ParsedOptions, name: FlagOption) -> bool:
    """Return a flag whose parser always supplies a boolean default."""

    return attributes[name]


def optional_boolean(
    attributes: ParsedOptions, name: FlagOption | OptionalFlagOption
) -> bool | None:
    """Return a flag, distinguishing absence from explicit false."""

    return attributes.get(name)


def text_tuple(attributes: ParsedOptions, name: TextListOption) -> tuple[str, ...]:
    """Return a repeated text option, defaulting to an empty tuple."""

    return tuple(attributes.get(name) or ())


def optional_text_tuple(attributes: ParsedOptions, name: TextListOption) -> tuple[str, ...] | None:
    """Return a repeated text option, distinguishing absence from empty."""

    value = attributes.get(name)
    return None if value is None else tuple(value)


def integer_tuple(attributes: ParsedOptions, name: IntegerListOption) -> tuple[int, ...]:
    """Return a repeated integer option, defaulting to an empty tuple."""

    return tuple(attributes.get(name) or ())


def optional_integer_tuple(
    attributes: ParsedOptions, name: IntegerListOption
) -> tuple[int, ...] | None:
    """Return a repeated integer option, distinguishing absence from empty."""

    value = attributes.get(name)
    return None if value is None else tuple(value)


def choice(value: object, allowed: tuple[ChoiceT, ...], *, name: str) -> ChoiceT:
    """Narrow a parsed choice or normalized alias to its canonical literal."""

    for option in allowed:
        if value == option:
            return option
    raise CliUsageError(f"{_flag(name)} must be one of {', '.join(allowed)}")


def one_of(attributes: ParsedOptions, name: TextOption, allowed: tuple[ChoiceT, ...]) -> ChoiceT:
    """Return one required choice option."""

    return choice(attributes.get(name), allowed, name=name)


def optional_one_of(
    attributes: ParsedOptions, name: TextOption, allowed: tuple[ChoiceT, ...]
) -> ChoiceT | None:
    """Return one optional choice option."""

    value = attributes.get(name)
    return None if value is None else choice(value, allowed, name=name)


def one_of_tuple(
    attributes: ParsedOptions, name: TextListOption, allowed: tuple[ChoiceT, ...]
) -> tuple[ChoiceT, ...]:
    """Return a repeated choice option, defaulting to an empty tuple."""

    return tuple(choice(item, allowed, name=name) for item in attributes.get(name) or ())


def key_value_entries(attributes: ParsedOptions, name: KeyValueOption) -> list[tuple[str, str]]:
    """Return entries already parsed by key_value_entry."""

    return attributes.get(name) or []


def tagged_events(attributes: ParsedOptions, name: EventOption) -> list[StepEvent] | None:
    """Return events already constructed by the argparse type converters."""

    return attributes.get(name)
