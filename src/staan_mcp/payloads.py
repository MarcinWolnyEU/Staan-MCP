"""What a tool returns, built from raw upstream responses.

Standard library only. :mod:`staan_mcp.server` and the standalone ``stdio_server.py`` fetch the
upstream responses in their own way (asynchronously and with threads) and then hand them to this
module, so the JSON a client receives is shaped by one piece of code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from staan_mcp.errors import StaanAPIError, StaanMCPError, StaanResponseError, is_fatal
from staan_mcp.formatting import (
    citations_to_gfm,
    interleave_results,
    simplify_result,
    truncate_text,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Outcome",
    "answer_payload",
    "raise_for_failures",
    "search_payload",
]

Outcome = dict[str, Any] | StaanMCPError
"""What one market of a search ended with: the raw response, or the error that stopped it."""


def _failures(
    markets: Sequence[str], outcomes: Sequence[Outcome]
) -> list[tuple[str, StaanMCPError]]:
    """Pair every market that ended in an error with that error."""
    return [
        (market, outcome)
        for market, outcome in zip(markets, outcomes, strict=True)
        if isinstance(outcome, StaanMCPError)
    ]


def raise_for_failures(markets: Sequence[str], outcomes: Sequence[Outcome]) -> list[str]:
    """Raise if the search failed fatally or in every market.

    Args:
        markets: The concrete markets, in request order.
        outcomes: One outcome per market, in the same order.

    Returns:
        The markets that failed while others succeeded, so the caller can log a warning.

    Raises:
        StaanMCPError: A fatal outcome (key or configuration problem) as it is, or, if every
            market failed, one :class:`StaanAPIError` that lists the failures per market.
    """
    fatal = next((o for o in outcomes if isinstance(o, StaanMCPError) and is_fatal(o)), None)
    if fatal is not None:
        raise fatal
    failed = _failures(markets, outcomes)
    if len(failed) == len(markets):
        msg = "; ".join(f"{market}: {error}" for market, error in failed)
        raise StaanAPIError(msg, endpoint="search")
    return [market for market, _ in failed]


def _raw_results(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Extract ``web.results`` from a raw search response, tolerating a malformed body."""
    web = response.get("web")
    results = web.get("results") if isinstance(web, Mapping) else None
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, Mapping)]


def _limit_raw_content(response: dict[str, Any], limit: int) -> None:
    """Shorten every ``full_content.text`` of a raw response in place (``0`` means no limit)."""
    for item in _raw_results(response):
        content = item.get("full_content")
        if isinstance(content, dict) and isinstance(text := content.get("text"), str):
            content["text"] = truncate_text(text, limit)


def _simple_results(outcomes: Sequence[Outcome], max_content_chars: int) -> list[Any]:
    """The flat, interleaved and de-duplicated result list of the markets that succeeded."""
    per_market = [_raw_results(o) for o in outcomes if isinstance(o, dict)]
    return [
        simplify_result(item, max_content_chars=max_content_chars)
        for item in interleave_results(per_market)
    ]


def _raw_responses(
    markets: Sequence[str], outcomes: Sequence[Outcome], max_content_chars: int
) -> list[Any]:
    """The raw response of each market, or ``{"market", "error"}`` for one that failed."""
    for outcome in outcomes:
        if isinstance(outcome, dict):
            _limit_raw_content(outcome, max_content_chars)
    return [
        {"market": market, "error": str(outcome)} if isinstance(outcome, StaanMCPError) else outcome
        for market, outcome in zip(markets, outcomes, strict=True)
    ]


def search_payload(
    markets: Sequence[str],
    outcomes: Sequence[Outcome],
    *,
    simple_response: bool,
    max_content_chars: int,
) -> list[Any]:
    """Build the JSON array the ``web_search`` tool returns.

    Args:
        markets: The concrete markets, in request order.
        outcomes: One outcome per market, in the same order.
        simple_response: ``True`` for the flat, de-duplicated result list, ``False`` for the raw
            responses with one entry per market.
        max_content_chars: Longest ``full_content`` text kept per result; ``0`` means no limit.
    """
    if simple_response:
        return _simple_results(outcomes, max_content_chars)
    return _raw_responses(markets, outcomes, max_content_chars)


def answer_payload(response: dict[str, Any], *, simple_response: bool) -> dict[str, Any]:
    """Build the structured result of the ``answer`` tool.

    Args:
        response: The raw upstream response.
        simple_response: ``True`` for ``{"answer": <text with footnote citations>}``, ``False``
            for the complete response.

    Raises:
        StaanResponseError: If the response has no ``answer`` text; that is an error in either
            mode.
    """
    text = response.get("answer")
    if not isinstance(text, str):
        msg = "Staan returned a response without an answer text."
        raise StaanResponseError(msg)
    if not simple_response:
        return response
    citations = response.get("citations")
    sources = response.get("sources")
    return {
        "answer": citations_to_gfm(
            text,
            citations if isinstance(citations, list) else [],
            sources if isinstance(sources, list) else [],
        )
    }
