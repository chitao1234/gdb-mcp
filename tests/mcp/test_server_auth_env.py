"""Entrypoint tests for environment-supplied server configuration."""

from __future__ import annotations

from gdb_mcp.server import parse_server_config


def test_auth_token_env_var_supplies_the_http_token(monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_AUTH_TOKEN", "env-secret")

    config = parse_server_config(["--transport", "streamable-http"])

    assert config.auth_token == "env-secret"


def test_auth_token_flag_wins_over_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_AUTH_TOKEN", "env-secret")

    config = parse_server_config(["--transport", "streamable-http", "--auth-token", "flag-secret"])

    assert config.auth_token == "flag-secret"


def test_auth_token_env_var_does_not_break_stdio(monkeypatch) -> None:
    monkeypatch.setenv("GDB_MCP_AUTH_TOKEN", "env-secret")

    config = parse_server_config([])

    assert config.transport == "stdio"
    assert config.auth_token is None
