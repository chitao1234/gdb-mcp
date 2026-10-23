"""Go-specific inspection contracts and Delve RPC boundaries."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from gdb_mcp import contracts as c
from gdb_mcp.domain import OperationError, OperationSuccess, ThreadListInfo, ThreadRecord
from gdb_mcp.domain.errors import DebuggerDecodeError
from gdb_mcp.session.delve_values import frame_record
from gdb_mcp.session.factory import create_default_session_service
from gdb_mcp.session.gdb_backend import GdbMiBackend
from gdb_mcp.session.lldb_backend import LldbSbBackend
from gdb_mcp.session.requests import ThreadQuery

from .test_delve_backend import backend


def test_deferred_records_keep_indices_and_unreadable_entries() -> None:
    native = {
        "pc": 42,
        "Defers": [
            {
                "DeferredLoc": {"pc": 16, "function": {"name": "main.cleanup"}},
                "DeferLoc": {"file": "main.go", "line": 20},
            },
            {"Unreadable": "cannot read defer"},
        ],
    }
    ordinary = frame_record(native)
    assert "defers" not in ordinary.to_payload()
    frame = frame_record(native, 2, include_defers=True)
    assert frame.level == 2 and frame.defers is not None
    assert [defer.index for defer in frame.defers] == [1, 2]
    first, second = frame.defers
    assert first.function is not None and first.function.func == "main.cleanup"
    assert first.registered_at is not None and first.registered_at.line == 20
    assert second.unreadable == "cannot read defer"
    assert frame_record({}, include_defers=True).defers == []
    with pytest.raises(DebuggerDecodeError):
        frame_record({"Defers": ["bad defer"]}, include_defers=True)


@pytest.mark.parametrize(
    ("release", "go_version", "channels", "deferred_arguments"),
    [
        ("1.6.1", "Go cmd/compile go1.16.4", False, True),
        ("1.21.0", "Go cmd/compile go1.21.0", False, False),
        ("1.21.1", "Go cmd/compile go1.21.0", True, False),
        ("1.27.2", "Go cmd/compile go1.27.1", True, False),
    ],
)
def test_exact_go_inspection_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    release: str,
    go_version: str,
    channels: bool,
    deferred_arguments: bool,
) -> None:
    selected = backend()
    monkeypatch.setattr(
        selected,
        "_request",
        Mock(
            side_effect=[
                {},
                {
                    "APIVersion": 2,
                    "DelveVersion": release,
                    "TargetGoVersion": go_version,
                },
                {"State": {"Pid": 42}},
            ]
        ),
    )
    selected._handshake(timeout_sec=1)
    assert selected._inspection.channel_queries_supported is channels
    assert selected._inspection.deferred_arguments_supported is deferred_arguments


def test_deferred_scope_is_forwarded_without_changing_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = backend()
    selected.runtime.mark_thread_selected(19)
    selected.runtime.mark_frame_selected(3)
    selected._inspection.deferred_arguments_supported = True
    monkeypatch.setattr(selected, "_require_client", Mock())
    request = Mock(
        side_effect=[
            {"Variable": {"value": "44"}},
            {"Variables": []},
            {"Args": [{"name": "b", "value": "44"}]},
        ]
    )
    monkeypatch.setattr(selected, "_request", request)
    assert isinstance(selected.evaluate_expression("b", 7, 2, deferred_call=1), OperationSuccess)
    assert isinstance(selected.get_variables(7, 2, deferred_call=1), OperationSuccess)
    for call in request.call_args_list:
        assert call.args[1]["Scope"] == {"GoroutineID": 7, "Frame": 2, "DeferredCall": 1}
    assert selected.runtime.current_thread_id == 19 and selected.runtime.current_frame == 3


def test_modern_deferred_scope_is_rejected_before_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_require_client", Mock())
    request = Mock()
    monkeypatch.setattr(selected, "_request", request)
    for result in (
        selected.evaluate_expression("a", deferred_call=1),
        selected.get_variables(deferred_call=1),
    ):
        assert isinstance(result, OperationError) and result.code == "unsupported_operation"
    request.assert_not_called()


def test_channel_queries_handle_overfull_pages_and_deduplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = backend()
    selected._inspection.channel_queries_supported = True
    monkeypatch.setattr(selected, "_require_client", Mock())
    monkeypatch.setattr(selected._inspection, "_variable", Mock(return_value={"kind": 18}))
    page = [{"id": number} for number in range(1, 1002)]
    request = Mock(
        side_effect=[
            {"Goroutines": page, "Nextg": -1},
            {"Goroutines": [{"id": 1001}, {"id": 1002}], "Nextg": -1},
        ]
    )
    monkeypatch.setattr(selected, "_request", request)
    result = selected.get_threads(ThreadQuery(waiting_on_channel="jobs", thread_id=7, frame=2))
    assert isinstance(result, OperationSuccess)
    assert len(result.value.threads) == 1002 and not result.value.truncated
    assert request.call_args_list[1].args[1]["Start"] == 1001
    arguments = request.call_args_list[0].args[1]
    assert arguments["EvalScope"] == {"GoroutineID": 7, "Frame": 2}
    assert arguments["Filters"] == [{"Kind": 8, "Arg": "jobs", "Negated": False}]


@pytest.mark.parametrize("count", [2, 3])
def test_channel_query_reports_truncation(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    selected = backend()
    selected._inspection.channel_queries_supported = True
    monkeypatch.setattr(selected, "_require_client", Mock())
    monkeypatch.setattr(selected._inspection, "_variable", Mock(return_value={"kind": 18}))
    monkeypatch.setattr(
        selected,
        "_request",
        Mock(return_value={"Goroutines": [{"id": i} for i in range(1, count + 1)], "Nextg": -1}),
    )
    result = selected.get_threads(ThreadQuery(waiting_on_channel="jobs", max_results=2))
    assert isinstance(result, OperationSuccess)
    assert len(result.value.threads) == 2 and result.value.truncated is (count > 2)


def test_channel_query_rejects_wrong_type_and_old_release(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = backend()
    monkeypatch.setattr(selected, "_require_client", Mock())
    request = Mock()
    monkeypatch.setattr(selected, "_request", request)
    result = selected.get_threads(ThreadQuery(waiting_on_channel="jobs"))
    assert isinstance(result, OperationError) and result.code == "unsupported_operation"
    selected._inspection.channel_queries_supported = True
    monkeypatch.setattr(selected._inspection, "_variable", Mock(return_value={"kind": 2}))
    result = selected.get_threads(ThreadQuery(waiting_on_channel="jobs"))
    assert isinstance(result, OperationError) and result.code == "validation_error"
    request.assert_not_called()


@pytest.mark.parametrize("debugger", [c.DEBUGGER_GDB, c.DEBUGGER_LLDB])
def test_other_backends_reject_go_options_and_honor_result_limit(
    monkeypatch: pytest.MonkeyPatch, debugger: c.DebuggerName
) -> None:
    selected = create_default_session_service(debugger).backend
    for result in (
        selected.get_threads(ThreadQuery(waiting_on_channel="jobs")),
        selected.get_backtrace(include_defers=True),
        selected.evaluate_expression("x", deferred_call=1),
        selected.get_variables(deferred_call=1),
    ):
        assert isinstance(result, OperationError) and result.code == "unsupported_operation"
    assert isinstance(selected, (GdbMiBackend, LldbSbBackend))
    inspection = selected._inspection
    monkeypatch.setattr(
        inspection,
        "get_threads",
        Mock(
            return_value=OperationSuccess(
                ThreadListInfo([ThreadRecord(id="1"), ThreadRecord(id="2")], "1")
            )
        ),
    )
    limited = selected.get_threads(ThreadQuery(max_results=1))
    assert isinstance(limited, OperationSuccess) and limited.value.truncated
    assert [thread.id for thread in limited.value.threads] == ["1"]
