"""Composed inspection service built from explicit collaborators."""

from __future__ import annotations

from typing import Literal

from ...domain import (
    BacktraceInfo,
    DisassemblyInfo,
    ExpressionValueInfo,
    FrameInfo,
    FrameSelectionInfo,
    InferiorListInfo,
    InferiorSelectionInfo,
    MemoryReadInfo,
    OperationError,
    OperationSuccess,
    RegistersInfo,
    SourceContextInfo,
    ThreadListInfo,
    ThreadSelectionInfo,
    VariablesInfo,
)
from ..command_runner import SessionCommandRunner
from ..requests import DisassemblyRequest, SourceContextRequest
from ..runtime import SessionRuntime
from .base import CodeLocationResolver, InspectionContext
from .disassembly import InspectionDisassembly
from .inferior_state import InspectionInferiorState
from .memory import InspectionMemory
from .source import InspectionSource
from .threads import InspectionThreads
from .variables import InspectionVariables


class SessionInspectionService:
    """Facade over the inspection collaborators.

    Built by composition only: each capability is an explicit collaborator, so
    there is no mixin inheritance or implicit MRO. The public method names are
    the stable inspection API used by the rest of the session layer.
    """

    def __init__(self, runtime: SessionRuntime, command_runner: SessionCommandRunner):
        context = InspectionContext(runtime, command_runner)
        resolver = CodeLocationResolver(runtime, command_runner)
        self._threads = InspectionThreads(context)
        self._inferiors = InspectionInferiorState(context)
        self._variables = InspectionVariables(context)
        self._memory = InspectionMemory(context)
        self._disassembly = InspectionDisassembly(context)
        self._source = InspectionSource(context, resolver)

    def reset_capability_cache(self) -> None:
        """Clear GDB feature probes for a new debugger process."""

        self._disassembly.reset_capability_cache()
        self._memory.reset_capability_cache()

    def get_threads(self) -> OperationSuccess[ThreadListInfo] | OperationError:
        """Return the thread list for the current session."""

        return self._threads.get_threads()

    def list_inferiors(self) -> OperationSuccess[InferiorListInfo] | OperationError:
        """Return the inferior inventory for the current session."""

        return self._inferiors.list_inferiors()

    def select_inferior(
        self, inferior_id: int
    ) -> OperationSuccess[InferiorSelectionInfo] | OperationError:
        """Select one inferior as the current debugger context."""

        return self._inferiors.select_inferior(inferior_id)

    def select_thread(
        self, thread_id: int
    ) -> OperationSuccess[ThreadSelectionInfo] | OperationError:
        """Select one thread as the current debugger context."""

        return self._threads.select_thread(thread_id)

    def get_backtrace(
        self, thread_id: int | None = None, max_frames: int = 100
    ) -> OperationSuccess[BacktraceInfo] | OperationError:
        """Return a structured backtrace."""

        return self._threads.get_backtrace(thread_id, max_frames)

    def get_frame_info(
        self,
        *,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[FrameInfo] | OperationError:
        """Return information about the current or an overridden frame."""

        return self._threads.get_frame_info(thread_id=thread_id, frame=frame)

    def disassemble(
        self, request: DisassemblyRequest
    ) -> OperationSuccess[DisassemblyInfo] | OperationError:
        """Return structured disassembly for one resolved code location."""

        return self._disassembly.disassemble(request)

    def select_frame(
        self, frame_number: int
    ) -> OperationSuccess[FrameSelectionInfo] | OperationError:
        """Select a specific stack frame."""

        return self._threads.select_frame(frame_number)

    def evaluate_expression(
        self,
        expression: str,
        thread_id: int | None = None,
        frame: int | None = None,
    ) -> OperationSuccess[ExpressionValueInfo] | OperationError:
        """Evaluate one expression in the current or an overridden context."""

        return self._memory.evaluate_expression(expression, thread_id=thread_id, frame=frame)

    def read_memory(
        self,
        address: str,
        count: int,
        *,
        offset: int = 0,
    ) -> OperationSuccess[MemoryReadInfo] | OperationError:
        """Read a block of memory."""

        return self._memory.read_memory(address, count, offset=offset)

    def get_variables(
        self, thread_id: int | None = None, frame: int = 0
    ) -> OperationSuccess[VariablesInfo] | OperationError:
        """Return local variables for a frame."""

        return self._variables.get_variables(thread_id, frame)

    def get_source_context(
        self, request: SourceContextRequest
    ) -> OperationSuccess[SourceContextInfo] | OperationError:
        """Return structured source lines for one resolved code location."""

        return self._source.get_source_context(request)

    def get_registers(
        self,
        thread_id: int | None = None,
        frame: int | None = None,
        register_numbers: list[int] | None = None,
        register_names: list[str] | None = None,
        include_vector_registers: bool = True,
        max_registers: int | None = None,
        value_format: Literal["hex", "natural"] = "hex",
    ) -> OperationSuccess[RegistersInfo] | OperationError:
        """Return register values for the current or an overridden context."""

        return self._variables.get_registers(
            thread_id=thread_id,
            frame=frame,
            register_numbers=register_numbers,
            register_names=register_names,
            include_vector_registers=include_vector_registers,
            max_registers=max_registers,
            value_format=value_format,
        )
