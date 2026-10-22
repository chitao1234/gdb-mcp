"""GDB command data kept typed until a public result is needed."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import DEBUGGER_GDB
from ..domain import BackendData, CommandExecutionInfo, OperationError, OperationSuccess
from ..domain.results import payload_to_mapping
from ..transport import ParsedMiResponse


@dataclass(frozen=True, slots=True)
class GdbCommandResult:
    """One successful command with its parsed MI response."""

    command: str
    parsed: ParsedMiResponse

    @property
    def console_output(self) -> str:
        return "".join(item for item in self.parsed.console if isinstance(item, str))

    def to_output(self) -> CommandExecutionInfo:
        """Build the public command payload at the operation boundary."""

        return CommandExecutionInfo(
            command=self.command,
            backend_data=BackendData(
                debugger=DEBUGGER_GDB,
                console_output=self.console_output,
                data=payload_to_mapping(self.parsed.command_data()),
            ),
        )


def command_output(
    result: OperationSuccess[GdbCommandResult] | OperationError,
) -> OperationSuccess[CommandExecutionInfo] | OperationError:
    """Publish a command result while preserving its outcome and metadata."""

    if isinstance(result, OperationError):
        return result
    return OperationSuccess(
        result.value.to_output(), warnings=result.warnings, action=result.action
    )
