"""Entrypoint tests for environment-supplied server configuration."""

from __future__ import annotations

import pytest

from gdb_mcp.server import parse_server_config


@pytest.fixture
def auth_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the environment token that HTTP transport should pick up."""

    monkeypatch.setenv("GDB_MCP_AUTH_TOKEN", "env-secret")


def test_auth_token_env_var_supplies_the_http_token(auth_token_env: None) -> None:
    """The environment token should supply the token when no flag is given."""

    config = parse_server_config(["--transport", "streamable-http"])

    assert config.auth_token == "env-secret"


def test_auth_token_flag_wins_over_the_env_var(auth_token_env: None) -> None:
    """An explicit ``--auth-token`` flag must override the environment token."""

    config = parse_server_config(["--transport", "streamable-http", "--auth-token", "flag-secret"])

    assert config.auth_token == "flag-secret"


def test_auth_token_env_var_does_not_break_stdio(auth_token_env: None) -> None:
    """The HTTP-only environment token must not leak into stdio transport."""

    config = parse_server_config([])

    assert config.transport == "stdio"
    assert config.auth_token is None
