"""Typed reads from one parsed argparse namespace."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import TypeVar

from .parsers import CliUsageError

ChoiceT = TypeVar("ChoiceT", bound=str)


def snapshot(namespace: argparse.Namespace) -> Mapping[str, object]:
    """Return one namespace's attributes as an ``object`` mapping."""

    attributes: Mapping[str, object] = vars(namespace)
    return attributes


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise CliUsageError(f"{_flag(name)} must be text")
    return value


def _integer(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CliUsageError(f"{_flag(name)} must be an integer")
    return value


def _boolean(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise CliUsageError(f"{_flag(name)} must be a flag")
    return value


def text(attributes: Mapping[str, object], name: str) -> str:
    """Return a required text option."""

    value = attributes.get(name)
    if value is None:
        raise CliUsageError(f"{_flag(name)} is required")
    return _string(name, value)


def optional_text(attributes: Mapping[str, object], name: str) -> str | None:
    """Return a text option, or ``None`` when it was not provided."""

    value = attributes.get(name)
    return None if value is None else _string(name, value)


def integer(attributes: Mapping[str, object], name: str) -> int:
    """Return a required integer option."""

    value = attributes.get(name)
    if value is None:
        raise CliUsageError(f"{_flag(name)} is required")
    return _integer(name, value)


def optional_integer(attributes: Mapping[str, object], name: str) -> int | None:
    """Return an integer option, or ``None`` when it was not provided."""

    value = attributes.get(name)
    return None if value is None else _integer(name, value)


def boolean(attributes: Mapping[str, object], name: str) -> bool:
    """Return a required boolean option."""

    return _boolean(name, attributes.get(name))


def optional_boolean(attributes: Mapping[str, object], name: str) -> bool | None:
    """Return a tri-state boolean option."""

    value = attributes.get(name)
    return None if value is None else _boolean(name, value)


def text_tuple(attributes: Mapping[str, object], name: str) -> tuple[str, ...]:
    """Return a repeated text option, defaulting to an empty tuple."""

    value = attributes.get(name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CliUsageError(f"{_flag(name)} must be a list")
    items: list[str] = []
    for item in value:
        items.append(_string(name, item))
    return tuple(items)


def optional_text_tuple(attributes: Mapping[str, object], name: str) -> tuple[str, ...] | None:
    """Return a repeated text option, distinguishing absence from empty."""

    return None if attributes.get(name) is None else text_tuple(attributes, name)


def integer_tuple(attributes: Mapping[str, object], name: str) -> tuple[int, ...]:
    """Return a repeated integer option, defaulting to an empty tuple."""

    value = attributes.get(name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CliUsageError(f"{_flag(name)} must be a list")
    items: list[int] = []
    for item in value:
        items.append(_integer(name, item))
    return tuple(items)


def optional_integer_tuple(attributes: Mapping[str, object], name: str) -> tuple[int, ...] | None:
    """Return a repeated integer option, distinguishing absence from empty."""

    return None if attributes.get(name) is None else integer_tuple(attributes, name)


def choice(value: object, allowed: tuple[ChoiceT, ...], *, name: str) -> ChoiceT:
    """Return one value from a closed set, or fail with the option name."""

    for option in allowed:
        if value == option:
            return option
    raise CliUsageError(f"{_flag(name)} must be one of {', '.join(allowed)}")


def one_of(attributes: Mapping[str, object], name: str, allowed: tuple[ChoiceT, ...]) -> ChoiceT:
    """Return one required choice option."""

    return choice(attributes.get(name), allowed, name=name)


def optional_one_of(
    attributes: Mapping[str, object], name: str, allowed: tuple[ChoiceT, ...]
) -> ChoiceT | None:
    """Return one optional choice option."""

    value = attributes.get(name)
    return None if value is None else choice(value, allowed, name=name)


def one_of_tuple(
    attributes: Mapping[str, object], name: str, allowed: tuple[ChoiceT, ...]
) -> tuple[ChoiceT, ...]:
    """Return a repeated choice option, defaulting to an empty tuple."""

    value = attributes.get(name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CliUsageError(f"{_flag(name)} must be a list")
    items: list[ChoiceT] = []
    for item in value:
        items.append(choice(item, allowed, name=name))
    return tuple(items)


def key_value_entries(attributes: Mapping[str, object], name: str) -> list[tuple[str, str]]:
    """Return repeated ``KEY=VALUE`` options."""

    value = attributes.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CliUsageError(f"{_flag(name)} must be a list")
    entries: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
        ):
            raise CliUsageError(f"{_flag(name)} must be KEY=VALUE")
        entries.append((item[0], item[1]))
    return entries


def tagged_events(attributes: Mapping[str, object], name: str) -> list[tuple[str, object]] | None:
    """Return repeated ``(option, value)`` events from a tagged-value action."""

    value = attributes.get(name)
    if value is None:
        return None
    if not isinstance(value, list):
        raise CliUsageError(f"{_flag(name)} must be a list")
    events: list[tuple[str, object]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise CliUsageError(f"{_flag(name)} must be an option/value pair")
        events.append((item[0], item[1]))
    return events
