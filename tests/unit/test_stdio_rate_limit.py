"""How the standalone server uses the rate limiter, on a fake clock.

The limiter's clock and the call's stop event are replaced by doubles that move time instead of
waiting, so no test sleeps. The real-time proof is ``tests/integration/test_rate_limit_stdio.py``.
"""
# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import io
import threading
import time
from typing import TYPE_CHECKING

import pytest
from local_staan import Reply, json_reply
from stdio_helpers import OK_SEARCH, SEARCH_BODY, settings_for

from staan_mcp import stdio_server
from staan_mcp.config import Settings
from staan_mcp.errors import ConfigurationError, StaanTransportError
from staan_mcp.ratelimit import RateLimiter

if TYPE_CHECKING:
    from local_staan import LocalStaan
WINDOW = 1.05
STOPPED = "The call was cancelled."


class Clock:  # pylint: disable=too-few-public-methods
    """The limiter's clock; only ``SteppedStop`` moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SteppedStop(threading.Event):
    """A stop event whose ``wait`` moves the fake clock instead of blocking."""

    def __init__(self, clock: Clock, *, early: float = 1.0, set_during: int | None = None) -> None:
        super().__init__()
        self.clock = clock
        self.early = early  # the fraction of a wait that elapses before it returns
        self.set_during = set_during  # the number of the wait (from 1) in which it gets set
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self.waits.append(timeout)
        self.clock.now += timeout * self.early
        if self.set_during == len(self.waits):
            self.set()
        return self.is_set()


def post(
    local_staan: LocalStaan,
    limiter: RateLimiter,
    stop: threading.Event,
    **overrides: float,
) -> object:
    settings = settings_for(local_staan, **overrides)
    return stdio_server._post(settings, "search", SEARCH_BODY, stop, limiter)


def test_a_request_within_the_budget_is_sent_at_once_and_says_nothing(
    local_staan: LocalStaan, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock()
    stop = SteppedStop(clock)

    post(local_staan, RateLimiter(2, clock), stop)

    assert len(stop.waits) == 0
    assert len(local_staan.requests) == 1
    assert capsys.readouterr().err == ""


def test_the_request_over_the_budget_waits_for_its_slot_and_says_so(
    local_staan: LocalStaan, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = Clock()
    stop = SteppedStop(clock)
    limiter = RateLimiter(2, clock)
    for _ in range(2):
        post(local_staan, limiter, stop, rate_limit=2)

    post(local_staan, limiter, stop, rate_limit=2)

    assert stop.waits == [pytest.approx(WINDOW)]
    assert len(local_staan.requests) == 3
    assert capsys.readouterr().err == (
        "staan-stdio: rate limit: search request delayed 1.05 s to keep to 2 requests per second\n"
    )


def test_a_wait_that_ends_early_is_followed_by_another_until_the_slot_is_reached(
    local_staan: LocalStaan,
) -> None:
    clock = Clock()
    stop = SteppedStop(clock, early=0.5)
    limiter = RateLimiter(1, clock)
    post(local_staan, limiter, stop)

    post(local_staan, limiter, stop)

    assert len(stop.waits) > 3
    assert stop.waits[:2] == [pytest.approx(WINDOW), pytest.approx(WINDOW / 2)]
    assert clock.now >= 1000.0 + WINDOW - 1e-9


def test_every_retry_takes_a_slot_of_its_own(local_staan: LocalStaan) -> None:
    local_staan.search = [Reply(503, b"{}", headers={"Retry-After": "0"}), json_reply(OK_SEARCH)]
    clock = Clock()
    stop = SteppedStop(clock)

    post(local_staan, RateLimiter(1, clock), stop, max_retries=2)

    assert len(local_staan.requests) == 2
    # the backoff before the retry (0.5 s), then the rest of the window until its slot
    assert stop.waits == [pytest.approx(0.5), pytest.approx(WINDOW - 0.5)]


def test_a_slot_after_the_deadline_fails_at_once_with_the_deadline_message(
    local_staan: LocalStaan,
) -> None:
    clock = Clock()
    stop = SteppedStop(clock)
    limiter = RateLimiter(0.01, clock)
    post(local_staan, limiter, stop, deadline=30.0)

    with pytest.raises(StaanTransportError) as caught:
        post(local_staan, limiter, stop, deadline=30.0)

    assert str(caught.value) == "The call to Staan did not finish within 30 s."
    assert caught.value.endpoint == "search"
    assert len(local_staan.requests) == 1
    assert len(stop.waits) == 0
    assert len(limiter._starts) == 1  # nothing was booked for it


def test_a_call_cancelled_before_it_starts_sends_and_books_nothing(local_staan: LocalStaan) -> None:
    clock = Clock()
    stop = SteppedStop(clock)
    stop.set()
    limiter = RateLimiter(5, clock)

    with pytest.raises(StaanTransportError, match=STOPPED):
        post(local_staan, limiter, stop)

    assert local_staan.requests == []
    assert len(limiter._starts) == 0


def test_a_call_cancelled_while_waiting_sends_nothing_and_gives_its_slot_back(
    local_staan: LocalStaan,
) -> None:
    clock = Clock()
    limiter = RateLimiter(1, clock)
    post(local_staan, limiter, SteppedStop(clock))
    stop = SteppedStop(clock, early=0.5, set_during=1)  # cancelled half-way

    with pytest.raises(StaanTransportError, match=STOPPED):
        post(local_staan, limiter, stop)

    assert len(local_staan.requests) == 1  # only the first one was sent
    assert len(limiter._starts) == 1  # the cancelled one's slot is free again
    follower = limiter.reserve()
    assert follower is not None
    assert follower.delay == pytest.approx(WINDOW - stop.waits[0] / 2)


def test_a_limiter_that_is_off_never_waits(local_staan: LocalStaan) -> None:
    clock = Clock()
    stop = SteppedStop(clock)
    limiter = RateLimiter(0, clock)

    for _ in range(5):
        post(local_staan, limiter, stop)

    assert len(stop.waits) == 0
    assert len(local_staan.requests) == 5


def test_a_caller_without_a_limiter_is_not_throttled(local_staan: LocalStaan) -> None:
    stop = threading.Event()
    settings = settings_for(local_staan)

    for _ in range(3):
        stdio_server._post(settings, "search", SEARCH_BODY, stop)

    assert len(local_staan.requests) == 3


@pytest.mark.parametrize(
    ("settings", "capacity"),
    [
        (Settings(api_key=None), 20),
        (Settings(api_key=None, rate_limit=5), 5),
        (Settings(api_key=None, rate_limit=0), 0),
        (ConfigurationError("STAAN_RATE_LIMIT is wrong"), 20),
    ],
)
def test_each_server_makes_its_own_limiter_from_its_settings(
    settings: Settings | ConfigurationError, capacity: int
) -> None:
    first = stdio_server.Server(settings, io.BytesIO(), io.BytesIO())
    second = stdio_server.Server(settings, io.BytesIO(), io.BytesIO())

    assert first._limiter.capacity == capacity
    assert first._limiter is not second._limiter


def test_the_tools_of_one_server_share_its_budget(local_staan: LocalStaan) -> None:
    local_staan.answer = json_reply({"answer": "Hi"})
    clock = Clock()
    limiter = RateLimiter(1, clock)
    settings = settings_for(local_staan, deadline=30.0)

    def run(tool: str, args: dict[str, object]) -> dict[str, object]:
        return stdio_server.call_tool(tool, args, settings, SteppedStop(clock), limiter)

    first = run("answer", {"query": "q"})
    second = run("web_search", {"query": "q", "market": "en-gb"})

    assert "isError" not in first
    assert "isError" not in second
    assert len(limiter._starts) == 2
    assert limiter._starts[1] - limiter._starts[0] == pytest.approx(WINDOW)


def test_the_real_time_clock_and_event_work_with_the_limiter(local_staan: LocalStaan) -> None:
    limiter = RateLimiter(5)
    stop = threading.Event()
    started = time.monotonic()

    post(local_staan, limiter, stop)

    assert time.monotonic() - started < 5.0
    assert len(local_staan.requests) == 1
