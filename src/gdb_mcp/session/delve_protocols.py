"""Shared Delve request interface and operation boundary."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Protocol, TypeVar

from typing_extensions import ParamSpec

from ..domain import OperationError, OperationSuccess
from ..transport.delve_rpc import DelveRpcClient, DelveRpcError
from .constants import DEFAULT_TIMEOUT_SEC

P = ParamSpec("P")
T = TypeVar("T")


def operation(
    function: Callable[P, OperationSuccess[T]],
) -> Callable[P, OperationSuccess[T] | OperationError]:
    @wraps(function)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> OperationSuccess[T] | OperationError:
        try:
            return function(*args, **kwargs)
        except DelveRpcError as exc:
            return OperationError(message=str(exc), code=exc.code)
        except OSError as exc:
            return OperationError(message=str(exc))

    return invoke


VARIABLE_LOAD_CONFIG: dict[str, object] = {
    "FollowPointers": True,
    "MaxVariableRecurse": 2,
    "MaxStringLen": 1024,
    "MaxArrayValues": 64,
    "MaxStructFields": -1,
}


class DelveRequests(Protocol):
    def _require_client(self) -> DelveRpcClient: ...

    def _request(
        self,
        method: str,
        arguments: dict[str, object] | None = None,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> dict[str, object]: ...

    def _client_state(self, response: dict[str, object], *, record: bool = True) -> None: ...
