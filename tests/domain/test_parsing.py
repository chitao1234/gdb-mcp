"""Unit tests for the untrusted-payload narrowing helpers."""

import logging

import pytest

from gdb_mcp.domain.adapters import frame_info_from_payload
from gdb_mcp.domain.parsing import as_mapping, as_optional_mapping, as_record_list


def test_as_mapping_falls_back_to_an_empty_mapping() -> None:
    """Non-mappings become an empty mapping so callers can read without guarding."""

    assert as_mapping(None) == {}
    assert as_mapping("not a mapping") == {}
    assert as_mapping({"a": 1}) == {"a": 1}


def test_as_optional_mapping_preserves_absence() -> None:
    """Absent/mistyped values stay None so callers can tell them from empty."""

    assert as_optional_mapping(None) is None
    assert as_optional_mapping("not a mapping") is None
    assert as_optional_mapping({}) == {}
    assert as_optional_mapping({"a": 1}) == {"a": 1}


def test_frame_info_reports_an_absent_frame_as_none() -> None:
    """A payload with no frame must not be indistinguishable from an empty frame."""

    assert frame_info_from_payload({}).frame is None
    assert frame_info_from_payload({"frame": {}}).frame == {}


def test_as_record_list_keeps_only_mappings(caplog: pytest.LogCaptureFixture) -> None:
    """Mappings are returned; other entries are dropped and logged."""

    with caplog.at_level(logging.WARNING, logger="gdb_mcp.domain.parsing"):
        records = as_record_list([{"a": 1}, "oops", 2, {"b": 2}])

    assert records == [{"a": 1}, {"b": 2}]
    assert any("non-mapping" in message for message in caplog.messages)


def test_as_record_list_warns_when_value_is_not_a_list(caplog: pytest.LogCaptureFixture) -> None:
    """A non-list payload is malformed output and should be reported."""

    with caplog.at_level(logging.WARNING, logger="gdb_mcp.domain.parsing"):
        records = as_record_list({"not": "a list"})

    assert records == []
    assert any("list of MI records" in message for message in caplog.messages)


def test_as_record_list_treats_a_missing_value_silently(caplog: pytest.LogCaptureFixture) -> None:
    """An absent payload value is expected and must not log noise."""

    with caplog.at_level(logging.WARNING, logger="gdb_mcp.domain.parsing"):
        records = as_record_list(None)

    assert records == []
    assert caplog.messages == []
