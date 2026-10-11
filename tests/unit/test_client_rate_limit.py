"""How ``StaanClient`` uses the rate limiter, on a fake clock and with no network.

The wait is ``anyio.sleep`` (patched here to move the fake clock), the transport is
``httpx2.MockTransport``. The end-to-end proof with real time is in
``tests/integration/test_rate_limit_package.py``.
"""
# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
import httpx2
import pytest

from staan_mcp.client import StaanClient
from staan_mcp.config import Settings
from staan_mcp.errors import StaanTransportError
from staan_mcp.ratelimit import RateLimiter

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.anyio

WINDOW = 1.05
OK = {"search_id": "s", "query": {"q": "x"}, "web": {"results": []}}


class Clock:
    """A clock that only the patched sleep moves."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.early = 1.0
        """Fraction of each requested sleep that actually elapses (below 1: wakes early)."""

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds * self.early


@pytest.fixture(name="clock")
def clock_fixture(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(anyio, "sleep", fake.sleep)
    return fake


async def instant(_delay: float) -> None:
    """Replaces the sleep between retries, which the rate limit must not depend on."""


def client_for(
    clock: Clock,
    handler: Callable[[httpx2.Request], httpx2.Response] | None = None,
    **settings: Any,
) -> StaanClient:
    transport = httpx2.MockTransport(handler or (lambda _request: httpx2.Response(201, json=OK)))
    return StaanClient(
        Settings(api_key="test-key-0123456789", max_retries=2, **settings),
        transport=transport,
        sleep=instant,
        limiter=RateLimiter(settings.get("rate_limit", 20.0), clock),
    )


async def search(client: StaanClient) -> None:
    await client.search_web(query="x", market="en-gb")


async def test_requests_within_the_budget_do_not_wait_or_log(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    client = client_for(clock, rate_limit=3)

    with caplog.at_level(logging.DEBUG, logger="staan_mcp.client"):
        for _ in range(3):
            await search(client)

    assert clock.sleeps == []
    assert [r for r in caplog.records if r.name == "staan_mcp.client"] == []


async def test_the_request_over_the_budget_waits_and_says_so(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    client = client_for(clock, rate_limit=2)

    with caplog.at_level(logging.DEBUG, logger="staan_mcp.client"):
        for _ in range(3):
            await search(client)

    assert clock.sleeps == [pytest.approx(WINDOW)]
    messages = [r.getMessage() for r in caplog.records if r.name == "staan_mcp.client"]
    assert messages == ["Staan search request delayed 1.05 s to keep to 2 requests per second"]
    assert "test-key" not in caplog.text


async def test_a_sleep_that_ends_early_is_followed_by_another_until_the_slot_is_reached(
    clock: Clock,
) -> None:
    clock.early = 0.5
    client = client_for(clock, rate_limit=1)
    await search(client)

    await search(client)

    assert len(clock.sleeps) > 3
    assert clock.now >= 1000.0 + WINDOW - 1e-9  # the second request did not start early
    assert clock.sleeps[0] == pytest.approx(WINDOW)
    assert clock.sleeps[1] == pytest.approx(WINDOW / 2)


async def test_every_retry_takes_a_slot_of_its_own(clock: Clock) -> None:
    replies = iter([httpx2.Response(503), httpx2.Response(503), httpx2.Response(201, json=OK)])
    sent: list[float] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        sent.append(clock.now)
        return next(replies)

    client = client_for(clock, handler, rate_limit=1)

    await search(client)

    assert sent == [pytest.approx(1000.0), pytest.approx(1000.0 + WINDOW), pytest.approx(1002.1)]


async def test_an_answer_request_shares_the_budget_with_searches(clock: Clock) -> None:
    client = client_for(
        clock, lambda _request: httpx2.Response(201, json={"answer": "Hi"}), rate_limit=1
    )
    await client.answer(query="q")

    await client.answer(query="q")

    assert clock.sleeps == [pytest.approx(WINDOW)]


async def test_a_wait_that_cannot_end_before_the_deadline_fails_at_once(clock: Clock) -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(201, json=OK)

    client = client_for(clock, handler, rate_limit=0.01, deadline=30.0)
    await search(client)

    with pytest.raises(StaanTransportError) as caught:
        await search(client)

    assert str(caught.value) == "The call to Staan did not finish within 30 s."
    assert caught.value.endpoint == "search"
    assert len(sent) == 1
    assert clock.sleeps == []  # it did not sleep to find out
    assert len(client._limiter._starts) == 1  # and booked nothing


async def test_a_wait_that_fits_the_deadline_is_made(clock: Clock) -> None:
    client = client_for(clock, rate_limit=1, deadline=5.0)
    await search(client)

    await search(client)

    assert clock.sleeps == [pytest.approx(WINDOW)]


async def test_a_cancelled_wait_gives_its_slot_back(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1000.0

    async def never(_seconds: float) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(anyio, "sleep", never)
    limiter = RateLimiter(1, lambda: now)
    client = StaanClient(
        Settings(api_key="test-key-0123456789"),
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(201, json=OK)),
        limiter=limiter,
    )
    await search(client)  # the immediate slot
    with anyio.move_on_after(0.05):
        await search(client)  # waits for the next one and is cancelled

    follower = limiter.reserve()

    assert follower is not None
    assert follower.delay == pytest.approx(WINDOW)  # not two windows: the cancelled slot is free


async def test_each_client_has_its_own_budget_from_its_settings() -> None:
    first = StaanClient(Settings(api_key=None, rate_limit=5))
    second = StaanClient(Settings(api_key=None, rate_limit=5))
    off = StaanClient(Settings(api_key=None, rate_limit=0))

    assert (first._limiter.capacity, second._limiter.capacity, off._limiter.capacity) == (5, 5, 0)
    assert first._limiter is not second._limiter
    for client in (first, second, off):
        await client.aclose()
