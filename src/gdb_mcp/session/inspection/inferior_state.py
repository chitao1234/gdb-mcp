"""Inferior listing, selection, and runtime enrichment methods."""

from __future__ import annotations

from ...domain import (
    InferiorListInfo,
    InferiorRecord,
    InferiorSelectionInfo,
    OperationError,
    OperationSuccess,
    stop_kind_from_reason,
)
from ..constants import DEFAULT_TIMEOUT_SEC
from ..inferiors import inferior_ids, parse_inferiors_output
from .base import InspectionContext


class InspectionInferiorState:
    """Inferior listing, selection, and runtime enrichment methods."""

    def __init__(self, context: InspectionContext):
        self._context = context
        self._runtime = context.runtime
        self._command_runner = context.command_runner

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """List the inferiors currently managed by this GDB session."""

        result = self._command_runner.execute_command_result(
            "info inferiors", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        payload = self._parse_inferiors_output(result.value.backend_data.console_output)
        self._runtime.update_inferior_inventory(
            current_inferior_id=payload.current_inferior_id,
            count=len(payload.inferiors),
            inferior_ids=inferior_ids(payload),
        )
        inferiors_with_state = self._enrich_inferiors_with_runtime_state(payload.inferiors)
        payload = InferiorListInfo(
            inferiors=inferiors_with_state,
            current_inferior_id=payload.current_inferior_id,
        )
        return OperationSuccess(payload)

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        """Select a specific inferior to make it the current debugger context."""

        result = self._command_runner.execute_command_result(
            f"inferior {inferior_id}", timeout_sec=DEFAULT_TIMEOUT_SEC
        )
        if isinstance(result, OperationError):
            return result

        self._runtime.mark_inferior_selected(inferior_id)
        inventory_result = self.list_inferiors()
        if isinstance(inventory_result, OperationError):
            return OperationSuccess(
                InferiorSelectionInfo(
                    inferior_id=inferior_id,
                    message=f"Inferior {inferior_id} selected",
                ),
                warnings=(
                    (
                        "Inferior selection succeeded, but refreshing inferior inventory failed: "
                        f"{inventory_result.message}"
                    ),
                ),
            )

        selected_record = next(
            (
                record
                for record in inventory_result.value.inferiors
                if record.inferior_id == inferior_id
            ),
            None,
        )
        if selected_record is None:
            return OperationSuccess(
                InferiorSelectionInfo(
                    inferior_id=inferior_id,
                    message=f"Inferior {inferior_id} selected",
                ),
                warnings=(
                    f"GDB selected inferior {inferior_id}, but it was missing from the refreshed inventory.",
                ),
            )

        return OperationSuccess(self._inferior_selection_info(inferior_id, selected_record))

    def _parse_inferiors_output(self, output: str) -> InferiorListInfo:
        """Parse `info inferiors` CLI output into a structured inferior list."""

        return parse_inferiors_output(
            output,
            current_inferior_id=self._runtime.current_inferior_id,
        )

    @staticmethod
    def _inferior_selection_info(inferior_id: int, record: InferiorRecord) -> InferiorSelectionInfo:
        """Convert one inferior record into a selection response."""

        display = record.display
        description = record.description
        connection = record.connection
        executable = record.executable

        return InferiorSelectionInfo(
            inferior_id=inferior_id,
            display=display,
            description=description,
            connection=connection,
            executable=executable,
            message=f"Inferior {inferior_id} selected",
        )

    def _enrich_inferiors_with_runtime_state(
        self,
        inferiors: list[InferiorRecord],
    ) -> list[InferiorRecord]:
        """Attach runtime execution-state metadata to listed inferiors when known."""

        state_by_inferior = {
            record.inferior_id: record
            for record in self._runtime.inferiors_state_summary()
            if isinstance(record.inferior_id, int)
        }

        enriched: list[InferiorRecord] = []
        for inferior in inferiors:
            inferior_id = inferior.inferior_id
            if not isinstance(inferior_id, int):
                enriched.append(inferior)
                continue

            state_record = state_by_inferior.get(inferior_id)
            if state_record is None:
                enriched.append(inferior)
                continue

            update: dict[str, object] = {}
            execution_state = state_record.execution_state
            if isinstance(execution_state, str):
                update["execution_state"] = execution_state
            stop_reason_value = (
                state_record.stop_reason if isinstance(state_record.stop_reason, str) else None
            )
            update["stop_reason"] = stop_reason_value
            update["stop_kind"] = stop_kind_from_reason(stop_reason_value)
            exit_code = state_record.exit_code
            update["exit_code"] = int(exit_code) if isinstance(exit_code, int) else None
            enriched.append(inferior.model_copy(update=update))

        return enriched
