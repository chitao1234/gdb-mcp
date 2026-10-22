"""Stop events captured by workflow steps independently of history retention."""

from __future__ import annotations

import pytest

from gdb_mcp.contracts import TOOL_EXECUTION_MANAGE, TOOL_SESSION_QUERY
from gdb_mcp.domain import OperationSuccess, SessionMessage, StopEvent
from gdb_mcp.session.service import SessionService
from gdb_mcp.session.workflow import BatchStepInvocation


@pytest.mark.parametrize("history_limit", [0, 1, 2])
@pytest.mark.parametrize("capture_stop_events", [False, True])
def test_batch_captures_each_new_stop_without_reusing_the_previous_one(
    session_service: SessionService, history_limit: int, capture_stop_events: bool
) -> None:
    runtime = session_service.runtime
    runtime.stop_history_limit = history_limit
    for _ in range(max(1, history_limit)):
        runtime.record_stop_event(StopEvent(execution_state="paused", reason="breakpoint-hit"))
    events: list[StopEvent] = []

    def stop() -> OperationSuccess[SessionMessage]:
        event = StopEvent(execution_state="paused", reason="breakpoint-hit")
        events.append(event)
        runtime.record_stop_event(event)
        return OperationSuccess(SessionMessage(message="stopped"))

    def inspect() -> OperationSuccess[SessionMessage]:
        return OperationSuccess(SessionMessage(message="inspected"))

    result = session_service.execute_batch(
        [
            BatchStepInvocation(tool=TOOL_EXECUTION_MANAGE, execute=stop),
            BatchStepInvocation(tool=TOOL_EXECUTION_MANAGE, execute=stop),
            BatchStepInvocation(tool=TOOL_SESSION_QUERY, execute=inspect),
        ],
        capture_stop_events=capture_stop_events,
    )

    first, second, third = result.value.steps
    assert first.stop_event is (events[0] if capture_stop_events else None)
    assert second.stop_event is (events[1] if capture_stop_events else None)
    assert third.stop_event is None
    assert result.value.last_stop_event is events[-1]
    assert len(runtime.stop_history) == history_limit
