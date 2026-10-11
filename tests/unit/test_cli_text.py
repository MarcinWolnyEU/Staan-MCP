"""Exact user-facing text of the command line: help, usage errors and startup refusals.

These tests pin messages word for word so that a reworded or mangled message is caught. The
README documents the same flags, so the help text is also a consistency checkpoint.
"""
# pylint: disable=redefined-outer-name,protected-access

from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from staan_mcp import cli
from staan_mcp.config import HttpConfig, Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

API_KEY = "test-key-0123456789"
AUTH_ENV = {
    "STAAN_MCP_AUTH_ISSUER_URL": "https://auth.example.com",
    "STAAN_MCP_AUTH_RESOURCE_URL": "https://mcp.example.com/mcp",
    "STAAN_MCP_AUTH_JWKS_URL": "https://auth.example.com/jwks",
}

EXPECTED_HELP = """\
usage: staan-mcp [-h] [--transport {stdio,http,stateless}] [--host HOST]
                 [--port PORT] [--path PATH] [--allowed-host HOST]
                 [--allowed-origin ORIGIN] [--insecure-no-auth]
                 [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [--version]

MCP server for the Staan API: web search and grounded answers.

options:
  -h, --help            show this help message and exit
  --transport {stdio,http,stateless}
                        transport to serve (default: stdio)
  --host HOST           bind address for the HTTP transports (default:
                        127.0.0.1)
  --port PORT           bind port for the HTTP transports (default: 8000)
  --path PATH           URL path of the MCP endpoint (default: /mcp)
  --allowed-host HOST   accepted Host header value; repeatable; env:
                        STAAN_MCP_ALLOWED_HOSTS
  --allowed-origin ORIGIN
                        accepted Origin header value; repeatable; env:
                        STAAN_MCP_ALLOWED_ORIGINS
  --insecure-no-auth    allow a non-loopback HTTP bind without the OAuth gate
                        (trusted proxy only)
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        log level; logs go to stderr (default: WARNING)
  --version             show program's version number and exit

The Staan API key is read from STAAN_API_KEY. Every option also has a
STAAN_MCP_* environment variable (STAAN_MCP_TRANSPORT, _HOST, _PORT, _PATH,
_ALLOWED_HOSTS, _ALLOWED_ORIGINS, _LOG_LEVEL); flags win.
"""


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every ``STAAN_*`` variable, fix the help width and restore the root logger."""
    for name in [name for name in os.environ if name.startswith("STAAN_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("COLUMNS", "80")
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> list[Settings]:
    """Replace the server factory and runner; record the settings ``main`` built."""
    recorded: list[Settings] = []

    def fake_create_server(settings: Settings, **_kwargs: Any) -> SimpleNamespace:
        recorded.append(settings)
        return SimpleNamespace(settings=SimpleNamespace(log_level="INFO"))

    monkeypatch.setattr(cli, "create_server", fake_create_server)
    monkeypatch.setattr(cli, "run_server", lambda *_args: None)
    return recorded


def run(monkeypatch: pytest.MonkeyPatch, argv: list[str], env: dict[str, str] | None = None) -> int:
    """Run ``main`` with a valid API key plus ``env``."""
    for name, value in {"STAAN_API_KEY": API_KEY, **(env or {})}.items():
        monkeypatch.setenv(name, value)
    return cli.main(argv)


def test_help_text_is_exactly_what_the_readme_documents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--help"]) == 0

    assert capsys.readouterr().out == EXPECTED_HELP


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--port", "eighty"], "invalid port: 'eighty' (use 1 to 65535)"),
        (["--port", "0"], "invalid port: '0' (use 1 to 65535)"),
        (["--path", "mcp"], "invalid path: 'mcp' (it must start with '/')"),
        (
            ["--transport", "sse"],
            "invalid choice: 'sse' (choose from stdio, http, stateless)",
        ),
        (
            ["--log-level", "LOUD"],
            "invalid choice: 'LOUD' (choose from DEBUG, INFO, WARNING, ERROR, CRITICAL)",
        ),
    ],
)
def test_usage_errors_state_the_problem_and_the_valid_values(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 2

    assert message in capsys.readouterr().err


def test_stdio_with_the_oauth_gate_is_refused_with_advice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_server: list[Settings]
) -> None:
    assert run(monkeypatch, [], AUTH_ENV) == 2

    assert capsys.readouterr().err == (
        "staan-mcp: error: STAAN_MCP_AUTH_* is set, but the OAuth gate only works with the http "
        "and stateless transports. stdio clients use environment credentials: unset the "
        "variables or choose --transport http.\n"
    )
    assert no_server == []


def test_non_loopback_host_without_access_control_is_refused_with_advice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_server: list[Settings]
) -> None:
    argv = ["--transport", "http", "--host", "10.0.0.5", "--allowed-host", "a.example.com"]

    assert run(monkeypatch, argv) == 2

    assert capsys.readouterr().err == (
        "staan-mcp: error: refusing to listen on non-loopback host '10.0.0.5' without access "
        "control. Configure the OAuth gate (STAAN_MCP_AUTH_*), or pass --insecure-no-auth if a "
        "trusted proxy authenticates every request.\n"
    )
    assert no_server == []


def test_non_loopback_host_without_allowed_hosts_is_refused_with_advice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_server: list[Settings]
) -> None:
    argv = ["--transport", "http", "--host", "10.0.0.5", "--insecure-no-auth"]

    assert run(monkeypatch, argv) == 2

    assert capsys.readouterr().err == (
        "staan-mcp: error: refusing to listen on non-loopback host '10.0.0.5' without an "
        "allowed Host header list. Pass --allowed-host (or STAAN_MCP_ALLOWED_HOSTS), for "
        "example 'mcp.example.com' and 'mcp.example.com:*'.\n"
    )
    assert no_server == []


def test_a_safe_configuration_has_no_startup_problem() -> None:
    http = HttpConfig(host="10.0.0.5", allowed_hosts=("mcp.example.com",), insecure_no_auth=True)

    assert cli._startup_problem("http", http, None) is None


def test_main_builds_the_server_from_the_environment_settings(
    monkeypatch: pytest.MonkeyPatch, no_server: list[Settings]
) -> None:
    assert run(monkeypatch, [], {"STAAN_TIMEOUT": "7"}) == 0

    assert len(no_server) == 1
    assert no_server[0].api_key == API_KEY
    assert no_server[0].timeout == 7


def test_missing_key_warning_tells_the_user_what_to_do(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, no_server: list[Settings]
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "")
    with caplog.at_level(logging.WARNING, logger="staan_mcp.cli"):
        assert cli.main([]) == 0

    messages = [r.getMessage() for r in caplog.records if r.name == "staan_mcp.cli"]
    assert messages == [
        (
            "STAAN_API_KEY is not set: tools will fail until it is. "
            "Create a key in the Staan console and export it."
        )
    ]
    assert len(no_server) == 1


def test_logging_goes_to_stderr_with_a_readable_format() -> None:
    root = logging.getLogger()
    root.handlers[:] = []  # basicConfig is a no-op while any handler is installed

    cli._configure_logging("INFO")

    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert handler.formatter is not None
    record = logging.LogRecord(
        "staan_mcp.x", logging.WARNING, "f.py", 1, "hello %s", ("you",), None
    )
    assert handler.formatter.format(record) == "WARNING staan_mcp.x: hello you"
    assert root.level == logging.INFO
