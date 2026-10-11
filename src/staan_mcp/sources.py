"""Validation of the caller-supplied sources of the ``answer`` tool.

Standard library only; shared by :mod:`staan_mcp.server` (as pydantic validators) and the
standalone ``stdio_server.py``. Every function raises :class:`ValueError` with a message that is
safe to show to the model.
"""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlsplit

from staan_mcp.contract import MAX_SOURCE_URL_LENGTH

__all__ = ["check_source_url", "require_url_or_content"]

_FIRST_PRINTABLE: Final = 32
_DELETE: Final = 127


def check_source_url(value: str) -> str | None:
    """Validate the URL of an answer source and return it stripped (``None`` if blank).

    Staan fetches these URLs, so a model that was talked into passing ``file:///etc/passwd`` or
    a cloud metadata address would turn the tool into a request forger. The check is a
    best-effort filter on the literal URL: http(s) only, a host, no whitespace or control
    characters, at most 2048 characters, and no literal private, loopback or link-local address.

    Raises:
        ValueError: If the URL is not acceptable.
    """
    url = value.strip()
    if not url:
        return None
    _check_url_text(url)
    _check_url_target(url)
    return url


def require_url_or_content(url: str | None, content: str | None) -> None:
    """Reject a source that carries neither a URL nor any content.

    Raises:
        ValueError: If both are missing or blank.
    """
    if not any(value and value.strip() for value in (url, content)):
        msg = "Each source needs a non-empty 'url' or 'content'."
        raise ValueError(msg)


def _check_url_text(url: str) -> None:
    """Reject a source URL that is too long or holds whitespace or control characters."""
    if len(url) > MAX_SOURCE_URL_LENGTH:
        msg = f"A source 'url' is limited to {MAX_SOURCE_URL_LENGTH} characters."
        raise ValueError(msg)
    if any(char.isspace() or ord(char) < _FIRST_PRINTABLE or ord(char) == _DELETE for char in url):
        msg = "A source 'url' must not contain whitespace or control characters."
        raise ValueError(msg)


def _check_url_target(url: str) -> None:
    """Reject a source URL that is not absolute http(s) with a host, or names a local host."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError as exc:
        msg = "A source 'url' is not a valid URL."
        raise ValueError(msg) from exc
    if parts.scheme.lower() not in {"http", "https"} or not host:
        msg = "A source 'url' must be an absolute http or https URL with a host."
        raise ValueError(msg)
    if _is_internal_host(host):
        msg = "A source 'url' must point to a public host, not a local or private address."
        raise ValueError(msg)


def _is_internal_host(host: str) -> bool:
    """Tell whether ``host`` is ``localhost`` or a literal private, loopback or reserved IP."""
    host = host.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global or address.is_multicast
