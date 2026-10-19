"""Shared helpers for driving the MCP CLI client from tests.

Every CLI test mocks the same two collaborators (``resolve_server`` and
``invoke_tool``), buffers ``main()``'s output, and then inspects the recorded
call. This module captures that one shape so individual tests only describe
their arguments and expectations.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from io import StringIO
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from gdb_mcp.client.cli import main
from gdb_mcp.client.daemon import ResolvedServer
from gdb_mcp.client.runtime import ClientToolResponse

SERVER_URL = "http://127.0.0.1:8000/mcp"
"""The server URL used by tests that pass an explicit ``--server-url``."""

MANAGED_TOKEN = "tok"
"""Auth token reported by the managed-server double."""


@dataclass(frozen=True)
class CliResult:
    """Everything a CLI test can observe about one ``main()`` invocation."""

    exit_code: int | str | None
    invoke: AsyncMock
    stdout: str
    stderr: str

    def assert_invoked(
        self,
        tool: str,
        payload: dict[str, object],
        *,
        auth_token: str | None = None,
        http_client: object | None = None,
        url: str = SERVER_URL,
    ) -> None:
        """Assert exactly one tool call was forwarded with this request."""

        self.invoke.assert_awaited_once_with(
            url,
            tool,
            payload,
            http_client=http_client,
            auth_token=auth_token,
        )

    @property
    def sent_tool(self) -> str:
        """The tool name forwarded to the transport."""

        return cast(str, self._call_args()[1])

    @property
    def sent_payload(self) -> dict[str, object]:
        """The arguments forwarded to the transport."""

        return cast(dict[str, object], self._call_args()[2])

    @property
    def sent_url(self) -> str:
        """The server URL forwarded to the transport."""

        return cast(str, self._call_args()[0])

    def _call_args(self) -> tuple[object, ...]:
        call = self.invoke.await_args
        assert call is not None, "the CLI never reached the transport"
        return cast(tuple[object, ...], call.args)


def invoke_cli(
    argv: Sequence[str],
    *,
    response: ClientToolResponse | dict[str, object] | None = None,
    is_error: bool = False,
    resolved: ResolvedServer | None = None,
    side_effect: BaseException | None = None,
    expect_exit: bool = False,
) -> CliResult:
    """Run ``main(argv)`` against a mocked transport and capture the result.

    ``resolved`` mocks the daemon lookup and is only needed when the call omits
    ``--server-url``; ``expect_exit`` catches the ``SystemExit`` that argparse
    raises for rejected input.
    """

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.ExitStack() as stack:
        invoke = stack.enter_context(
            patch("gdb_mcp.client.cli.invoke_tool", new_callable=AsyncMock)
        )
        if resolved is not None:
            resolve = stack.enter_context(
                patch("gdb_mcp.client.cli.resolve_server", new_callable=AsyncMock)
            )
            resolve.return_value = resolved
        invoke.return_value = _as_response(response, is_error=is_error)
        if side_effect is not None:
            invoke.side_effect = side_effect

        if expect_exit:
            with pytest.raises(SystemExit) as exc_info:
                asyncio.run(main(list(argv), stdout=stdout, stderr=stderr))
            exit_code: int | str | None = exc_info.value.code
        else:
            exit_code = asyncio.run(main(list(argv), stdout=stdout, stderr=stderr))

    return CliResult(
        exit_code=exit_code,
        invoke=invoke,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _as_response(
    response: ClientToolResponse | dict[str, object] | None, *, is_error: bool
) -> ClientToolResponse:
    if isinstance(response, ClientToolResponse):
        return response
    return ClientToolResponse(
        payload=response if response is not None else {"status": "success"},
        is_error=is_error,
    )
