"""The rate limit on both implementations, from outside: real processes, real JSON-RPC, real HTTP.

Each test starts ``python -m staan_mcp`` (the package) or ``python stdio_server.py`` (the
standalone server) with the limit in its environment, points it at ``LocalStaan`` and talks to it
over stdin and stdout. The verdict is always the monotonic arrival times the upstream recorded:
how many requests fall into any one second, and how far apart the first and the last are.
"""
# pylint: disable=missing-function-docstring

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from rate_limit_helpers import (
    ENGLISH_MARKETS,
    MINIMUM_SPAN,
    WINDOW,
    max_in_window,
    script_failing_once,
    script_markets,
    two_groups,
    url_for,
)
from rpc_process import RpcProcess, clean_environment, initialize
from staan_fakes import API_KEY

from staan_mcp import stdio_server
from staan_mcp.markets import MARKETS

if TYPE_CHECKING:
    from collections.abc import Callable

    from local_staan import LocalStaan

COMMANDS = {
    "package": [sys.executable, "-m", "staan_mcp"],
    "standalone": [sys.executable, str(Path(stdio_server.__file__).resolve())],
}
implementations = pytest.mark.parametrize("implementation", list(COMMANDS))

NEGATIVE = (
    "STAAN_RATE_LIMIT must be a finite number of requests per second, 0 (no limit) or greater"
)
DEADLINE_MESSAGE = "The call to Staan did not finish within 20 s."


def tools_call(request_id: int, name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def web_search(request_id: int, market: str | list[str], **arguments: Any) -> dict[str, Any]:
    return tools_call(
        request_id, "web_search", query="python", market=market, full_content="none", **arguments
    )


def start(implementation: str, upstream: LocalStaan, **env: str) -> RpcProcess:
    """A server of ``implementation`` against ``upstream``, handshake done."""
    environment = clean_environment(
        {
            "STAAN_API_KEY": API_KEY,
            "STAAN_BASE_URL": upstream.base_url,
            "STAAN_MAX_RETRIES": "0",
            **env,
        }
    )
    process = RpcProcess(COMMANDS[implementation], environment)
    initialize(process)
    return process


def replies(process: RpcProcess, count: int) -> dict[Any, dict[str, Any]]:
    """The next ``count`` messages the server writes, by request id."""
    received = {}
    for _ in range(count):
        message = process._next_message("tool reply")  # pylint: disable=protected-access
        received[message["id"]] = message["result"]
    return received


def result_text(result: dict[str, Any]) -> str:
    (block,) = result["content"]
    return str(block["text"])


def urls_of(result: dict[str, Any]) -> list[str]:
    assert not result.get("isError"), result_text(result)
    return sorted(hit["url"] for hit in json.loads(result_text(result)))


def wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


@implementations
def test_two_calls_over_all_markets_stay_within_twenty_requests_a_second(
    implementation: str, local_staan: LocalStaan
) -> None:
    script_markets(local_staan)
    started = time.monotonic()

    with start(implementation, local_staan) as process:
        process.notify(web_search(2, "*"))
        process.notify(web_search(3, "*"))
        results = replies(process, 2)
    elapsed = time.monotonic() - started

    arrivals = local_staan.arrivals()
    expected = sorted(url_for(market) for market in MARKETS)
    assert len(arrivals) == 24  # all sent, none refused
    assert max_in_window(arrivals) == 20  # the whole budget, never more
    assert two_groups(arrivals) == [20, 4]  # the same shape for both implementations
    assert arrivals[-1] - arrivals[0] >= MINIMUM_SPAN
    assert elapsed >= WINDOW  # on the client's clock: no stamping jitter in it
    assert [urls_of(results[2]), urls_of(results[3])] == [expected, expected]


@implementations
def test_the_limit_from_the_environment_is_applied(
    implementation: str, local_staan: LocalStaan
) -> None:
    script_markets(local_staan)
    started = time.monotonic()

    with start(implementation, local_staan, STAAN_RATE_LIMIT="5") as process:
        process.notify(web_search(2, "en-*"))
        results = replies(process, 1)
    elapsed = time.monotonic() - started

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 10
    assert max_in_window(arrivals) == 5
    assert two_groups(arrivals) == [5, 5]
    assert elapsed >= WINDOW
    assert urls_of(results[2]) == sorted(url_for(market) for market in ENGLISH_MARKETS)


@implementations
def test_a_limit_of_zero_turns_the_limiter_off(
    implementation: str, local_staan: LocalStaan
) -> None:
    script_markets(local_staan)

    with start(implementation, local_staan, STAAN_RATE_LIMIT="0") as process:
        process.notify(web_search(2, "*"))
        process.notify(web_search(3, "*"))
        replies(process, 2)

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 24
    assert arrivals[-1] - arrivals[0] < WINDOW  # all 24 at once


@implementations
def test_retries_count_towards_the_budget(implementation: str, local_staan: LocalStaan) -> None:
    markets = ENGLISH_MARKETS[:5]
    script_failing_once(local_staan, markets)

    with start(implementation, local_staan, STAAN_RATE_LIMIT="5", STAAN_MAX_RETRIES="1") as process:
        process.notify(web_search(2, markets))
        results = replies(process, 1)

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 10  # five requests and their five retries
    assert max_in_window(arrivals) == 5
    assert two_groups(arrivals) == [5, 5]
    assert arrivals[-1] - arrivals[0] >= MINIMUM_SPAN
    assert urls_of(results[2]) == sorted(url_for(market) for market in markets)


@implementations
def test_a_deadline_shorter_than_the_wait_fails_at_once_without_sending(
    implementation: str, local_staan: LocalStaan
) -> None:
    script_markets(local_staan)

    # One request every 100 s: the first goes, the nine others would wait far past the 20 s
    # deadline.
    env = {"STAAN_RATE_LIMIT": "0.01", "STAAN_DEADLINE": "20"}
    with start(implementation, local_staan, **env) as process:
        started = time.monotonic()  # the handshake is done: only the call is timed
        process.notify(web_search(2, "en-*", simple_response=False))
        results = replies(process, 1)

    assert time.monotonic() - started < 10.0  # on the spot, not after the 20 s of the deadline
    assert len(local_staan.arrivals()) == 1
    entries = json.loads(result_text(results[2]))
    assert [entry.get("error") for entry in entries].count(DEADLINE_MESSAGE) == 9


@implementations
def test_cancelling_a_call_stops_the_requests_that_are_still_waiting(
    implementation: str, local_staan: LocalStaan
) -> None:
    script_markets(local_staan)

    with start(implementation, local_staan, STAAN_RATE_LIMIT="5") as process:
        process.notify(web_search(2, "en-*"))
        wait_until(lambda: len(local_staan.arrivals()) >= 5)  # five sent, five waiting
        process.notify(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}}
        )
        time.sleep(1.4)  # the window in which the other five would have been sent
        count = len(local_staan.arrivals())
        assert process.call({"jsonrpc": "2.0", "id": 9, "method": "ping"})["id"] == 9

    assert count == 5


@implementations
def test_a_cancelled_call_gives_its_slots_to_the_next_one(
    implementation: str, local_staan: LocalStaan
) -> None:
    script_markets(local_staan)

    with start(implementation, local_staan, STAAN_RATE_LIMIT="5") as process:
        process.notify(web_search(2, "en-*"))  # 5 sent, 5 booked for the next second
        wait_until(lambda: len(local_staan.arrivals()) >= 5)
        process.notify(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}}
        )
        time.sleep(0.1)  # let the cancellation reach the waiters
        process.notify(web_search(3, ENGLISH_MARKETS[:2]))
        replies(process, 1)

    arrivals = local_staan.arrivals()
    assert len(arrivals) == 7  # five, then the two of the second call
    assert max_in_window(arrivals) == 5
    # The two went in the first free slot, one window after the first five (not behind the
    # ten-request call's phantom slots, which would put them another window later).
    assert arrivals[-1] - arrivals[0] < 1.6


def configuration_message(implementation: str, upstream: LocalStaan, value: str) -> str:
    """What ``implementation`` says about ``STAAN_RATE_LIMIT=value``."""
    environment = clean_environment(
        {"STAAN_API_KEY": API_KEY, "STAAN_BASE_URL": upstream.base_url, "STAAN_RATE_LIMIT": value}
    )
    with RpcProcess(COMMANDS[implementation], environment) as process:
        if implementation == "package":  # refuses to start: the message is on stderr
            assert process.close() == 2
            return process.stderr().strip()
        initialize(process)  # reports it when a tool is called
        result = process.call(web_search(2, "en-gb"))["result"]
    assert result["isError"] is True
    return result_text(result)


@pytest.mark.parametrize(
    ("value", "sentence"),
    [
        ("fast", "STAAN_RATE_LIMIT must be a number of requests per second, got 'fast'."),
        ("-1", f"{NEGATIVE}, got '-1'."),
        ("nan", f"{NEGATIVE}, got 'nan'."),
    ],
)
def test_an_invalid_limit_is_refused_with_the_same_words_by_both(
    local_staan: LocalStaan, value: str, sentence: str
) -> None:
    package = configuration_message("package", local_staan, value)
    standalone = configuration_message("standalone", local_staan, value)

    assert package == f"staan-mcp: error: {sentence}"
    assert standalone == f"Error executing tool web_search: {sentence}"
