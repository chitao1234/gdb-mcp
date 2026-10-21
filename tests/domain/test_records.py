"""Tests for the record models over debugger payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gdb_mcp.domain.records import (
    BreakpointRecord,
    DisassemblyInstructionRecord,
    FrameRecord,
    InferiorRecord,
    InferiorStateRecord,
    MemoryBlockRecord,
    RecordModel,
    RegisterRecord,
    SourceLineRecord,
    ThreadRecord,
    VariableRecord,
)
from gdb_mcp.domain.results import payload_to_mapping

_RECORD_MODELS: tuple[type[RecordModel], ...] = (
    BreakpointRecord,
    DisassemblyInstructionRecord,
    FrameRecord,
    InferiorRecord,
    InferiorStateRecord,
    MemoryBlockRecord,
    RegisterRecord,
    SourceLineRecord,
    ThreadRecord,
    VariableRecord,
)


class TestRecordRoundTrip:
    """A record keeps what the debugger sent and nothing it did not."""

    def test_undeclared_fields_are_kept(self) -> None:
        """A field this server does not know still reaches the client."""

        record = BreakpointRecord.from_payload({"number": "1", "what": "nums[0]", "disp": "keep"})

        assert record.to_payload() == {"number": "1", "what": "nums[0]", "disp": "keep"}

    def test_fields_that_were_not_sent_stay_absent(self) -> None:
        """Declared-but-unsent fields are readable, yet do not reappear in the payload."""

        record = FrameRecord.from_payload({"func": "main"})

        assert record.level is None
        assert record.to_payload() == {"func": "main"}

    @pytest.mark.parametrize("model", _RECORD_MODELS)
    def test_an_empty_payload_is_a_record_with_no_fields(self, model: type[RecordModel]) -> None:
        """Every record tolerates a debugger that reported nothing."""

        assert model.from_payload({}).to_payload() == {}

    def test_hyphenated_payload_keys_are_read_by_alias(self) -> None:
        """MI spells some fields with hyphens; the record exposes and re-emits them."""

        record = BreakpointRecord.from_payload({"original-location": "main"})

        assert record.original_location == "main"
        assert record.to_payload() == {"original-location": "main"}

        thread = ThreadRecord.from_payload({"id": "1", "target-id": "Thread 0x1"})

        assert thread.target_id == "Thread 0x1"
        assert payload_to_mapping(thread) == {"id": "1", "target-id": "Thread 0x1"}

    def test_a_dap_spelled_key_is_read_by_alias(self) -> None:
        """DAP spells the same idea in camel case; the record accepts both."""

        record = VariableRecord.from_payload({"variablesReference": 3})

        assert record.variables_reference == 3
        assert record.to_payload() == {"variablesReference": 3}


class TestRecordValidation:
    """Declared types are a contract: a record never silently re-spells a value."""

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"times": "3"}, "valid integer"),
            ({"enabled": "y"}, "valid boolean"),
            ({"number": 1}, "valid string"),
        ],
    )
    def test_a_value_of_the_wrong_type_is_rejected(
        self, payload: dict[str, object], message: str
    ) -> None:
        """Adapters canonicalize, so a raw MI spelling must not pass validation."""

        with pytest.raises(ValidationError, match=message):
            BreakpointRecord.from_payload(payload)

    def test_a_register_number_is_an_integer(self) -> None:
        """The number indexes the register-name list, so MI's spelling is not enough."""

        with pytest.raises(ValidationError, match="valid integer"):
            RegisterRecord.from_payload({"number": "2"})

    def test_an_unknown_stop_kind_is_rejected(self) -> None:
        """`stop_kind` is a closed vocabulary, not free text."""

        with pytest.raises(ValidationError, match="stop_kind"):
            InferiorStateRecord.from_payload({"stop_kind": "not-a-kind"})

    def test_a_known_stop_kind_is_kept(self) -> None:
        record = InferiorStateRecord.from_payload({"stop_kind": "breakpoint"})

        assert record.stop_kind == "breakpoint"
