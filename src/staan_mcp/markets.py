"""Staan search markets and wildcard expansion.

Staan accepts exactly twelve markets (see :data:`MARKETS`). The ``web_search`` tool lets callers
pass one market, a list of markets, or fnmatch-style wildcards such as ``en-*`` which are
expanded against that fixed set here, before any request is made.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Final, NoReturn

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["DEFAULT_MARKET", "MARKETS", "InvalidMarketError", "expand_markets"]

MARKETS: Final[tuple[str, ...]] = (
    "fr-fr",
    "de-de",
    "en-us",
    "en-gb",
    "en-fr",
    "en-ca",
    "en-au",
    "en-in",
    "en-ie",
    "en-nz",
    "en-za",
    "en-sg",
)
"""Every market the Staan API accepts, in canonical (lowercase) form."""

DEFAULT_MARKET: Final = "en-gb"
"""Market used when the caller does not choose one (the API itself defaults to ``fr-fr``)."""

_WILDCARD_CHARS: Final = frozenset("*?[")


class InvalidMarketError(ValueError):
    """The market specification is empty, unknown or a wildcard that matches nothing."""


def _fail(problem: str) -> NoReturn:
    """Raise an error that explains ``problem`` and lists the valid markets and wildcards."""
    msg = (
        f"{problem} Valid markets: {', '.join(MARKETS)}. "
        "Wildcards are allowed: '*' (any characters), '?' (one character) and '[abc]' "
        "(a set), for example 'en-*' (all English markets) or '*-fr'."
    )
    raise InvalidMarketError(msg)


def _is_pattern(item: str) -> bool:
    """Tell whether ``item`` contains a wildcard character."""
    return not _WILDCARD_CHARS.isdisjoint(item)


def _expand_item(raw: str) -> list[str]:
    """Expand one market item to the matching entries of :data:`MARKETS`.

    Args:
        raw: A market code or a wildcard pattern, in any letter case.

    Returns:
        The matching markets, in :data:`MARKETS` order.

    Raises:
        InvalidMarketError: If the item is blank, an unknown code or matches nothing.
    """
    item = raw.strip().lower()
    if not item:
        _fail("A market must not be empty.")
    if _is_pattern(item):
        matches = [market for market in MARKETS if fnmatchcase(market, item)]
        if not matches:
            _fail(f"The market pattern {item!r} matches no market.")
        return matches
    if item not in MARKETS:
        _fail(f"Unknown market {item!r}.")
    return [item]


def expand_markets(spec: str | Sequence[str]) -> list[str]:
    """Resolve a market specification to a list of concrete markets.

    Each item is stripped and lowercased. An item containing ``*``, ``?`` or ``[`` is matched
    against :data:`MARKETS` with :func:`fnmatch.fnmatchcase`; any other item must be an exact
    member. The result keeps first-seen order and has no duplicates.

    Args:
        spec: One market or pattern, or a sequence of them.

    Returns:
        The distinct markets, for example ``["en-us", "en-gb"]``.

    Raises:
        InvalidMarketError: If ``spec`` is empty, contains an empty item or an unknown code, or
            if a pattern matches nothing. The message lists the valid markets and the wildcard
            syntax.
    """
    items = [spec] if isinstance(spec, str) else list(spec)
    if not items:
        _fail("At least one market is required.")
    expanded = (market for item in items for market in _expand_item(item))
    return list(dict.fromkeys(expanded))
