"""Normalisation of domain names for the Answer ``filter`` and search domain lists.

Staan expects bare domains (``example.com``): no scheme, path, port, credentials or ``www.``
prefix. :func:`normalize_domain` turns what people and models actually type, such as
``https://www.Example.com:443/a?b``, into that form or raises :class:`InvalidDomainError`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "MAX_DOMAINS",
    "InvalidDomainError",
    "normalize_domain",
    "normalize_domain_filters",
    "normalize_domains",
]

MAX_DOMAINS: Final = 10
"""The most domains Staan accepts in one ``include_domains`` or ``exclude_domains`` list."""

_SCHEME: Final = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_PATH_START: Final = re.compile(r"[/?#]")
_PORT: Final = re.compile(r":\d*$")
_LABEL: Final = re.compile(r"^[a-z0-9-]+$")
_WWW: Final = "www."


class InvalidDomainError(ValueError):
    """The value cannot be reduced to a bare domain name."""


def _invalid(value: str, reason: str) -> InvalidDomainError:
    """Build an error that names the offending ``value`` and explains ``reason``."""
    return InvalidDomainError(
        f"Invalid domain {value!r}: {reason}. Expected a bare domain such as 'example.com'."
    )


def _extract_host(value: str) -> str:
    """Reduce a URL-ish string to its lowercase host, steps 1 to 8 of the contract."""
    text = _SCHEME.sub("", value.strip(), count=1).removeprefix("//")
    authority = _PATH_START.split(text, maxsplit=1)[0]
    host = _PORT.sub("", authority.rpartition("@")[2]).lower().removesuffix(".")
    return host.removeprefix(_WWW)


def _to_ascii(host: str, value: str) -> str:
    """Convert an internationalised host name to punycode; ASCII hosts pass through."""
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid(value, "it is not a valid internationalised domain name") from exc


def _check_labels(host: str, value: str) -> None:
    """Check dots, label alphabet and hyphen placement.

    Raises:
        InvalidDomainError: If ``host`` is not a well-formed multi-label domain.
    """
    if "." not in host:
        raise _invalid(value, "it needs at least one dot")
    for label in host.split("."):
        if not label:
            raise _invalid(value, "it has an empty label")
        if not _LABEL.fullmatch(label):
            raise _invalid(value, "labels may only contain letters, digits and hyphens")
        if label.startswith("-") or label.endswith("-"):
            raise _invalid(value, "a label must not start or end with a hyphen")


def normalize_domain(value: str) -> str:
    """Reduce ``value`` to a bare, lowercase domain name.

    In order: strip whitespace, drop the URL scheme and a leading ``//``, drop everything from
    the first ``/``, ``?`` or ``#``, drop ``userinfo@`` and ``:port``, lowercase, strip a
    trailing dot, and remove one leading ``www.``. Internationalised names are converted to
    punycode, then the result is validated.

    Args:
        value: A domain or URL, for example ``"https://www.Example.com:443/a?b"``.

    Returns:
        The bare domain, for example ``"example.com"``. A subdomain is kept:
        ``"en.wikipedia.org"`` stays as it is.

    Raises:
        InvalidDomainError: If the result has no dot, an empty label, a character outside
            ``[a-z0-9-]``, or a label that starts or ends with ``-``.
    """
    host = _to_ascii(_extract_host(value), value)
    _check_labels(host, value)
    return host


def normalize_domains(values: Sequence[str]) -> list[str]:
    """Normalise every item of ``values`` and drop duplicates, keeping first-seen order.

    Args:
        values: Domains or URLs.

    Returns:
        The distinct bare domains.

    Raises:
        InvalidDomainError: If an item is invalid or more than :data:`MAX_DOMAINS` distinct
            domains remain.
    """
    domains = list(dict.fromkeys(normalize_domain(value) for value in values))
    if len(domains) > MAX_DOMAINS:
        msg = f"At most {MAX_DOMAINS} distinct domains are allowed, got {len(domains)}."
        raise InvalidDomainError(msg)
    return domains


def normalize_domain_filters(
    include_domains: Sequence[str] | None, exclude_domains: Sequence[str] | None
) -> tuple[list[str], list[str]]:
    """Normalise both domain lists of a search and enforce that at most one is used.

    Args:
        include_domains: Domains to restrict the results to, or ``None``.
        exclude_domains: Domains to remove from the results, or ``None``.

    Returns:
        The normalised ``(include, exclude)`` lists; a list that was not given is empty.

    Raises:
        InvalidDomainError: If both lists are non-empty, a domain is invalid or a list is too
            long.
    """
    if include_domains and exclude_domains:
        msg = "Use include_domains or exclude_domains, not both."
        raise InvalidDomainError(msg)
    return normalize_domains(include_domains or []), normalize_domains(exclude_domains or [])
