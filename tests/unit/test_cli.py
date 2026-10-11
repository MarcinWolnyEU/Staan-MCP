"""Tests for the command-line entry point: parsing, startup rules and transport selection."""
# pylint: disable=redefined-outer-name,protected-access

from __future__ import annotations

import logging
import os
import runpy
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from staan_mcp import __version__, cli
from staan_mcp.config import HttpConfig
from staan_mcp.contract import SERVER_NAME
from staan_mcp.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from staan_mcp.config import Transport

API_KEY = "test-key-0123456789"
STAAN_MCP_VARIABLES = (
    "STAAN_MCP_TRANSPORT",
    "STAAN_MCP_HOST",
    "STAAN_MCP_PORT",
    "STAAN_MCP_PATH",
    "STAAN_MCP_ALLOWED_HOSTS",
    "STAAN_MCP_ALLOWED_ORIGINS",
    "STAAN_MCP_LOG_LEVEL",
)
LOOPBACK_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOOPBACK_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
AUTH_ENV = {
    "STAAN_MCP_AUTH_ISSUER_URL": "https://auth.example.com",
    "STAAN_MCP_AUTH_RESOURCE_URL": "https://mcp.example.com/mcp",
    "STAAN_MCP_AUTH_JWKS_URL": "https://auth.example.com/jwks",
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every ``STAAN_*`` variable and restore the root logger afterwards."""
    for name in [name for name in os.environ if name.startswith("STAAN_")]:
        monkeypatch.delenv(name)
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


@dataclass
class Launch:
    """One recorded call of :func:`staan_mcp.cli.run_server`."""

    server: MCPServer[Any]
    transport: Transport
    http: HttpConfig


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> list[Launch]:
    """Replace ``run_server`` with a recorder, so ``main`` never blocks."""
    recorded: list[Launch] = []

    def record(server: MCPServer[Any], transport: Transport, http: HttpConfig) -> None:
        recorded.append(Launch(server, transport, http))

    monkeypatch.setattr(cli, "run_server", record)
    return recorded


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    env: dict[str, str] | None = None,
) -> int:
    """Run ``main`` with ``env`` set (on top of a valid API key)."""
    for name, value in {"STAAN_API_KEY": API_KEY, **(env or {})}.items():
        monkeypatch.setenv(name, value)
    return cli.main(argv)


# --------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------


def test_parser_defaults() -> None:
    args = cli.build_parser().parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.path == "/mcp"
    assert args.allowed_host is None
    assert args.allowed_origin is None
    assert args.insecure_no_auth is False
    assert args.log_level == "WARNING"


def test_parser_reads_defaults_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAAN_MCP_TRANSPORT", "stateless")
    monkeypatch.setenv("STAAN_MCP_HOST", "10.1.2.3")
    monkeypatch.setenv("STAAN_MCP_PORT", "9001")
    monkeypatch.setenv("STAAN_MCP_PATH", "/staan")
    monkeypatch.setenv("STAAN_MCP_LOG_LEVEL", "debug")

    args = cli.build_parser().parse_args([])

    assert args.transport == "stateless"
    assert args.host == "10.1.2.3"
    assert args.port == 9001
    assert args.path == "/staan"
    assert args.log_level == "DEBUG"


def test_parser_flags_win_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAAN_MCP_TRANSPORT", "stateless")
    monkeypatch.setenv("STAAN_MCP_PORT", "9001")

    args = cli.build_parser().parse_args(["--transport", "http", "--port", "9100"])

    assert args.transport == "http"
    assert args.port == 9100


def test_parser_treats_blank_environment_values_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in STAAN_MCP_VARIABLES:
        monkeypatch.setenv(name, "")

    args = cli.build_parser().parse_args([])

    assert (args.transport, args.host, args.port, args.path, args.log_level) == (
        "stdio",
        "127.0.0.1",
        8000,
        "/mcp",
        "WARNING",
    )


@pytest.mark.parametrize("value", ["http", "HTTP", " Http "])
def test_transport_is_case_insensitive(value: str) -> None:
    assert cli.build_parser().parse_args(["--transport", value]).transport == "http"


@pytest.mark.parametrize("value", ["info", "INFO", "Info"])
def test_log_level_is_case_insensitive(value: str) -> None:
    assert cli.build_parser().parse_args(["--log-level", value]).log_level == "INFO"


@pytest.mark.parametrize(
    "argv",
    [
        ["--transport", "sse"],
        ["--transport", ""],
        ["--port", "0"],
        ["--port", "65536"],
        ["--port", "-1"],
        ["--port", "eighty"],
        ["--path", "mcp"],
        ["--log-level", "LOUD"],
        ["--unknown-flag"],
        ["--allowed-host"],
    ],
)
def test_invalid_arguments_exit_with_status_2_and_name_the_problem(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 2

    err = capsys.readouterr().err
    assert err.startswith("usage: staan-mcp")
    assert "error:" in err


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STAAN_MCP_TRANSPORT", "sse"),
        ("STAAN_MCP_PORT", "abc"),
        ("STAAN_MCP_PORT", "70000"),
        ("STAAN_MCP_PATH", "mcp"),
        ("STAAN_MCP_LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_environment_defaults_are_rejected_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    assert cli.main([]) == 2
    assert "invalid" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "port"), [(["--port", "1"], 1), (["--port", "65535"], 65535), (["--port", " 80 "], 80)]
)
def test_port_boundaries(argv: list[str], port: int) -> None:
    assert cli.build_parser().parse_args(argv).port == port


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0

    assert capsys.readouterr().out == f"staan-mcp {__version__}\n"


def test_help_lists_every_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0

    out = capsys.readouterr().out
    for option in (
        "--transport",
        "--host",
        "--port",
        "--path",
        "--allowed-host",
        "--allowed-origin",
        "--insecure-no-auth",
        "--log-level",
        "--version",
        "STAAN_API_KEY",
    ):
        assert option in out


# --------------------------------------------------------------------------------------
# main(): what gets launched
# --------------------------------------------------------------------------------------


def test_default_run_serves_stdio(monkeypatch: pytest.MonkeyPatch, launches: list[Launch]) -> None:
    assert run_main(monkeypatch, []) == 0

    (launch,) = launches
    assert launch.transport == "stdio"
    assert isinstance(launch.server, MCPServer)
    assert launch.server.name == SERVER_NAME
    assert launch.http == HttpConfig()


@pytest.mark.parametrize("transport", ["stdio", "http", "stateless"])
def test_main_passes_the_selected_transport(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch], transport: str
) -> None:
    assert run_main(monkeypatch, ["--transport", transport]) == 0

    assert [launch.transport for launch in launches] == [transport]


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_main_gives_the_sdk_the_log_level_so_the_http_server_honours_it(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch], level: str
) -> None:
    assert run_main(monkeypatch, ["--log-level", level.lower()]) == 0

    assert launches[0].server.settings.log_level == level


def test_the_default_log_level_is_warning_for_the_sdk_too(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    assert run_main(monkeypatch, []) == 0

    assert launches[0].server.settings.log_level == "WARNING"


def test_main_builds_the_http_config_from_flags(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    argv = [
        "--transport",
        "http",
        "--host",
        "0.0.0.0",  # noqa: S104
        "--port",
        "9000",
        "--path",
        "/staan",
        "--allowed-host",
        "mcp.example.com",
        "--allowed-host",
        "mcp.example.com:*",
        "--allowed-origin",
        "https://app.example.com",
        "--insecure-no-auth",
    ]

    assert run_main(monkeypatch, argv) == 0

    assert launches[0].http == HttpConfig(
        host="0.0.0.0",  # noqa: S104
        port=9000,
        path="/staan",
        allowed_hosts=("mcp.example.com", "mcp.example.com:*"),
        allowed_origins=("https://app.example.com",),
        insecure_no_auth=True,
    )


def test_allowed_lists_come_from_the_environment_when_no_flag_is_given(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    env = {
        "STAAN_MCP_ALLOWED_HOSTS": "a.example.com, b.example.com c.example.com,,",
        "STAAN_MCP_ALLOWED_ORIGINS": "https://a.example.com,https://b.example.com",
    }

    assert run_main(monkeypatch, ["--transport", "http"], env) == 0

    assert launches[0].http.allowed_hosts == ("a.example.com", "b.example.com", "c.example.com")
    assert launches[0].http.allowed_origins == ("https://a.example.com", "https://b.example.com")


def test_allowed_flags_replace_the_environment_lists(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    env = {"STAAN_MCP_ALLOWED_HOSTS": "env.example.com", "STAAN_MCP_ALLOWED_ORIGINS": "https://e"}
    argv = ["--transport", "http", "--allowed-host", "flag.example.com", "--allowed-origin", "o"]

    assert run_main(monkeypatch, argv, env) == 0

    assert launches[0].http.allowed_hosts == ("flag.example.com",)
    assert launches[0].http.allowed_origins == ("o",)


def test_main_uses_the_environment_for_server_options(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    env = {"STAAN_MCP_TRANSPORT": "stateless", "STAAN_MCP_PORT": "8123", "STAAN_MCP_PATH": "/x"}

    assert run_main(monkeypatch, [], env) == 0

    assert launches[0].transport == "stateless"
    assert (launches[0].http.port, launches[0].http.path) == (8123, "/x")


def test_the_oauth_gate_is_wired_into_http_servers(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    env = {**AUTH_ENV, "STAAN_MCP_AUTH_SCOPES": "staan"}

    assert run_main(monkeypatch, ["--transport", "http"], env) == 0

    auth = launches[0].server.settings.auth
    assert auth is not None
    assert auth.required_scopes == ["staan"]
    assert str(auth.resource_server_url) == "https://mcp.example.com/mcp"


def test_no_oauth_gate_without_auth_variables(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    assert run_main(monkeypatch, ["--transport", "http"]) == 0

    assert launches[0].server.settings.auth is None


def test_main_configures_logging_to_the_requested_level(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    assert run_main(monkeypatch, ["--log-level", "debug"]) == 0
    assert logging.getLogger().level == logging.DEBUG

    assert run_main(monkeypatch, []) == 0
    assert logging.getLogger().level == logging.WARNING
    assert len(launches) == 2


# --------------------------------------------------------------------------------------
# Startup rules
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.5"])
@pytest.mark.parametrize("transport", ["http", "stateless"])
def test_loopback_hosts_need_no_access_control(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch], host: str, transport: str
) -> None:
    assert run_main(monkeypatch, ["--transport", transport, "--host", host]) == 0

    assert len(launches) == 1


def test_stdio_rejects_the_oauth_gate(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_main(monkeypatch, [], AUTH_ENV) == 2

    err = capsys.readouterr().err
    assert err.startswith("staan-mcp: error: ")
    assert "STAAN_MCP_AUTH_*" in err
    assert "stdio" in err
    assert launches == []


@pytest.mark.parametrize("transport", ["http", "stateless"])
def test_non_loopback_host_requires_access_control(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    capsys: pytest.CaptureFixture[str],
    transport: str,
) -> None:
    argv = ["--transport", transport, "--host", "0.0.0.0", "--allowed-host", "a.example.com"]  # noqa: S104

    assert run_main(monkeypatch, argv) == 2

    err = capsys.readouterr().err
    assert err.startswith("staan-mcp: error: ")
    assert "without access control" in err
    assert "--insecure-no-auth" in err
    assert "'0.0.0.0'" in err
    assert launches == []


@pytest.mark.parametrize("host", ["localhostX", "XLOCALHOST", "127.0.0.1X", "X[::1]X"])
def test_a_host_that_only_looks_like_loopback_requires_access_control(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch], host: str
) -> None:
    assert run_main(monkeypatch, ["--transport", "http", "--host", host]) == 2

    assert launches == []


def test_non_loopback_host_requires_an_allowed_host_even_with_the_gate(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_main(monkeypatch, ["--transport", "http", "--host", "0.0.0.0"], AUTH_ENV) == 2  # noqa: S104

    err = capsys.readouterr().err
    assert "allowed Host header list" in err
    assert "--allowed-host" in err
    assert launches == []


def test_non_loopback_host_requires_an_allowed_host_even_with_insecure_no_auth(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--transport", "http", "--host", "10.0.0.5", "--insecure-no-auth"]

    assert run_main(monkeypatch, argv) == 2

    assert "allowed Host header list" in capsys.readouterr().err
    assert launches == []


def test_non_loopback_host_is_accepted_with_the_gate_and_an_allowed_host(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    argv = ["--transport", "http", "--host", "0.0.0.0", "--allowed-host", "mcp.example.com"]  # noqa: S104

    assert run_main(monkeypatch, argv, AUTH_ENV) == 0

    assert len(launches) == 1


def test_non_loopback_host_is_accepted_with_insecure_no_auth_and_an_allowed_host(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    argv = [
        "--transport",
        "stateless",
        "--host",
        "0.0.0.0",  # noqa: S104
        "--allowed-host",
        "mcp.example.com",
        "--insecure-no-auth",
    ]

    assert run_main(monkeypatch, argv) == 0

    assert launches[0].http.insecure_no_auth is True


def test_stdio_ignores_the_http_binding_options(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    assert run_main(monkeypatch, ["--host", "0.0.0.0"]) == 0  # noqa: S104

    assert launches[0].transport == "stdio"


# --------------------------------------------------------------------------------------
# API key warning and configuration errors
# --------------------------------------------------------------------------------------


def test_missing_api_key_only_warns(
    launches: list[Launch], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="staan_mcp.cli"):
        assert cli.main([]) == 0

    assert len(launches) == 1
    messages = [r.getMessage() for r in caplog.records if r.name == "staan_mcp.cli"]
    assert len(messages) == 1
    assert "STAAN_API_KEY is not set" in messages[0]
    assert caplog.records[-1].levelno == logging.WARNING


def test_a_configured_api_key_means_no_warning_and_is_never_logged(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert run_main(monkeypatch, ["--log-level", "DEBUG"]) == 0

    assert len(launches) == 1
    assert "STAAN_API_KEY" not in caplog.text
    assert API_KEY not in caplog.text
    captured = capsys.readouterr()
    assert API_KEY not in captured.out + captured.err


def test_a_blank_api_key_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "   ")

    with caplog.at_level(logging.WARNING, logger="staan_mcp.cli"):
        assert cli.main([]) == 0

    assert "STAAN_API_KEY is not set" in caplog.text
    assert len(launches) == 1


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"STAAN_TIMEOUT": "soon"}, "STAAN_TIMEOUT must be a number of seconds"),
        ({"STAAN_TIMEOUT": "0"}, "STAAN_TIMEOUT"),
        ({"STAAN_MAX_RETRIES": "99"}, "STAAN_MAX_RETRIES must be between 0 and 10"),
        ({"STAAN_BASE_URL": "http://api.example.com"}, "STAAN_BASE_URL must use https"),
        ({"STAAN_MCP_AUTH_ISSUER_URL": "https://auth.example.com"}, "STAAN_MCP_AUTH_RESOURCE_URL"),
    ],
)
def test_configuration_errors_print_the_message_and_exit_2(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    expected: str,
) -> None:
    assert run_main(monkeypatch, ["--transport", "http"], env) == 2

    err = capsys.readouterr().err
    assert err.startswith("staan-mcp: error: ")
    assert expected in err
    assert err.count("\n") == 1
    assert launches == []


def test_a_configuration_error_while_building_the_server_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    launches: list[Launch],
    capsys: pytest.CaptureFixture[str],
) -> None:
    def explode(*_args: object, **_kwargs: object) -> MCPServer[Any]:
        msg = "install staan-mcp[oauth]"
        raise ConfigurationError(msg)

    monkeypatch.setattr(cli, "create_server", explode)

    assert run_main(monkeypatch, []) == 2

    assert capsys.readouterr().err == "staan-mcp: error: install staan-mcp[oauth]\n"
    assert launches == []


# --------------------------------------------------------------------------------------
# run_server
# --------------------------------------------------------------------------------------


@dataclass
class FakeServer:
    """Records calls of ``MCPServer.run``."""

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    def run(self, *args: Any, **kwargs: Any) -> None:
        """Record the call instead of serving."""
        self.calls.append((args, kwargs))


def serve(transport: Transport, http: HttpConfig) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Run ``run_server`` against a fake server and return its single ``run`` call."""
    fake = FakeServer()
    cli.run_server(cast("MCPServer[Any]", fake), transport, http)
    assert len(fake.calls) == 1
    return fake.calls[0]


def test_run_server_stdio_runs_the_default_transport() -> None:
    assert serve("stdio", HttpConfig(host="0.0.0.0", port=1)) == ((), {})  # noqa: S104


def test_run_server_http_uses_stateful_streamable_http() -> None:
    http = HttpConfig(host="localhost", port=8123, path="/staan")

    args, kwargs = serve("http", http)

    assert args == ("streamable-http",)
    security = kwargs.pop("transport_security")
    assert kwargs == {"host": "localhost", "port": 8123, "streamable_http_path": "/staan"}
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == LOOPBACK_HOSTS
    assert security.allowed_origins == LOOPBACK_ORIGINS


def test_run_server_stateless_adds_stateless_and_json_response() -> None:
    args, kwargs = serve("stateless", HttpConfig(port=9000))

    assert args == ("streamable-http",)
    security = kwargs.pop("transport_security")
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 9000,
        "streamable_http_path": "/mcp",
        "stateless_http": True,
        "json_response": True,
    }
    assert security.allowed_hosts == LOOPBACK_HOSTS


def test_run_server_passes_allowed_hosts_and_origins_to_the_sdk() -> None:
    http = HttpConfig(
        host="0.0.0.0",  # noqa: S104
        allowed_hosts=("mcp.example.com", "mcp.example.com:*"),
        allowed_origins=("https://app.example.com",),
    )

    _, kwargs = serve("http", http)

    security = kwargs["transport_security"]
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["mcp.example.com", "mcp.example.com:*"]
    assert security.allowed_origins == ["https://app.example.com"]


def test_run_server_keeps_loopback_hosts_when_only_origins_are_configured() -> None:
    http = HttpConfig(allowed_origins=("https://app.example.com",))

    _, kwargs = serve("stateless", http)

    security = kwargs["transport_security"]
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    assert security.allowed_origins == ["https://app.example.com"]


def test_run_server_without_origins_allows_no_cross_origin_requests() -> None:
    _, kwargs = serve("http", HttpConfig(allowed_hosts=("mcp.example.com",)))

    assert kwargs["transport_security"].allowed_origins == []


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "::1",
        "LOCALHOST",
        "[::1]",
        "127.0.0.2",
        "127.255.255.254",
        "0:0:0:0:0:0:0:1",
        " localhost",
    ],
)
def test_security_settings_protect_every_loopback_spelling(host: str) -> None:
    # The SDK only auto-protects the exact strings 127.0.0.1, localhost and ::1, so every other
    # spelling that is_loopback() accepts needs explicit settings (DNS-rebinding protection).
    security = cli._security_settings(HttpConfig(host=host))

    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == LOOPBACK_HOSTS
    assert security.allowed_origins == LOOPBACK_ORIGINS


def test_security_settings_are_none_for_a_non_loopback_bind_with_nothing_configured() -> None:
    # main() refuses this combination before it gets here; the helper stays honest about it.
    assert cli._security_settings(HttpConfig(host="0.0.0.0")) is None  # noqa: S104


@pytest.mark.parametrize("host", ["localhostX", "XLOCALHOST", "127.0.0.1X", "X[::1]X"])
def test_security_settings_do_not_treat_a_look_alike_host_as_loopback(host: str) -> None:
    assert cli._security_settings(HttpConfig(host=host)) is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("LOCALHOST", "localhost"),
        ("  localhost ", "localhost"),
        ("[::1]", "::1"),
        ("[FE80::1]", "fe80::1"),
        # Characters that merely look like what is stripped must stay, so the host is not read
        # as loopback (a `strip("XX[]XX")` mutant would turn the second into `::1`).
        ("localhostX", "localhostx"),
        ("X[::1]X", "x[::1]x"),
    ],
)
def test_bind_host_is_normalised(given: str, expected: str) -> None:
    assert cli._bind_host(given) == expected


def test_main_passes_the_normalised_bind_host_to_the_server(
    monkeypatch: pytest.MonkeyPatch, launches: list[Launch]
) -> None:
    assert run_main(monkeypatch, ["--transport", "http", "--host", "LOCALHOST"]) == 0

    assert launches[0].http.host == "localhost"


def test_main_runs_the_real_run_server_with_the_built_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(_self: MCPServer[Any], *args: Any, **kwargs: Any) -> None:
        ran.append((args, kwargs))

    monkeypatch.setattr(MCPServer, "run", fake_run)

    assert run_main(monkeypatch, ["--transport", "stateless", "--port", "8555"]) == 0

    assert ran == [
        (
            ("streamable-http",),
            {
                "host": "127.0.0.1",
                "port": 8555,
                "streamable_http_path": "/mcp",
                "transport_security": TransportSecuritySettings(
                    allowed_hosts=LOOPBACK_HOSTS, allowed_origins=LOOPBACK_ORIGINS
                ),
                "stateless_http": True,
                "json_response": True,
            },
        )
    ]


# --------------------------------------------------------------------------------------
# python -m staan_mcp
# --------------------------------------------------------------------------------------


def test_python_dash_m_runs_main_and_exits_with_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "main", lambda: 7)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("staan_mcp", run_name="__main__")

    assert exit_info.value.code == 7


def test_python_dash_m_with_version_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["staan-mcp", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("staan_mcp", run_name="__main__")

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"staan-mcp {__version__}\n"
