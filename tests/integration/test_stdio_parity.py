"""Drift guard: the standalone ``stdio_server.py`` must say and do what the package's server does.

The two implementations share the package's pure modules, so these tests compare the parts that
are written twice, always on the same inputs: the tool definitions, the result of the same
upstream replies, the retry schedule, which arguments are accepted, the configuration errors and
the protocol answers of the two real processes.

Known, deliberate differences. Everything else is identical, and the tests pin both lists (the
protocol ones row by row in ``test_stdio_wire.py``, each with the answer of both sides).

Tool calls:

* The text of a *rejected argument* differs: the package shows pydantic's report, the standalone
  server a one-line ``Invalid argument 'x': ...``. Both start with ``Error executing tool <name>:``
  and the same arguments are rejected. Errors raised by the shared modules (unknown market,
  invalid domain, source URL checks, upstream errors) are word for word the same.
* The standalone server reports a configuration error when a tool is called, the package refuses
  to start (exit status 2) with the same message.
* A transport failure names the exception class of the HTTP library, ``ConnectError`` for
  ``httpx2`` and ``ConnectionRefusedError`` for ``urllib``; the sentence around it is the same.
* The ``User-Agent`` is ``staan-mcp-stdio/<version>`` instead of ``staan-mcp/<version>``.
* A key or configuration error in one market cancels the others. The package cancels their
  tasks, which drops a request already sent. The standalone server cannot take a request back
  from a socket a thread is blocked on, so it only stops the markets whose request is not sent
  yet. Which requests of a call are on the wire when the error comes back is therefore a matter
  of timing there (on a real network all of them are, the answer being slower than sending). The
  tests state that: the upstream holds the failing answer until every request has arrived.

JSON-RPC lines. The SDK silently drops what it cannot read as a JSON-RPC message, and the
standalone server answers or tolerates it, on purpose (a client that sent garbage learns so; one
that left out a member still gets served):

* Not JSON (text, a byte order mark, nesting beyond the parser, bytes that are not UTF-8 as a
  whole): the SDK says nothing, the standalone server ``-32700``.
* A JSON array (a batch) or a scalar: nothing, ``-32600``.
* An ``id`` that is not a string or an integer (null, a float, a boolean, an array, an object):
  nothing, ``-32600``. A request with an ``id`` and no string ``method``: nothing, ``-32600``.
* No ``jsonrpc`` member, or one that is not ``"2.0"``: the SDK ignores the message, the
  standalone server serves it.
* ``params`` that is not an object (a string, an array): the SDK ignores the message, the
  standalone server treats it as absent (or ``-32602`` where the method needs parameters).
* Optional members whose type the SDK checks and the standalone server does not: ``_meta``, the
  ``cursor`` of the list methods, what is nested in ``capabilities``, the optional fields of
  ``clientInfo``. The SDK answers ``-32602``, the standalone server ignores them.
* A method the standalone server does not know, with parameters the SDK would reject
  (``resources/subscribe``, ``logging/setLevel``, ``completion/complete``): ``-32602`` there,
  ``-32601`` here.

Identical on the wire, and tested as such: the handshake (``initialize`` needs a string
``protocolVersion``, a ``capabilities`` object and a ``clientInfo`` with a string ``name`` and
``version``; every request but ``ping`` is refused with ``-32602`` until one was accepted or
``notifications/initialized`` arrived), the protocol version negotiation, ``prompts/get`` and
``resources/read`` (an unknown prompt or resource), unknown methods, notifications and
responses, every valid kind of ``id``, duplicate keys, a carriage return, blank lines and bytes
that are not UTF-8 inside a string (read as ``U+FFFD``).
"""
# pylint: disable=protected-access

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import httpx2
import pytest
from local_staan import LocalStaan, Reply
from mcp import Client
from rpc_process import INITIALIZE, RpcProcess, clean_environment, initialize
from staan_fakes import API_KEY, FakeStaan, call, make_server, text_of

from staan_mcp import cli, stdio_server
from staan_mcp.client import StaanClient
from staan_mcp.config import Settings
from staan_mcp.contract import TOOL_LIST
from staan_mcp.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mcp.types import CallToolResult

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEARCH_FIXTURES = sorted(path.name for path in FIXTURES.glob("search_*.json"))
ANSWER_FIXTURES = sorted(path.name for path in FIXTURES.glob("answer_*.json"))
HANDSHAKE: list[dict[str, Any]] = [
    {**INITIALIZE, "id": "handshake"},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]
"""What a client says first: the standalone server serves nothing but ``ping`` before it."""


def fixture(name: str) -> Any:
    """Load ``tests/fixtures/<name>``."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def reply_of(payload: object, status: int = 201) -> Reply:
    """A JSON reply."""
    return Reply(status, json.dumps(payload).encode())


@dataclass(frozen=True)
class Script:
    """What the upstream answers, described once and played on both fakes."""

    search: Reply = field(default_factory=lambda: reply_of({"web": {"results": []}}))
    by_market: dict[str, Reply] = field(default_factory=dict)
    answer: Reply = field(default_factory=lambda: reply_of({"answer": "Hi"}))


@dataclass(frozen=True)
class Outcome:
    """What a client sees of one tool call, and what the upstream received."""

    is_error: bool
    text: str
    structured: Any
    bodies: list[dict[str, Any]]


def by_market(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order request bodies independently of which thread arrived first."""
    return sorted(bodies, key=lambda body: str(body.get("market", "")))


def httpx_reply(reply: Reply) -> httpx2.Response:
    """The same reply as an ``httpx2`` response."""
    return httpx2.Response(reply.status, content=reply.body, headers=reply.headers)


async def run_package(
    tool: str, arguments: dict[str, Any], script: Script, **settings: Any
) -> Outcome:
    """Call ``tool`` on the package's server (in memory, over ``httpx2.MockTransport``)."""
    fake = FakeStaan()
    fake.search = httpx_reply(script.search)
    fake.by_market = {market: httpx_reply(r) for market, r in script.by_market.items()}
    fake.answer = httpx_reply(script.answer)
    result: CallToolResult = await call(fake, tool, arguments, **settings)
    return Outcome(
        bool(result.is_error), text_of(result), result.structured_content, by_market(fake.bodies())
    )


def run_stdio(
    upstream: LocalStaan, tool: str, arguments: dict[str, Any], script: Script, **settings: Any
) -> Outcome:
    """Call ``tool`` on the standalone server (in process, over a real loopback socket)."""
    upstream.requests.clear()
    upstream.search = script.search
    upstream.by_market = dict(script.by_market)
    upstream.answer = script.answer
    base = Settings(api_key=API_KEY, base_url=upstream.base_url, timeout=5.0, max_retries=0)
    result = stdio_server.call_tool(tool, arguments, replace(base, **settings), threading.Event())
    (block,) = result["content"]
    return Outcome(
        bool(result.get("isError")),
        block["text"],
        result.get("structuredContent"),
        by_market(upstream.bodies()),
    )


async def run_both(
    upstream: LocalStaan, tool: str, arguments: dict[str, Any], script: Script, **settings: Any
) -> tuple[Outcome, Outcome]:
    """The package's outcome and the standalone server's, for the same call."""
    package = await run_package(tool, arguments, script, **settings)
    standalone = run_stdio(upstream, tool, arguments, script, **settings)
    return package, standalone


def error_text(tool: str, message: str) -> str:
    """The text of an error result, as the package's SDK writes it."""
    return f"Error executing tool {tool}: {message}"


# --------------------------------------------------------------------------------------
# 1. The tool definitions
# --------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tools_list_is_identical_in_every_field() -> None:
    async with Client(make_server(FakeStaan())) as client:
        package = [
            tool.model_dump(by_alias=True, exclude_none=True)
            for tool in (await client.list_tools()).tools
        ]

    assert package == TOOL_LIST


def test_the_standalone_server_sends_the_same_list_over_the_wire() -> None:
    settings = Settings(api_key=API_KEY)
    sent = [*HANDSHAKE, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in sent))
    stdout = io.BytesIO()

    stdio_server.Server(settings, stdin, stdout).serve()

    *_, listing = (json.loads(line) for line in stdout.getvalue().splitlines())
    assert listing["result"] == {"tools": TOOL_LIST}


# --------------------------------------------------------------------------------------
# 2. The same upstream replies, shaped the same way
# --------------------------------------------------------------------------------------

SEARCH_ARGUMENTS: list[dict[str, Any]] = [
    {"query": "q"},
    {"query": "q", "full_content": "none"},
    {"query": "q", "simple_response": False},
    {"query": "q", "market": ["en-gb", "fr-fr", "de-de"]},
    {"query": "q", "market": "*", "simple_response": False, "full_content": "html", "offset": 10},
    {"query": "q", "include_domains": ["https://www.Example.com/x"], "market": "en-*"},
]


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", SEARCH_ARGUMENTS)
@pytest.mark.parametrize("name", SEARCH_FIXTURES)
@pytest.mark.parametrize("max_content_chars", [0, 200])
async def test_search_fixtures_give_identical_results(
    local_staan: LocalStaan, name: str, arguments: dict[str, Any], max_content_chars: int
) -> None:
    script = Script(search=reply_of(fixture(name)))

    package, standalone = await run_both(
        local_staan, "web_search", arguments, script, max_content_chars=max_content_chars
    )

    assert standalone == package
    assert not package.is_error
    assert package.bodies


@pytest.mark.anyio
async def test_a_search_with_one_failing_market_gives_identical_results(
    local_staan: LocalStaan,
) -> None:
    script = Script(
        search=reply_of(fixture("search_plain.json")),
        by_market={"de-de": Reply(500), "fr-fr": reply_of(fixture("search_short.json"))},
    )
    for simple_response in (True, False):
        arguments = {
            "query": "q",
            "market": ["en-gb", "de-de", "fr-fr"],
            "simple_response": simple_response,
        }

        package, standalone = await run_both(local_staan, "web_search", arguments, script)

        assert standalone == package
        assert not package.is_error


@pytest.mark.anyio
@pytest.mark.parametrize("simple_response", [True, False])
@pytest.mark.parametrize("name", ANSWER_FIXTURES)
async def test_answer_fixtures_give_identical_results(
    local_staan: LocalStaan, name: str, *, simple_response: bool
) -> None:
    script = Script(answer=reply_of(fixture(name)))
    arguments = {
        "query": "q",
        "simple_response": simple_response,
        "filter": "https://www.Example.com/a",
    }

    package, standalone = await run_both(local_staan, "answer", arguments, script)

    assert standalone == package
    assert not package.is_error
    assert package.structured is not None
    assert package.bodies[0]["filter"] == "example.com"


@pytest.mark.anyio
async def test_answer_sources_are_sent_identically(local_staan: LocalStaan) -> None:
    arguments = {
        "query": "q",
        "sources": [
            {"url": " https://a.example/x ", "title": "A"},
            {"url": "  ", "content": "some text"},
            {"content": "more", "bogus": 1},
        ],
        "sources_mode": "context",
        "language": "fr",
        "mode": "long",
    }

    package, standalone = await run_both(local_staan, "answer", arguments, Script())

    assert standalone == package
    assert package.bodies[0]["sources"] == [
        {"url": "https://a.example/x", "title": "A"},
        {"content": "some text"},
        {"content": "more"},
    ]


UPSTREAM_ERRORS: list[tuple[str, Reply]] = [
    ("400-list", Reply(400, b'{"message":["market must be valid","q too short"]}')),
    ("400-string", Reply(400, b'{"message":"  bad  "}')),
    ("400-empty", Reply(400, b"")),
    ("401", Reply(401, b'{"message":"Unauthorized","statusCode":401}')),
    ("401-text", Reply(401, b"nope")),
    ("403-disabled", Reply(403, b'{"code":"KEY_DISABLED","message":"API key is disabled"}')),
    ("403-forbidden", Reply(403, b'{"message":"Forbidden"}')),
    ("404", Reply(404, b'{"message":"Not found"}')),
    ("418", Reply(418, b"")),
    ("422", Reply(422, b'{"message":"unprocessable"}')),
    ("429", Reply(429, b'{"message":"slow down"}', {"Retry-After": "1"})),
    ("500", Reply(500, b'{"message":"internal secret"}')),
    ("502", Reply(502, b"")),
    ("503", Reply(503, b"")),
    ("long-message", Reply(400, json.dumps({"message": "x" * 700}).encode())),
    ("non-json-ok", Reply(200, b"<html>")),
    ("array-ok", Reply(200, b"[1, 2]")),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reply", [reply for _, reply in UPSTREAM_ERRORS], ids=[name for name, _ in UPSTREAM_ERRORS]
)
@pytest.mark.parametrize("tool", ["web_search", "answer"])
async def test_upstream_failures_are_reported_in_the_same_words(
    local_staan: LocalStaan, tool: str, reply: Reply
) -> None:
    script = Script(search=reply, answer=reply)

    package, standalone = await run_both(local_staan, tool, {"query": "q"}, script)

    assert standalone == package
    assert package.is_error
    assert package.text.startswith(f"Error executing tool {tool}: ")
    assert package.structured is None
    assert "internal secret" not in package.text


@pytest.mark.anyio
async def test_a_search_that_fails_in_every_market_is_reported_in_the_same_words(
    local_staan: LocalStaan,
) -> None:
    script = Script(search=Reply(503))

    package, standalone = await run_both(
        local_staan, "web_search", {"query": "q", "market": ["fr-fr", "en-gb"]}, script
    )
    assert standalone == package
    assert package.text == error_text(
        "web_search",
        "fr-fr: Staan is temporarily unavailable (HTTP 503). Try again later.; "
        "en-gb: Staan is temporarily unavailable (HTTP 503). Try again later.",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "markets", [["fr-fr", "de-de"], ["de-de", "fr-fr"], ["en-gb", "de-de", "fr-fr"]]
)
async def test_a_rejected_key_in_one_market_ends_the_search_in_the_same_words(
    local_staan: LocalStaan, markets: list[str]
) -> None:
    # The failing 401 waits until the requests of all the markets have reached the upstream, as
    # it would on a real network (an answer cannot beat the other requests of the same call
    # onto the wire). Without that the standalone server, which cannot take a request back from
    # its socket once a thread has sent it, was racing its own threads, and the two sides
    # sometimes sent different sets of requests.
    script = Script(search=Reply(503), by_market={"de-de": Reply(401, b"{}", after=len(markets))})

    package, standalone = await run_both(
        local_staan, "web_search", {"query": "q", "market": markets}, script
    )

    assert standalone == package
    assert [body["market"] for body in package.bodies] == sorted(markets)
    assert package.text.startswith(
        error_text("web_search", "Staan authentication failed (HTTP 401)")
    )


@pytest.mark.anyio
async def test_a_call_that_the_deadline_cuts_short_is_reported_in_the_same_words(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both answer 503, then hang. The standalone server really waits 0.3 s before its retry (the
    # package's retry wait is instant), which leaves less than the 0.6 s timeout of the deadline:
    # the hung attempt is ended by the deadline, not by the timeout, and says so in the package's
    # words.
    monkeypatch.setattr(stdio_server, "backoff_delay", lambda _attempt, _retry_after: 0.3)
    limits: dict[str, Any] = {"timeout": 0.6, "deadline": 0.8, "max_retries": 2}
    attempts = 0

    async def hang_after_a_503(_request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(503)
        await anyio.Event().wait()  # hangs until the deadline cancels the call
        msg = "the deadline should have cancelled this call"
        raise AssertionError(msg)

    fake = FakeStaan()
    fake.search = hang_after_a_503
    package = text_of(await call(fake, "web_search", {"query": "q"}, **limits))
    local_staan.search = [Reply(503), Reply(201, b"{}", delay=3.0)]
    base = Settings(api_key=API_KEY, base_url=local_staan.base_url)
    result = stdio_server.call_tool(
        "web_search", {"query": "q"}, replace(base, **limits), threading.Event()
    )

    expected = error_text("web_search", "en-gb: The call to Staan did not finish within 0.8 s.")
    assert package == expected
    assert result["content"] == [{"type": "text", "text": expected}]
    assert result["isError"] is True
    assert attempts == len(local_staan.requests) == 2


@pytest.mark.anyio
async def test_a_response_without_an_answer_text_is_reported_in_the_same_words(
    local_staan: LocalStaan,
) -> None:
    script = Script(answer=reply_of({"answer": None, "citations": []}))
    for simple_response in (True, False):
        package, standalone = await run_both(
            local_staan, "answer", {"query": "q", "simple_response": simple_response}, script
        )

        assert standalone == package
        assert package.text == error_text(
            "answer", "Staan returned a response without an answer text."
        )


class RecordedWaits(threading.Event):
    """An event whose ``wait`` records the requested delay instead of sleeping."""

    def __init__(self) -> None:
        """Start unset, with no recorded waits."""
        super().__init__()
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        """Record ``timeout`` and report that the event was not set."""
        self.waits.append(timeout)
        return False


async def package_waits(replies: list[Reply], max_retries: int) -> tuple[int, list[float]]:
    """The requests made and the delays slept by the package's client for ``replies``."""
    slept: list[float] = []
    queue = iter(replies)
    requests = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        return httpx_reply(next(queue))

    async def sleep(delay: float) -> None:
        slept.append(delay)

    settings = Settings(api_key=API_KEY, max_retries=max_retries)
    async with StaanClient(
        settings, transport=httpx2.MockTransport(handler), sleep=sleep
    ) as client:
        with contextlib.suppress(Exception):  # only the schedule matters here
            await client.search_web(query="q", market="en-gb")
    return requests, slept


def standalone_waits(
    upstream: LocalStaan, replies: list[Reply], max_retries: int
) -> tuple[int, list[float]]:
    """The requests made and the delays waited by the standalone server for ``replies``."""
    upstream.search = replies
    settings = Settings(
        api_key=API_KEY, base_url=upstream.base_url, timeout=5.0, max_retries=max_retries
    )
    stop = RecordedWaits()
    with contextlib.suppress(Exception):  # only the schedule matters here
        stdio_server._post(settings, "search", {"q": "q", "market": "en-gb"}, stop)
    return len(upstream.requests), [wait or 0.0 for wait in stop.waits]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500, 501, 502, 503, 504])
async def test_the_same_statuses_are_retried_with_the_same_schedule(
    local_staan: LocalStaan, status: int
) -> None:
    replies = [
        Reply(status, headers={"Retry-After": "2"}),
        Reply(status, headers={"Retry-After": "x"}),
        Reply(status),
        Reply(201, b"{}"),
    ]

    expected = await package_waits(replies, max_retries=2)
    actual = standalone_waits(local_staan, replies, max_retries=2)

    assert actual == expected


@pytest.mark.anyio
async def test_a_recovering_upstream_gives_the_same_requests_and_delays(
    local_staan: LocalStaan,
) -> None:
    replies = [Reply(503, headers={"Retry-After": "40"}), Reply(429), Reply(500), Reply(201, b"{}")]

    expected = await package_waits(replies, max_retries=3)
    actual = standalone_waits(local_staan, replies, max_retries=3)

    assert actual == expected
    assert expected == (4, [30.5, 1.0, 2.0])  # Retry-After is capped at 30 s


# --------------------------------------------------------------------------------------
# 3. Which arguments are accepted
# --------------------------------------------------------------------------------------

# (tool, arguments, how the rejection text compares: SAME word for word, WRAPPED inside the
# package's pydantic report, or DIFFERENT because the two validators word it differently)
SAME, WRAPPED, DIFFERENT = "same", "wrapped", "different"
ARGUMENT_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("web_search", {"query": "x"}, SAME),
    ("web_search", {"query": "x", "bogus": 1}, SAME),
    ("web_search", {"query": "x", "offset": 10.0}, SAME),
    ("web_search", {"query": "x", "simple_response": "yes"}, SAME),
    ("web_search", {"query": "x", "simple_response": 1}, SAME),
    ("web_search", {"query": "x", "simple_response": "FALSE"}, SAME),
    ("web_search", {"query": "x", "market": ["en-gb", "FR-FR"]}, SAME),
    ("web_search", {"query": "x", "include_domains": None, "exclude_domains": ["a.com"]}, SAME),
    ("web_search", {"query": "x" * 400}, SAME),
    ("web_search", {}, DIFFERENT),
    ("web_search", {"query": ""}, DIFFERENT),
    ("web_search", {"query": "x" * 401}, DIFFERENT),
    ("web_search", {"query": 5}, DIFFERENT),
    ("web_search", {"query": None}, DIFFERENT),
    ("web_search", {"query": "x", "market": 5}, DIFFERENT),
    ("web_search", {"query": "x", "market": None}, DIFFERENT),
    ("web_search", {"query": "x", "market": [1]}, DIFFERENT),
    ("web_search", {"query": "x", "market": "zz"}, SAME),
    ("web_search", {"query": "x", "market": "zz-*"}, SAME),
    ("web_search", {"query": "x", "market": ""}, SAME),
    ("web_search", {"query": "x", "market": []}, SAME),
    ("web_search", {"query": "x", "offset": 5}, DIFFERENT),
    ("web_search", {"query": "x", "offset": "10"}, DIFFERENT),
    ("web_search", {"query": "x", "offset": True}, DIFFERENT),
    ("web_search", {"query": "x", "offset": None}, DIFFERENT),
    ("web_search", {"query": "x", "offset": 10.5}, DIFFERENT),
    ("web_search", {"query": "x", "offset": False}, SAME),
    ("web_search", {"query": "x", "offset": 0}, SAME),
    ("web_search", {"query": "x", "offset": -0.0}, SAME),
    ("web_search", {"query": "x", "offset": 30.0}, SAME),
    ("web_search", {"query": "x", "offset": 1}, DIFFERENT),
    ("web_search", {"query": "x", "offset": 1.0}, DIFFERENT),
    ("web_search", {"query": "x", "offset": [10]}, DIFFERENT),
    ("web_search", {"query": "x", "full_content": "pdf"}, DIFFERENT),
    ("web_search", {"query": "x", "full_content": None}, DIFFERENT),
    ("web_search", {"query": "x", "include_domains": "a.com"}, DIFFERENT),
    ("web_search", {"query": "x", "include_domains": [1]}, DIFFERENT),
    ("web_search", {"query": "x", "include_domains": ["not a domain"]}, SAME),
    (
        "web_search",
        {"query": "x", "include_domains": ["a.com"], "exclude_domains": ["b.com"]},
        SAME,
    ),
    ("web_search", {"query": "x", "exclude_domains": [f"d{i}.com" for i in range(11)]}, SAME),
    ("web_search", {"query": "x", "simple_response": None}, DIFFERENT),
    ("web_search", {"query": "x", "simple_response": "maybe"}, DIFFERENT),
    ("web_search", {"query": "x", "simple_response": 2}, DIFFERENT),
    ("answer", {"query": "x"}, SAME),
    ("answer", {"query": "x", "filter": None}, SAME),
    ("answer", {"query": "x", "filter": ""}, SAME),
    ("answer", {"query": "x", "filter": "https://www.lemonde.fr/x"}, SAME),
    ("answer", {"query": "x", "filter": "not a domain"}, SAME),
    ("answer", {"query": "x", "filter": 5}, DIFFERENT),
    ("answer", {"query": "x" * 2001}, DIFFERENT),
    ("answer", {"query": "x", "language": "de"}, DIFFERENT),
    ("answer", {"query": "x", "mode": None}, DIFFERENT),
    ("answer", {"query": "x", "sources_mode": "x"}, DIFFERENT),
    ("answer", {"query": "x", "sources": None}, SAME),
    ("answer", {"query": "x", "sources": []}, SAME),
    ("answer", {"query": "x", "sources": "a"}, DIFFERENT),
    ("answer", {"query": "x", "sources": [1]}, DIFFERENT),
    ("answer", {"query": "x", "sources": [{}]}, WRAPPED),
    ("answer", {"query": "x", "sources": [{"url": "  "}]}, WRAPPED),
    ("answer", {"query": "x", "sources": [{"url": 5}]}, DIFFERENT),
    ("answer", {"query": "x", "sources": [{"url": "https://a.example", "bogus": 1}]}, SAME),
    ("answer", {"query": "x", "sources": [{"url": "https://a.example"}] * 10}, SAME),
    ("answer", {"query": "x", "sources": [{"url": "https://a.example"}] * 11}, DIFFERENT),
    ("answer", {"query": "x", "sources": [{"content": "c" * 50_000}]}, SAME),
    ("answer", {"query": "x", "sources": [{"content": "c" * 50_001}]}, DIFFERENT),
    (
        "answer",
        {"query": "x", "sources": [{"url": "https://a.example", "title": "t" * 501}]},
        DIFFERENT,
    ),
]
SOURCE_URL_PROBLEMS = [
    "file:///etc/passwd",
    "http://localhost/x",
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/",
    "http://10.0.0.1/",
    "ftp://example.com/",
    "https:///nohost",
    "https://exa mple.com/",
    "https://example.com/\u0007",
    "https://example.com:notaport/",
    "https://example.com/" + "a" * 2048,
]


def assert_same_acceptance(package: Outcome, standalone: Outcome, tool: str, text: str) -> None:
    """Both accept and answer identically, or both reject (and the text compares as ``text``)."""
    assert standalone.is_error == package.is_error
    if package.is_error:
        assert standalone.text.startswith("Error executing tool ")
        assert package.text.startswith("Error executing tool ")
        assert not standalone.bodies
        assert not package.bodies
        reason = standalone.text.removeprefix(f"Error executing tool {tool}: ")
        if text == SAME:
            assert standalone.text == package.text
        elif text == WRAPPED:
            assert reason in package.text
    else:
        assert standalone == package


@pytest.mark.anyio
@pytest.mark.parametrize(("tool", "arguments", "text"), ARGUMENT_CASES)
async def test_the_same_arguments_are_accepted_and_rejected(
    local_staan: LocalStaan, tool: str, arguments: dict[str, Any], text: str
) -> None:
    script = Script(answer=reply_of(fixture("answer_normal.json")))

    package, standalone = await run_both(local_staan, tool, arguments, script)

    assert_same_acceptance(package, standalone, tool, text)


@pytest.mark.anyio
@pytest.mark.parametrize("url", SOURCE_URL_PROBLEMS)
async def test_unsafe_source_urls_are_rejected_with_the_same_reason(
    local_staan: LocalStaan, url: str
) -> None:
    arguments = {"query": "x", "sources": [{"url": url}]}

    package, standalone = await run_both(local_staan, "answer", arguments, Script())

    assert package.is_error
    assert standalone.is_error
    assert not package.bodies
    assert not standalone.bodies
    reason = standalone.text.removeprefix("Error executing tool answer: ")
    assert reason.startswith("A source 'url' ")
    assert reason in package.text  # pydantic wraps it as "Value error, <reason> [type=...]"


# --------------------------------------------------------------------------------------
# 4. Configuration
# --------------------------------------------------------------------------------------

BAD_ENVIRONMENTS: list[dict[str, str]] = [
    {"STAAN_API_KEY": "bad key"},
    {"STAAN_API_KEY": "cl\u00e9"},
    {"STAAN_BASE_URL": "ftp://api.example.com"},
    {"STAAN_BASE_URL": "not a url"},
    {"STAAN_BASE_URL": "http://api.example.com"},
    {"STAAN_BASE_URL": "https://user:secret@api.example.com"},
    {"STAAN_BASE_URL": "https://api.example.com:99999"},
    {"STAAN_TIMEOUT": "abc"},
    {"STAAN_TIMEOUT": "0"},
    {"STAAN_TIMEOUT": "-1"},
    {"STAAN_TIMEOUT": "nan"},
    {"STAAN_TIMEOUT": "inf"},
    {"STAAN_DEADLINE": "soon"},
    {"STAAN_DEADLINE": "0"},
    {"STAAN_MAX_RETRIES": "many"},
    {"STAAN_MAX_RETRIES": "11"},
    {"STAAN_MAX_RETRIES": "-1"},
    {"STAAN_MCP_MAX_CONTENT_CHARS": "lots"},
    {"STAAN_MCP_MAX_CONTENT_CHARS": "199"},
    {"STAAN_MCP_MAX_CONTENT_CHARS": "-5"},
    # Several problems at once: the same one is reported first.
    {"STAAN_MAX_RETRIES": "99", "STAAN_TIMEOUT": "abc"},
    {"STAAN_MCP_MAX_CONTENT_CHARS": "5", "STAAN_API_KEY": "bad key", "STAAN_DEADLINE": "x"},
    {"STAAN_BASE_URL": "ftp://x", "STAAN_MAX_RETRIES": "99", "STAAN_MCP_MAX_CONTENT_CHARS": "5"},
]
GOOD_ENVIRONMENTS: list[dict[str, str]] = [
    {},
    {"STAAN_API_KEY": "  padded-key  "},
    {"STAAN_API_KEY": ""},
    {"STAAN_BASE_URL": "https://api.example.com/v2///"},
    {"STAAN_BASE_URL": "http://localhost:8080"},
    {"STAAN_TIMEOUT": " 2.5 ", "STAAN_DEADLINE": "1e2"},
    {"STAAN_MAX_RETRIES": "0"},
    {"STAAN_MAX_RETRIES": "10"},
    {"STAAN_MCP_MAX_CONTENT_CHARS": "0"},
    {"STAAN_MCP_MAX_CONTENT_CHARS": "200"},
]


@pytest.fixture(autouse=True)
def no_staan_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start without any ``STAAN_*`` setting the shell may have; restore the root logger after.

    ``cli.main`` reconfigures logging, which would leak into the tests that read log records.
    """
    for name in [name for name in os.environ if name.startswith("STAAN_")]:
        monkeypatch.delenv(name)
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


def serve_main(monkeypatch: pytest.MonkeyPatch, *messages: object) -> list[dict[str, Any]]:
    """Run the standalone ``main()`` after a handshake; return what it wrote after that."""
    sent = [*HANDSHAKE, *messages]
    stdin = io.TextIOWrapper(io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in sent)))
    written = io.BytesIO()
    stdout = io.TextIOWrapper(written)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    assert stdio_server.main([]) == 0
    stdout.flush()
    replies = [json.loads(line) for line in written.getvalue().splitlines()]
    return [reply for reply in replies if reply.get("id") != HANDSHAKE[0]["id"]]


CALL_SEARCH = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "web_search", "arguments": {"query": "x"}},
}


@pytest.mark.parametrize(
    "environ", BAD_ENVIRONMENTS, ids=[str(sorted(e.items())) for e in BAD_ENVIRONMENTS]
)
def test_a_bad_environment_gets_the_same_message_from_both(
    environ: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as excinfo:
        Settings.from_env()
    message = str(excinfo.value)

    assert cli.main([]) == 2  # the package refuses to start
    assert capsys.readouterr().err == f"{cli.PROG}: error: {message}\n"
    (reply,) = serve_main(monkeypatch, CALL_SEARCH)  # the standalone server says it per call
    assert reply["result"] == {
        "content": [{"type": "text", "text": error_text("web_search", message)}],
        "isError": True,
    }
    assert "cl\u00e9" not in message
    assert "bad key" not in message
    assert "secret" not in message
    assert "configuration problem (tools will report it)" in capsys.readouterr().err


@pytest.mark.parametrize(
    "environ", GOOD_ENVIRONMENTS, ids=[str(sorted(e.items())) for e in GOOD_ENVIRONMENTS]
)
def test_a_good_environment_is_accepted_by_both(
    environ: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    Settings.from_env()  # raises if the package would refuse it

    (reply,) = serve_main(monkeypatch, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert reply["result"] == {"tools": TOOL_LIST}
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------------------
# 5. The two real processes
# --------------------------------------------------------------------------------------

STANDALONE = [sys.executable, str(Path(stdio_server.__file__))]
PACKAGE = [sys.executable, "-m", "staan_mcp"]


def request(request_id: int, method: str, params: object = None) -> dict[str, Any]:
    """A JSON-RPC request."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def tool_call(request_id: int, name: str, arguments: object) -> dict[str, Any]:
    """A ``tools/call`` request."""
    return request(request_id, "tools/call", {"name": name, "arguments": arguments})


def exchange(command: list[str], upstream: LocalStaan) -> list[dict[str, Any]]:
    """Run the protocol script against ``command`` and return the normalised answers."""
    env = clean_environment({"STAAN_API_KEY": API_KEY, "STAAN_BASE_URL": upstream.base_url})
    answers: list[dict[str, Any]] = []
    with RpcProcess(command, env) as process:
        answers.append(process.call(INITIALIZE))
        process.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answers += [
            process.call(message)
            for message in [
                request(2, "ping"),
                request(3, "tools/list"),
                request(4, "prompts/list"),
                request(5, "resources/list"),
                request(6, "resources/templates/list"),
                request(7, "bogus/method"),
                request(8, "tools/call", {"name": "web_search", "arguments": []}),
                tool_call(9, "nope", {}),
                tool_call(10, "web_search", {"query": "x", "market": "zz"}),
                tool_call(
                    11,
                    "web_search",
                    {"query": "x", "include_domains": ["a.com"], "exclude_domains": ["b.com"]},
                ),
                tool_call(
                    12,
                    "web_search",
                    {"query": "python", "market": ["en-gb", "fr-fr"], "full_content": "none"},
                ),
                tool_call(
                    13, "answer", {"query": "capital?", "filter": "https://www.Example.com/x"}
                ),
                tool_call(14, "answer", {"query": "capital?", "simple_response": False}),
            ]
        ]
    for answer in answers:
        result = answer.get("result", {})
        if result.get("isError") is False:  # the SDK spells out the default, the script omits it
            del result["isError"]
    return answers


def test_the_two_processes_answer_the_whole_protocol_script_identically(
    local_staan: LocalStaan,
) -> None:
    local_staan.search = reply_of(fixture("search_extra_snippets.json"))
    local_staan.answer = reply_of(fixture("answer_repeated_citations.json"))

    package = exchange(PACKAGE, local_staan)
    package_bodies = by_market(local_staan.bodies())
    local_staan.requests.clear()
    standalone = exchange(STANDALONE, local_staan)
    standalone_bodies = by_market(local_staan.bodies())

    assert len(package) == len(standalone) == 14
    for expected, actual in zip(package, standalone, strict=True):
        assert actual == expected
    assert package[0]["result"]["serverInfo"] == {"name": "staan", "version": stdio_server.VERSION}
    assert package[2]["result"] == {"tools": TOOL_LIST}
    assert standalone_bodies == package_bodies
    assert len(package_bodies) == 4  # two markets, then two answers


def test_both_processes_negotiate_the_protocol_version_the_same_way() -> None:
    results = []
    for command in (PACKAGE, STANDALONE):
        with RpcProcess(command, clean_environment({"STAAN_API_KEY": API_KEY})) as process:
            versions = []
            for requested in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "1999-01-01"):
                params = {**INITIALIZE["params"], "protocolVersion": requested}
                versions.append(
                    process.call(request(1, "initialize", params))["result"]["protocolVersion"]
                )
        results.append(versions)

    assert (
        results[0]
        == results[1]
        == ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2025-11-25"]
    )


def test_the_standalone_process_reports_a_missing_key_like_the_package() -> None:
    texts = []
    for command in (PACKAGE, STANDALONE):
        with RpcProcess(command, clean_environment({})) as process:
            initialize(process)
            texts.append(process.call(tool_call(2, "web_search", {"query": "x"}))["result"])

    assert texts[0] == texts[1]
    assert texts[0]["isError"] is True
    assert texts[0]["content"][0]["text"].startswith(
        "Error executing tool web_search: STAAN_API_KEY is not set."
    )
