"""What the rate limit end-to-end tests share: the budget check and the scripted upstream.

The limiter is judged from outside, on the arrival times the fake upstream recorded with the
monotonic clock (``LocalStaan.arrivals``), never on how long a test slept.
"""

from __future__ import annotations

import json
from itertools import pairwise
from typing import TYPE_CHECKING

from local_staan import LocalStaan, Reply, json_reply

from staan_mcp.markets import MARKETS

if TYPE_CHECKING:
    from collections.abc import Sequence

WINDOW = 1.0
"""The window Staan counts in."""

SCHEDULER_TOLERANCE = 0.2
"""How much the fake's arrival stamps may disagree with when the limiter booked the starts.

The stamp is taken by a handler thread that has to be scheduled (and win the GIL, in the in-process
tests) after the client sent: 1 ms to 30 ms on an idle machine, and up to about 150 ms measured
here while ten busy processes and three test loops shared twelve cores. Two requests booked 1.05 s
apart (the window plus the 50 ms safety margin) can thus be stamped less than a second apart. The
checks below count in windows of ``WINDOW - SCHEDULER_TOLERANCE``, so the limiter's margin plus
this tolerance (0.25 s in all) is the slack; a limiter that let a burst through unchecked would
put 24 arrivals into 0.1 s, far from it."""

MINIMUM_SPAN = WINDOW - SCHEDULER_TOLERANCE
"""Least time between the first and the last arrival when a burst has to be spread over more than
one window: the limiter books the next group 1.05 s after the first, less the tolerance."""

ENGLISH_MARKETS = [market for market in MARKETS if market.startswith("en-")]
"""Ten markets, so ``en-*`` is a call of ten requests."""


def max_in_window(arrivals: Sequence[float], window: float = WINDOW - SCHEDULER_TOLERANCE) -> int:
    """The most arrivals that fall into one half-open window of ``window`` seconds.

    The default is the second Staan counts in, shortened by ``SCHEDULER_TOLERANCE``.
    """
    ordered = sorted(arrivals)
    most = 0
    first = 0
    for last, moment in enumerate(ordered):
        while moment - ordered[first] >= window:
            first += 1
        most = max(most, last - first + 1)
    return most


def two_groups(arrivals: Sequence[float]) -> list[int]:
    """Sizes of the two bursts a limited burst arrives in, split at the longest pause.

    The longest pause is the one the limiter put there (about a second), however far a loaded
    machine spreads the arrivals inside each burst.
    """
    ordered = sorted(arrivals)
    gaps = [after - before for before, after in pairwise(ordered)]
    split = gaps.index(max(gaps)) + 1
    return [split, len(ordered) - split]


def url_for(market: str) -> str:
    """The one page the fake finds in ``market``."""
    return f"https://{market}.example/page"


def script_markets(upstream: LocalStaan, markets: Sequence[str] = MARKETS) -> None:
    """Make the fake answer each market with one result of its own."""
    for market in markets:
        page = {"title": market, "url": url_for(market), "snippet": f"Snippet of {market}"}
        upstream.by_market[market] = json_reply({"web": {"results": [page]}})


def script_failing_once(upstream: LocalStaan, markets: Sequence[str]) -> None:
    """Make the fake answer each market with 503 (retry at once) and then with its result."""
    unavailable = Reply(503, b"{}", headers={"Retry-After": "0"})
    for market in markets:
        script_markets(upstream, [market])
        ok = upstream.by_market[market]
        assert isinstance(ok, Reply)
        upstream.by_market[market] = [unavailable, ok]


def urls_in(text: str) -> list[str]:
    """The result URLs in the text of a ``web_search`` result (the simple response)."""
    return [hit["url"] for hit in json.loads(text)]
