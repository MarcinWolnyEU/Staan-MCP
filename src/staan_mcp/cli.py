"""Command-line entry point: option parsing, startup safety rules and transport selection.

Every option has a ``STAAN_MCP_*`` environment default and a flag always wins over the
environment. The server refuses to start (exit status 2, message on stderr) in configurations
that would expose the Staan API key's quota: an OAuth gate on stdio, or a non-loopback HTTP bind
without access control or an allowed-host list. Logging goes to stderr only, because stdout is the
stdio transport's wire.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from mcp.server.transport_security import TransportSecuritySettings

from staan_mcp import __version__
from staan_mcp.config import AuthConfig, HttpConfig, Settings, Transport, is_loopback
from staan_mcp.errors import ConfigurationError
from staan_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp.server import MCPServer

__all__ = ["build_parser", "main", "run_server"]

_logger = logging.getLogger(__name__)

PROG: Final = "staan-mcp"
EXIT_USAGE: Final = 2
TRANSPORTS: Final[tuple[Transport, ...]] = ("stdio", "http", "stateless")
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LOG_LEVELS: Final[tuple[LogLevel, ...]] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LOOPBACK_HOSTS: Final = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOOPBACK_ORIGINS: Final = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
_LIST_SEPARATOR: Final = re.compile(r"[\s,]+")
_MAX_PORT: Final = 65535


# --------------------------------------------------------------------------------------
# Argument types
# --------------------------------------------------------------------------------------


def _transport(value: str) -> Transport:
    """Parse ``--transport`` (also applied to the environment default)."""
    name = value.strip().lower()
    for candidate in TRANSPORTS:
        if candidate == name:
            return candidate
    msg = f"invalid choice: {value!r} (choose from {', '.join(TRANSPORTS)})"
    raise argparse.ArgumentTypeError(msg)


def _log_level(value: str) -> LogLevel:
    """Parse ``--log-level`` case-insensitively."""
    name = value.strip().upper()
    for candidate in LOG_LEVELS:
        if candidate == name:
            return candidate
    msg = f"invalid choice: {value!r} (choose from {', '.join(LOG_LEVELS)})"
    raise argparse.ArgumentTypeError(msg)


def _port(value: str) -> int:
    """Parse ``--port``: an integer from 1 to 65535."""
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= _MAX_PORT:
        msg = f"invalid port: {value!r} (use 1 to {_MAX_PORT})"
        raise argparse.ArgumentTypeError(msg)
    return port


def _path(value: str) -> str:
    """Parse ``--path``: it must start with a slash."""
    if not value.startswith("/"):
        msg = f"invalid path: {value!r} (it must start with '/')"
        raise argparse.ArgumentTypeError(msg)
    return value


def _env_list(name: str) -> tuple[str, ...]:
    """Read a comma- or space-separated list from the environment."""
    raw = os.environ.get(name, "")
    return tuple(item for item in _LIST_SEPARATOR.split(raw) if item)


# --------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, reading the ``STAAN_MCP_*`` defaults from the environment.

    Returns:
        The parser. Defaults taken from the environment are validated like flag values.
    """
    env = os.environ.get
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="MCP server for the Staan API: web search and grounded answers.",
        epilog=(
            "The Staan API key is read from STAAN_API_KEY. Every option also has a STAAN_MCP_* "
            "environment variable (STAAN_MCP_TRANSPORT, _HOST, _PORT, _PATH, _ALLOWED_HOSTS, "
            "_ALLOWED_ORIGINS, _LOG_LEVEL); flags win."
        ),
    )
    parser.add_argument(
        "--transport",
        type=_transport,
        default=env("STAAN_MCP_TRANSPORT") or "stdio",
        metavar="{stdio,http,stateless}",
        help="transport to serve (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=env("STAAN_MCP_HOST") or "127.0.0.1",
        help="bind address for the HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=env("STAAN_MCP_PORT") or "8000",
        help="bind port for the HTTP transports (default: 8000)",
    )
    parser.add_argument(
        "--path",
        type=_path,
        default=env("STAAN_MCP_PATH") or "/mcp",
        help="URL path of the MCP endpoint (default: /mcp)",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        metavar="HOST",
        help="accepted Host header value; repeatable; env: STAAN_MCP_ALLOWED_HOSTS",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        metavar="ORIGIN",
        help="accepted Origin header value; repeatable; env: STAAN_MCP_ALLOWED_ORIGINS",
    )
    parser.add_argument(
        "--insecure-no-auth",
        action="store_true",
        help="allow a non-loopback HTTP bind without the OAuth gate (trusted proxy only)",
    )
    parser.add_argument(
        "--log-level",
        type=_log_level,
        default=env("STAAN_MCP_LOG_LEVEL") or "WARNING",
        metavar="{DEBUG,INFO,WARNING,ERROR,CRITICAL}",
        help="log level; logs go to stderr (default: WARNING)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _bind_host(host: str) -> str:
    """Normalise a bind address: trim, lowercase and unbracket (``[::1]`` becomes ``::1``)."""
    return host.strip().strip("[]").lower()


def _http_config(args: argparse.Namespace) -> HttpConfig:
    """Combine the parsed options and the list environment variables into an ``HttpConfig``."""
    return HttpConfig(
        host=_bind_host(args.host),
        port=args.port,
        path=args.path,
        allowed_hosts=tuple(args.allowed_host or _env_list("STAAN_MCP_ALLOWED_HOSTS")),
        allowed_origins=tuple(args.allowed_origin or _env_list("STAAN_MCP_ALLOWED_ORIGINS")),
        insecure_no_auth=args.insecure_no_auth,
    )


# --------------------------------------------------------------------------------------
# Startup rules
# --------------------------------------------------------------------------------------


def _startup_problem(transport: Transport, http: HttpConfig, auth: AuthConfig | None) -> str | None:
    """Check the safety rules for a transport and configuration.

    Args:
        transport: The selected transport.
        http: Binding and access-control options.
        auth: The OAuth gate, if configured.

    Returns:
        A message describing the first violated rule, or ``None`` if the combination is safe.
    """
    if transport == "stdio":
        if auth is not None:
            return (
                "STAAN_MCP_AUTH_* is set, but the OAuth gate only works with the http and "
                "stateless transports. stdio clients use environment credentials: unset the "
                "variables or choose --transport http."
            )
        return None
    if is_loopback(http.host):
        return None
    if auth is None and not http.insecure_no_auth:
        return (
            f"refusing to listen on non-loopback host {http.host!r} without access control. "
            "Configure the OAuth gate (STAAN_MCP_AUTH_*), or pass --insecure-no-auth if a "
            "trusted proxy authenticates every request."
        )
    if not http.allowed_hosts:
        return (
            f"refusing to listen on non-loopback host {http.host!r} without an allowed Host "
            "header list. Pass --allowed-host (or STAAN_MCP_ALLOWED_HOSTS), for example "
            "'mcp.example.com' and 'mcp.example.com:*'."
        )
    return None


def _configure_logging(level: str) -> None:
    """Send logs to stderr at ``level``.

    Called before the server is created, so the SDK's own ``basicConfig`` call (which would
    otherwise install its handler) finds a configured root logger and leaves it alone.
    """
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger().setLevel(level)


def _apply_log_level(server: MCPServer[Any], level: LogLevel) -> None:
    """Give the SDK the chosen level, which also sets the HTTP server's (uvicorn) log level."""
    server.settings.log_level = level


def _fail(message: str) -> int:
    """Print an error to stderr and return the usage exit status."""
    sys.stderr.write(f"{PROG}: error: {message}\n")
    return EXIT_USAGE


# --------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------


def _security_settings(http: HttpConfig) -> TransportSecuritySettings | None:
    """Build the Host and Origin allow-lists passed to the SDK.

    The SDK only switches its own DNS-rebinding protection on for the three exact spellings
    ``127.0.0.1``, ``localhost`` and ``::1``. :func:`~staan_mcp.config.is_loopback` accepts more
    (any ``127.0.0.0/8`` address, other cases of ``localhost``, long-form ``::1``), so a loopback
    bind always gets explicit settings here instead of relying on the SDK default.

    Returns:
        Explicit settings: the configured hosts and origins, with the loopback hosts as the
        default hosts and, if nothing at all was configured, the loopback origins. ``None`` only
        for a non-loopback bind with nothing configured, which :func:`main` refuses earlier.
    """
    if not (http.allowed_hosts or http.allowed_origins or is_loopback(http.host)):
        return None
    unconfigured = not (http.allowed_hosts or http.allowed_origins)
    return TransportSecuritySettings(
        allowed_hosts=list(http.allowed_hosts) or list(_LOOPBACK_HOSTS),
        allowed_origins=list(http.allowed_origins)
        or (list(_LOOPBACK_ORIGINS) if unconfigured else []),
    )


def run_server(server: MCPServer[Any], transport: Transport, http: HttpConfig) -> None:
    """Serve ``server`` over ``transport`` and block until it stops.

    Kept thin so tests can replace it.

    Args:
        server: The server from :func:`staan_mcp.server.create_server`.
        transport: ``stdio``, ``http`` (stateful) or ``stateless`` (no sessions, JSON replies).
        http: Binding and access-control options; ignored for stdio.
    """
    if transport == "stdio":
        server.run()
        return
    options: dict[str, Any] = {
        "host": http.host,
        "port": http.port,
        "streamable_http_path": http.path,
        "transport_security": _security_settings(http),
    }
    if transport == "stateless":
        options.update(stateless_http=True, json_response=True)
    server.run("streamable-http", **options)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace | int:
    """Parse the command line, returning an exit status instead of raising ``SystemExit``."""
    try:
        return build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(cast("int | None", exc.code) or 0)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``staan-mcp`` command.

    Args:
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit status: 0 on a clean stop (or ``--version``/``--help``), 2 for a usage
        or configuration error.
    """
    args = _parse(argv)
    if isinstance(args, int):
        return args
    _configure_logging(args.log_level)
    try:
        settings = Settings.from_env()
        auth = AuthConfig.from_env()
        http = _http_config(args)
        problem = _startup_problem(args.transport, http, auth)
        if problem is not None:
            return _fail(problem)
        if settings.api_key is None:
            _logger.warning(
                "STAAN_API_KEY is not set: tools will fail until it is. "
                "Create a key in the Staan console and export it."
            )
        server = create_server(settings, auth=auth)
    except ConfigurationError as exc:
        return _fail(str(exc))
    _apply_log_level(server, args.log_level)
    run_server(server, args.transport, http)
    return 0
