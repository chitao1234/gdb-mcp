"""Unit tests for the untrusted-payload narrowing helpers."""

import logging

from gdb_mcp.domain.parsing import as_record_list


def test_as_record_list_keeps_only_mappings(caplog):
    """Mappings are returned; other entries are dropped and logged."""

    with caplog.at_level(logging.WARNING, logger="gdb_mcp.domain.parsing"):
        records = as_record_list([{"a": 1}, "oops", 2, {"b": 2}])

    assert records == [{"a": 1}, {"b": 2}]
    assert any("non-mapping" in message for message in caplog.messages)


def test_as_record_list_warns_when_value_is_not_a_list(caplog):
    """A non-list payload is malformed output and should be reported."""

    with caplog.at_level(logging.WARNING, logger="gdb_mcp.domain.parsing"):
        records = as_record_list({"not": "a list"})

    assert records == []
    assert any("list of MI records" in message for message in caplog.messages)


def test_as_record_list_treats_a_missing_value_silently(caplog):
    """An absent payload value is expected and must not log noise."""

    with caplog.at_level(logging.WARNING, logger="gdb_mcp.domain.parsing"):
        records = as_record_list(None)

    assert records == []
    assert caplog.messages == []
