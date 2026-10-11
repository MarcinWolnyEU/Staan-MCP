"""The rate limit, proven end to end on the package server with real time and a real socket.

The server is the real one (``create_server`` with the real ``StaanClient``, real ``httpx2``
and ``anyio.sleep``); the upstream is ``LocalStaan``, a loopback HTTP server that stamps each
arrival with the monotonic clock. Every check reads those stamps: how many requests Staan would
have seen in any second, never how long a test slept.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from mcp import Client
from rate_limit_helpers import (
    ENGLISH_MARKETS,
    MINIMUM_SPAN,
    WINDOW,
    max_in_window,
    script_failing_once,
    script_markets,
    url_for,
    urls_in,
)
from staan_fakes import API_KEY, instant_sleep, text_of

from staan_mcp.client import StaanClient
from staan_mcp.config import Settings
from staan_mcp.markets import MARKETS
from staan_mcp.server import create_server

if TYPE_CHECKING:
    from local_staan import LocalStaan
    from mcp.server import MCPServer
    from mcp.types import CallToolResult

pytestmark = pytest.mark.anyio

DEADLINE_MESSAGE = "The call to Staan did not finish within 20 s."


def server_for(
    upstream: LocalStaan, *, instant_retries: bool = False, **overrides: Any
) -> MCPServer[Any]:
    """The real server against ``upstream``; ``overrides`` are :class:`Settings` fields."""
    settings = Settings(
        api_key=API_KEY, base_url=upstream.base_url, **{"max_retries": 0, **overrides}
    )

    def factory(configured: Settings) -> StaanClient:
        if instant_retries:  # the backoff between retries is not what is under test
            return StaanClient(configured, sleep=instant_sleep)
        return StaanClient(configured)

    return create_server(settings, client_factory=factory)


async def search_all(
    server: MCPServer[Any], market: str | list[str], calls: int = 1, **arguments: Any
) -> list[CallToolResult]:
    """Run ``calls`` ``web_search`` calls at the same time on one server."""
    results: list[CallToolResult] = []

    async with Client(server) as client:

        async def one() -> None:
            args = {"query": "python", "market": market, "full_content": "none", **arguments}
            results.append(await client.call_tool("web_search", args))

        async with anyio.create_task_group() as group:
            for _ in range(calls):
                group.start_soon(one)
    return results


async def test_two_calls_over_all_markets_stay_within_twenty_requests_a_second(
    local_staan: LocalStaan,
) -> None:
    script_markets(local_staan)
    started = time.monotonic()

    results = await search_all(server_for(local_staan), "*", calls=2)

    elapsed = time.monotonic() - started
    arrivals = local_staan.arrivals()
    assert len(arrivals) == 2 * len(MARKETS) == 24  # every request was sent, none refused
    assert max_in_window(arrivals) == 20  # the whole budget is used, and never more
    assert arrivals[-1] - arrivals[0] >= MINIMUM_SPAN  # the 21st to 24th waited for the window
    assert elapsed >= WINDOW  # on the client's clock: no stamping jitter in it
    assert [result.is_error for result in results] == [False, False]
    for result in results:  # and the answers are complete
        assert sorted(urls_in(text_of(result))) == sorted(url_for(market) for market in MARKETS)


async def test_one_call_over_all_markets_fits_the_budget_and_is_not_delayed(
    local_staan: LocalStaan,
) -> None:
    script_markets(local_staan)

    (result,) = await search_all(server_for(local_staan), "*")

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 12
    assert arrivals[-1] - arrivals[0] < WINDOW  # twelve of the twenty: all at once
    assert len(urls_in(text_of(result))) == 12


async def test_the_budget_is_shared_by_search_and_answer(local_staan: LocalStaan) -> None:
    script_markets(local_staan)
    settings = Settings(api_key=API_KEY, base_url=local_staan.base_url, max_retries=0, rate_limit=5)
    server = create_server(settings)
    async with Client(server) as client:

        async def answer() -> None:
            await client.call_tool("answer", {"query": "why"})

        async def search() -> None:
            await client.call_tool("web_search", {"query": "x", "market": "en-*"})

        async with anyio.create_task_group() as group:
            group.start_soon(search)  # 10 requests
            group.start_soon(answer)  # 1 more

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 11
    assert max_in_window(arrivals) <= 5
    assert arrivals[-1] - arrivals[0] >= 2 * MINIMUM_SPAN


async def test_the_limit_from_the_environment_is_applied(local_staan: LocalStaan) -> None:
    script_markets(local_staan)
    settings = Settings.from_env(
        {
            "STAAN_API_KEY": API_KEY,
            "STAAN_BASE_URL": local_staan.base_url,
            "STAAN_MAX_RETRIES": "0",
            "STAAN_RATE_LIMIT": "5",
        }
    )
    started = time.monotonic()

    async with Client(create_server(settings)) as client:
        result = await client.call_tool(
            "web_search", {"query": "x", "market": "en-*", "full_content": "none"}
        )

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 10
    assert max_in_window(arrivals) == 5
    assert time.monotonic() - started >= WINDOW
    assert sorted(urls_in(text_of(result))) == sorted(url_for(m) for m in ENGLISH_MARKETS)


async def test_retries_count_towards_the_budget(local_staan: LocalStaan) -> None:
    markets = ENGLISH_MARKETS[:5]
    script_failing_once(local_staan, markets)
    server = server_for(local_staan, instant_retries=True, rate_limit=5, max_retries=1)

    (result,) = await search_all(server, markets)

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 10  # five requests and the five retries, all sent
    assert max_in_window(arrivals) == 5  # the retries did not slip in beside the first five
    assert arrivals[-1] - arrivals[0] >= MINIMUM_SPAN
    assert sorted(urls_in(text_of(result))) == sorted(url_for(m) for m in markets)


async def test_a_deadline_shorter_than_the_wait_fails_at_once_without_sending(
    local_staan: LocalStaan,
) -> None:
    script_markets(local_staan)
    # One request every 100 s: the first goes, the nine others would have to wait far past the
    # deadline of 20 s.
    server = server_for(local_staan, rate_limit=0.01, deadline=20.0)
    started = time.monotonic()

    (result,) = await search_all(server, "en-*", simple_response=False)

    assert time.monotonic() - started < 10.0  # failed on the spot, not after 20 s
    assert len(local_staan.arrivals()) == 1
    entries = json.loads(text_of(result))
    assert [entry.get("error") for entry in entries].count(DEADLINE_MESSAGE) == 9


async def test_a_cancelled_wait_does_not_hold_up_the_next_request(local_staan: LocalStaan) -> None:
    settings = Settings(api_key=API_KEY, base_url=local_staan.base_url, rate_limit=1)
    async with StaanClient(settings) as client:
        await client.search_web(query="a", market="en-gb")
        with anyio.move_on_after(0.2) as scope:
            await client.search_web(query="b", market="en-gb")  # waits for the next second
        await client.search_web(query="c", market="en-gb")

    assert scope.cancelled_caught
    sent = [request.body["q"] for request in local_staan.requests]
    assert sent == ["a", "c"]  # "b" was never sent
    first, last = local_staan.arrivals()
    assert last - first < 1.8  # the next second: the cancelled slot did not push it to two
    assert last - first >= MINIMUM_SPAN
