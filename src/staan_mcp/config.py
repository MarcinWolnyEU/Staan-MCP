"""Runtime configuration: environment-driven settings, OAuth gate settings and HTTP binding.

Everything here is a frozen dataclass. Parsing and validation happen in the ``from_env``
constructors, which raise :class:`~staan_mcp.errors.ConfigurationError` with a message that
names the offending environment variable (and never its secret value).
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from staan_mcp.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_BASE_URL",
    "AuthConfig",
    "HttpConfig",
    "Settings",
    "Transport",
    "is_loopback",
]

DEFAULT_BASE_URL: Final = "https://api.staan.ai/v2"
DEFAULT_TIMEOUT: Final = 30.0
DEFAULT_DEADLINE: Final = 50.0
DEFAULT_MAX_RETRIES: Final = 2
DEFAULT_RATE_LIMIT: Final = 20.0
"""Requests per second Staan allows an API key (the cap the limiter keeps to by default)."""
RATE_LIMIT_WINDOW: Final = 1.0
"""Seconds over which the rate limit is counted: a sliding window, not a refilling bucket."""
RATE_LIMIT_SAFETY_MARGIN: Final = 0.05
"""Seconds added to the window so jitter between booking a slot and the request reaching Staan
(a thread or task waking late, connection set-up, a coarse clock) cannot squeeze one request
too many into a second as Staan counts it. It costs about 5 % of throughput (19 requests per
second instead of 20 at the default); it is large next to the 1 ms to 16 ms jitter of the
platforms this runs on and small next to any request."""
MAX_RETRIES_LIMIT: Final = 10
DEFAULT_MAX_CONTENT_CHARS: Final = 0
MIN_MAX_CONTENT_CHARS: Final = 200
DEFAULT_ALGORITHMS: Final = ("RS256", "ES256")

ENV_API_KEY: Final = "STAAN_API_KEY"
ENV_BASE_URL: Final = "STAAN_BASE_URL"
ENV_TIMEOUT: Final = "STAAN_TIMEOUT"
ENV_DEADLINE: Final = "STAAN_DEADLINE"
ENV_MAX_RETRIES: Final = "STAAN_MAX_RETRIES"
ENV_RATE_LIMIT: Final = "STAAN_RATE_LIMIT"
ENV_MAX_CONTENT_CHARS: Final = "STAAN_MCP_MAX_CONTENT_CHARS"

ENV_AUTH_ISSUER_URL: Final = "STAAN_MCP_AUTH_ISSUER_URL"
ENV_AUTH_RESOURCE_URL: Final = "STAAN_MCP_AUTH_RESOURCE_URL"
ENV_AUTH_SCOPES: Final = "STAAN_MCP_AUTH_SCOPES"
ENV_AUTH_JWKS_URL: Final = "STAAN_MCP_AUTH_JWKS_URL"
ENV_AUTH_INTROSPECTION_URL: Final = "STAAN_MCP_AUTH_INTROSPECTION_URL"
ENV_AUTH_CLIENT_ID: Final = "STAAN_MCP_AUTH_CLIENT_ID"
ENV_AUTH_CLIENT_SECRET: Final = "STAAN_MCP_AUTH_CLIENT_SECRET"  # noqa: S105  (a variable name)
ENV_AUTH_ALGORITHMS: Final = "STAAN_MCP_AUTH_ALGORITHMS"

_AUTH_ENV_VARS: Final = (
    ENV_AUTH_ISSUER_URL,
    ENV_AUTH_RESOURCE_URL,
    ENV_AUTH_SCOPES,
    ENV_AUTH_JWKS_URL,
    ENV_AUTH_INTROSPECTION_URL,
    ENV_AUTH_CLIENT_ID,
    ENV_AUTH_CLIENT_SECRET,
    ENV_AUTH_ALGORITHMS,
)
_LIST_SEPARATOR: Final = re.compile(r"[\s,]+")
_VISIBLE_ASCII_START: Final = 0x21
_VISIBLE_ASCII_END: Final = 0x7E

Transport = Literal["stdio", "http", "stateless"]
"""The MCP transports this server can run."""


def is_loopback(host: str) -> bool:
    """Tell whether ``host`` only reaches the local machine.

    Args:
        host: A host name or IP literal, optionally an IPv6 literal in brackets.

    Returns:
        ``True`` for ``localhost``, any ``127.0.0.0/8`` address and ``::1``.
    """
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _env(environ: Mapping[str, str], name: str) -> str | None:
    """Return the stripped value of ``name``, or ``None`` if unset or blank."""
    return environ.get(name, "").strip() or None


def _validate_url(name: str, value: str) -> str:
    """Check that ``value`` is an absolute https URL (http only for loopback hosts).

    The messages name only the variable. They never repeat the value, because a URL can carry
    credentials (``https://user:secret@host``), which are rejected outright.

    Args:
        name: Environment variable name, used in the error message.
        value: The URL to validate.

    Returns:
        ``value`` unchanged.

    Raises:
        ConfigurationError: If the URL is malformed, has a bad port, carries credentials, or is
            not https (and not loopback).
    """
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port  # raises ValueError for a non-numeric or out-of-range port
    except ValueError as exc:
        msg = f"{name} is not a valid URL."
        raise ConfigurationError(msg) from exc
    if not host or parts.scheme not in {"http", "https"}:
        msg = f"{name} must be an absolute http(s) URL."
        raise ConfigurationError(msg)
    if parts.username is not None or parts.password is not None:
        msg = f"{name} must not contain credentials (user:password@)."
        raise ConfigurationError(msg)
    if parts.scheme != "https" and not is_loopback(host):
        msg = f"{name} must use https unless the host is loopback."
        raise ConfigurationError(msg)
    return value


def _parse_api_key(raw: str | None) -> str | None:
    """Check that ``STAAN_API_KEY`` can be sent as an HTTP header value.

    Args:
        raw: The stripped value, or ``None`` if unset.

    Returns:
        ``raw`` unchanged.

    Raises:
        ConfigurationError: If the key holds anything but visible ASCII characters (for example
            a pasted smart quote or an embedded space). The message never contains the key.
    """
    if raw is not None and not all(
        _VISIBLE_ASCII_START <= ord(char) <= _VISIBLE_ASCII_END for char in raw
    ):
        msg = (
            f"{ENV_API_KEY} contains characters that cannot be sent in an HTTP header. Use only "
            "visible ASCII characters, without spaces or quotes pasted from a document."
        )
        raise ConfigurationError(msg)
    return raw


def _parse_seconds(raw: str | None, name: str, default: float) -> float:
    """Parse a duration variable: a finite number of seconds greater than zero."""
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        msg = f"{name} must be a number of seconds, got {raw!r}."
        raise ConfigurationError(msg) from exc
    if not math.isfinite(value) or value <= 0:
        msg = f"{name} must be a finite number greater than 0, got {raw!r}."
        raise ConfigurationError(msg)
    return value


def _parse_max_content_chars(raw: str | None) -> int:
    """Parse ``STAAN_MCP_MAX_CONTENT_CHARS``: ``0`` (no limit) or at least 200."""
    if raw is None:
        return DEFAULT_MAX_CONTENT_CHARS
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{ENV_MAX_CONTENT_CHARS} must be an integer, got {raw!r}."
        raise ConfigurationError(msg) from exc
    if value != 0 and value < MIN_MAX_CONTENT_CHARS:
        msg = (
            f"{ENV_MAX_CONTENT_CHARS} must be 0 (no limit) or at least {MIN_MAX_CONTENT_CHARS}, "
            f"got {value}."
        )
        raise ConfigurationError(msg)
    return value


def _parse_max_retries(raw: str | None) -> int:
    """Parse ``STAAN_MAX_RETRIES``: an integer in ``0..MAX_RETRIES_LIMIT``."""
    if raw is None:
        return DEFAULT_MAX_RETRIES
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{ENV_MAX_RETRIES} must be an integer, got {raw!r}."
        raise ConfigurationError(msg) from exc
    if not 0 <= value <= MAX_RETRIES_LIMIT:
        msg = f"{ENV_MAX_RETRIES} must be between 0 and {MAX_RETRIES_LIMIT}, got {value}."
        raise ConfigurationError(msg)
    return value


def _parse_rate_limit(raw: str | None) -> float:
    """Parse ``STAAN_RATE_LIMIT``: requests per second, a finite number, ``0`` for no limit."""
    if raw is None:
        return DEFAULT_RATE_LIMIT
    try:
        value = float(raw)
    except ValueError as exc:
        msg = f"{ENV_RATE_LIMIT} must be a number of requests per second, got {raw!r}."
        raise ConfigurationError(msg) from exc
    if not math.isfinite(value) or value < 0:
        msg = (
            f"{ENV_RATE_LIMIT} must be a finite number of requests per second, "
            f"0 (no limit) or greater, got {raw!r}."
        )
        raise ConfigurationError(msg)
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Upstream API settings.

    Attributes:
        api_key: The Staan API key, or ``None`` if not configured. Hidden from ``repr``.
        base_url: Base URL of the Staan API, without a trailing slash.
        timeout: Per-attempt timeout in seconds (``full_content`` fetches can take a few seconds
            and answers longer).
        max_retries: How many times 429, 5xx and connection errors are retried. A read timeout
            is never retried: the request was already sent and is billable.
        deadline: Overall limit in seconds for one call, retries and waits included. It stays
            below the 60 s many MCP clients allow, so the model gets a clean tool error.
        max_content_chars: Longest ``full_content`` text kept per search result; longer pages
            are cut at a paragraph boundary with a visible marker. ``0`` means no limit.
        rate_limit: Most upstream requests the server starts in any second, retries included,
            all tools and markets together; later ones wait. ``0`` turns the limit off. See
            :mod:`staan_mcp.ratelimit`.
    """

    api_key: str | None = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    deadline: float = DEFAULT_DEADLINE
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS
    rate_limit: float = DEFAULT_RATE_LIMIT

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``STAAN_API_KEY``, ``STAAN_BASE_URL``, ``STAAN_TIMEOUT``, ...

        Args:
            environ: Mapping to read from; defaults to ``os.environ``.

        Returns:
            The validated settings.

        Raises:
            ConfigurationError: If a value is invalid. A missing API key is not an error
                here; clients raise it on first use so tool listing works without a key.
        """
        env = os.environ if environ is None else environ
        base_url = _env(env, ENV_BASE_URL) or DEFAULT_BASE_URL
        return cls(
            api_key=_parse_api_key(_env(env, ENV_API_KEY)),
            base_url=_validate_url(ENV_BASE_URL, base_url).rstrip("/"),
            timeout=_parse_seconds(_env(env, ENV_TIMEOUT), ENV_TIMEOUT, DEFAULT_TIMEOUT),
            max_retries=_parse_max_retries(_env(env, ENV_MAX_RETRIES)),
            deadline=_parse_seconds(_env(env, ENV_DEADLINE), ENV_DEADLINE, DEFAULT_DEADLINE),
            max_content_chars=_parse_max_content_chars(_env(env, ENV_MAX_CONTENT_CHARS)),
            rate_limit=_parse_rate_limit(_env(env, ENV_RATE_LIMIT)),
        )


def _split_list(raw: str | None) -> tuple[str, ...]:
    """Split a space- or comma-separated list, dropping empty items."""
    if raw is None:
        return ()
    return tuple(item for item in _LIST_SEPARATOR.split(raw) if item)


def _parse_algorithms(raw: str | None) -> tuple[str, ...]:
    """Parse the JWT algorithm allow-list, refusing the unsigned ``none`` algorithm."""
    algorithms = _split_list(raw) or DEFAULT_ALGORITHMS
    if any(algorithm.lower() == "none" for algorithm in algorithms):
        msg = f"{ENV_AUTH_ALGORITHMS} must not allow the 'none' algorithm."
        raise ConfigurationError(msg)
    return algorithms


def _require(value: str | None, name: str) -> str:
    """Return ``value`` or raise a ``ConfigurationError`` naming the missing variable."""
    if value is None:
        msg = f"{name} is required when any STAAN_MCP_AUTH_* variable is set."
        raise ConfigurationError(msg)
    return value


def _check_token_source(
    jwks_url: str | None,
    introspection_url: str | None,
    client_id: str | None,
    client_secret: str | None,
) -> None:
    """Enforce: exactly one verification method, and credentials for introspection."""
    if (jwks_url is None) == (introspection_url is None):
        msg = (
            f"Set exactly one of {ENV_AUTH_JWKS_URL} (JWT verification) or "
            f"{ENV_AUTH_INTROSPECTION_URL} (token introspection)."
        )
        raise ConfigurationError(msg)
    if introspection_url is not None and (client_id is None or client_secret is None):
        msg = (
            f"{ENV_AUTH_INTROSPECTION_URL} requires both {ENV_AUTH_CLIENT_ID} and "
            f"{ENV_AUTH_CLIENT_SECRET}."
        )
        raise ConfigurationError(msg)


def _optional_url(name: str, value: str | None) -> str | None:
    """Validate ``value`` as a URL if it is set."""
    return None if value is None else _validate_url(name, value)


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """OAuth 2.1 resource-server gate for the HTTP transports.

    Attributes:
        issuer_url: Issuer of accepted tokens (the ``iss`` claim).
        resource_url: Canonical URL of this MCP server, also the expected audience.
        required_scopes: Scopes every token must carry.
        jwks_url: JWKS endpoint for JWT verification (exclusive with ``introspection_url``).
        introspection_url: RFC 7662 endpoint (exclusive with ``jwks_url``).
        client_id: Client id for introspection (HTTP Basic client authentication).
        client_secret: Client secret for introspection. Hidden from ``repr``.
        algorithms: Accepted JWT signature algorithms; ``none`` is never allowed.
    """

    issuer_url: str
    resource_url: str
    required_scopes: tuple[str, ...] = ()
    jwks_url: str | None = None
    introspection_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthConfig | None:
        """Build the gate configuration from ``STAAN_MCP_AUTH_*`` variables.

        Args:
            environ: Mapping to read from; defaults to ``os.environ``.

        Returns:
            The validated configuration, or ``None`` if no ``STAAN_MCP_AUTH_*`` variable is
            set (the gate is then disabled).

        Raises:
            ConfigurationError: If the configuration is partial or invalid.
        """
        env = os.environ if environ is None else environ
        if all(_env(env, name) is None for name in _AUTH_ENV_VARS):
            return None
        issuer = _require(_env(env, ENV_AUTH_ISSUER_URL), ENV_AUTH_ISSUER_URL)
        resource = _require(_env(env, ENV_AUTH_RESOURCE_URL), ENV_AUTH_RESOURCE_URL)
        jwks_url = _optional_url(ENV_AUTH_JWKS_URL, _env(env, ENV_AUTH_JWKS_URL))
        introspection_url = _optional_url(
            ENV_AUTH_INTROSPECTION_URL, _env(env, ENV_AUTH_INTROSPECTION_URL)
        )
        client_id = _env(env, ENV_AUTH_CLIENT_ID)
        client_secret = _env(env, ENV_AUTH_CLIENT_SECRET)
        _check_token_source(jwks_url, introspection_url, client_id, client_secret)
        return cls(
            issuer_url=_validate_url(ENV_AUTH_ISSUER_URL, issuer),
            resource_url=_validate_url(ENV_AUTH_RESOURCE_URL, resource),
            required_scopes=_split_list(_env(env, ENV_AUTH_SCOPES)),
            jwks_url=jwks_url,
            introspection_url=introspection_url,
            client_id=client_id,
            client_secret=client_secret,
            algorithms=_parse_algorithms(_env(env, ENV_AUTH_ALGORITHMS)),
        )


@dataclass(frozen=True, slots=True)
class HttpConfig:
    """Binding and protection settings for the HTTP transports.

    Attributes:
        host: Interface to bind; loopback by default.
        port: TCP port to listen on.
        path: URL path of the MCP endpoint.
        allowed_hosts: Accepted ``Host`` header values (DNS-rebinding protection).
        allowed_origins: Accepted ``Origin`` header values.
        insecure_no_auth: Explicit opt-in to serve a non-loopback host without OAuth.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    insecure_no_auth: bool = False
