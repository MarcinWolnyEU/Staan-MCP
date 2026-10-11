"""Tests for the async Staan client, driven through ``httpx2.MockTransport``."""
# Exact-class assertions (type(x) is Y) are deliberate: subclasses must not satisfy them.
# pylint: disable=unidiomatic-typecheck,protected-access,too-many-lines

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio
import httpx2
import pytest

from staan_mcp import __version__
from staan_mcp.client import StaanClient
from staan_mcp.config import Settings
from staan_mcp.errors import (
    ConfigurationError,
    StaanAPIError,
    StaanAuthenticationError,
    StaanForbiddenError,
    StaanKeyDisabledError,
    StaanRateLimitError,
    StaanResponseError,
    StaanServerError,
    StaanTransportError,
    StaanValidationError,
)
from staan_mcp.upstream import MAX_RESPONSE_BYTES, SEARCH_COUNT

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

API_KEY = "test-key-0123456789"
BASE_URL = "https://api.staan.ai/v2"
USER_AGENT = f"staan-mcp/{__version__} (+https://github.com/MarcinWolnyEU/Staan-MCP)"

OK_SEARCH = {"search_id": "abc", "query": {"q": "x"}, "web": {"results": []}}
OK_ANSWER = {
    "request_id": "r",
    "answer": "Hi",
    "citations": [],
    "sources": [],
    "related_queries": [],
    "usages": [],
}

BODY_400 = json.dumps(
    {
        "message": ["count must be equal to 10", "offset must be a factor of 10"],
        "error": "Bad Request",
        "statusCode": 400,
    }
).encode()
BODY_401 = b'{"message":"Unauthorized","statusCode":401}'
BODY_401_GATEWAY = b'{"code":"INVALID_CREDENTIALS","message":"Unknown API key or invalid secret"}'
BODY_403_DISABLED = b'{"code":"KEY_DISABLED","message":"API key is disabled"}'
BODY_403_FORBIDDEN = b'{"message":"Forbidden","statusCode":403}'

Reply = httpx2.Response | Exception


def ok(payload: object = None, status: int = 200) -> httpx2.Response:
    """Build a successful JSON response."""
    return httpx2.Response(status, json=OK_SEARCH if payload is None else payload)


def failure(status: int, body: bytes = b"", **headers: str) -> httpx2.Response:
    """Build an error response with a raw body and optional headers."""
    wire = {name.replace("_", "-"): value for name, value in headers.items()}
    return httpx2.Response(status, content=body, headers=wire)


@dataclass
class Upstream:
    """A recording MockTransport handler that replays queued replies.

    The last reply is repeated when the queue runs dry, so ``Upstream(failure(500))`` fails every
    attempt.
    """

    replies: list[Reply]
    requests: list[httpx2.Request] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def only(self) -> httpx2.Request:
        """The single recorded request."""
        assert len(self.requests) == 1
        return self.requests[0]

    def json_body(self, index: int = 0) -> dict[str, Any]:
        """Decode the JSON body of a recorded request."""
        body = json.loads(self.requests[index].content)
        assert isinstance(body, dict)
        return body


@dataclass
class FakeSleep:
    """Records requested delays instead of sleeping."""

    delays: list[float] = field(default_factory=list)

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@dataclass
class Rig:
    """A client wired to a recording upstream and a fake sleep."""

    client: StaanClient
    upstream: Upstream
    sleep: FakeSleep


def make_rig(*replies: Reply, **settings: Any) -> Rig:
    """Create a rig; ``settings`` override :class:`Settings` fields (default key: API_KEY)."""
    upstream = Upstream(list(replies) or [ok()])
    sleep = FakeSleep()
    config = Settings(**{"api_key": API_KEY, **settings})
    client = StaanClient(config, transport=httpx2.MockTransport(upstream), sleep=sleep)
    return Rig(client, upstream, sleep)


async def search(rig: Rig, **overrides: Any) -> dict[str, Any]:
    """Run a search through ``rig`` and close its client."""
    async with rig.client:
        return await rig.client.search_web(**{"query": "python", "market": "en-gb", **overrides})


async def ask(rig: Rig, **overrides: Any) -> dict[str, Any]:
    """Run an answer call through ``rig`` and close its client."""
    async with rig.client:
        return await rig.client.answer(**{"query": "why?", **overrides})


pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------------------
# Missing key
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("api_key", [None, ""])
@pytest.mark.parametrize("call", [search, ask])
async def test_missing_key_raises_configuration_error_before_any_request(
    api_key: str | None, call: Callable[..., Any]
) -> None:
    rig = make_rig(ok(), api_key=api_key)

    with pytest.raises(ConfigurationError) as excinfo:
        await call(rig)

    assert str(excinfo.value) == (
        "STAAN_API_KEY is not set. Create a key in the Staan console and set the "
        "STAAN_API_KEY environment variable for the MCP server."
    )
    assert not rig.upstream.requests
    assert not rig.sleep.delays


# --------------------------------------------------------------------------------------
# Search request shape
# --------------------------------------------------------------------------------------


async def test_search_sends_the_exact_default_request() -> None:
    rig = make_rig(ok())

    result = await search(rig)

    request = rig.upstream.only
    assert request.method == "POST"
    assert str(request.url) == "https://api.staan.ai/v2/search/web"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["content-type"] == "application/json"
    assert rig.upstream.json_body() == {
        "q": "python",
        "market": "en-gb",
        "count": 10,
        "offset": 0,
        "extra_snippets": True,
    }
    assert result == OK_SEARCH


def test_search_count_constant_is_ten() -> None:
    assert SEARCH_COUNT == 10


@pytest.mark.parametrize("offset", [0, 10, 20, 30])
async def test_search_forwards_offset_and_always_sends_count_ten(offset: int) -> None:
    rig = make_rig()

    await search(rig, offset=offset)

    body = rig.upstream.json_body()
    assert body["offset"] == offset
    assert body["count"] == 10
    assert body["extra_snippets"] is True


@pytest.mark.parametrize("fmt", ["markdown", "html"])
async def test_search_forwards_full_content_format(fmt: str) -> None:
    rig = make_rig()

    await search(rig, full_content=fmt)

    assert rig.upstream.json_body()["full_content"] == fmt


async def test_search_omits_full_content_when_none() -> None:
    rig = make_rig()

    await search(rig, full_content=None)

    assert "full_content" not in rig.upstream.json_body()


async def test_search_forwards_market_and_query_verbatim() -> None:
    rig = make_rig()

    await search(rig, query='site:example.com "exact phrase" é', market="de-de")

    body = rig.upstream.json_body()
    assert body["q"] == 'site:example.com "exact phrase" é'
    assert body["market"] == "de-de"


async def test_search_sends_include_domains_only() -> None:
    rig = make_rig()

    await search(rig, include_domains=["a.com", "b.org"])

    body = rig.upstream.json_body()
    assert body["include_domains"] == ["a.com", "b.org"]
    assert "exclude_domains" not in body


async def test_search_sends_exclude_domains_only() -> None:
    rig = make_rig()

    await search(rig, exclude_domains=("a.com",))

    body = rig.upstream.json_body()
    assert body["exclude_domains"] == ["a.com"]
    assert "include_domains" not in body


@pytest.mark.parametrize("empty", [None, [], ()])
async def test_search_omits_empty_domain_lists(empty: Sequence[str] | None) -> None:
    rig = make_rig()

    await search(rig, include_domains=empty, exclude_domains=empty)

    body = rig.upstream.json_body()
    assert "include_domains" not in body
    assert "exclude_domains" not in body


async def test_search_never_sends_min_score_or_max_snippets() -> None:
    rig = make_rig()

    await search(
        rig,
        offset=30,
        full_content="html",
        include_domains=["a.com"],
        exclude_domains=["b.com"],
    )

    assert set(rig.upstream.json_body()) == {
        "q",
        "market",
        "count",
        "offset",
        "extra_snippets",
        "full_content",
        "include_domains",
        "exclude_domains",
    }


@pytest.mark.parametrize("status", [200, 201])
async def test_search_accepts_200_and_201(status: int) -> None:
    rig = make_rig(ok(status=status))

    assert await search(rig) == OK_SEARCH
    assert len(rig.upstream.requests) == 1


async def test_search_returns_the_decoded_body_untouched() -> None:
    payload = {
        "web": {"results": [{"title": "T", "extra_snippets": [{"chunk": "c", "score": 0.5}]}]}
    }
    rig = make_rig(ok(payload))

    assert await search(rig) == payload


@pytest.mark.parametrize("base", [BASE_URL, f"{BASE_URL}/", f"{BASE_URL}///"])
async def test_trailing_slashes_in_base_url_do_not_double_up(base: str) -> None:
    rig = make_rig(base_url=base)

    await search(rig)

    assert str(rig.upstream.only.url) == "https://api.staan.ai/v2/search/web"


async def test_only_slashes_are_trimmed_from_the_end_of_the_base_url() -> None:
    rig = make_rig(base_url="https://api.staan.ai/vX/")

    await search(rig)

    assert str(rig.upstream.only.url) == "https://api.staan.ai/vX/search/web"


async def test_custom_base_url_is_used_for_search_and_answer() -> None:
    rig = make_rig(ok(OK_ANSWER), base_url="http://localhost:9000/v9")

    await ask(rig)

    assert str(rig.upstream.only.url) == "http://localhost:9000/v9/answer"


# --------------------------------------------------------------------------------------
# Answer request shape
# --------------------------------------------------------------------------------------


async def test_answer_sends_the_exact_default_request() -> None:
    rig = make_rig(ok(OK_ANSWER, status=200))

    result = await ask(rig)

    request = rig.upstream.only
    assert request.method == "POST"
    assert str(request.url) == "https://api.staan.ai/v2/answer"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["content-type"] == "application/json"
    assert rig.upstream.json_body() == {
        "query": "why?",
        "language": "en",
        "mode": "short",
        "stream": False,
        "markdown": True,
    }
    assert result == OK_ANSWER


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("mode", ["short", "long"])
async def test_answer_forwards_language_and_mode(language: str, mode: str) -> None:
    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, language=language, mode=mode)

    body = rig.upstream.json_body()
    assert body["language"] == language
    assert body["mode"] == mode


async def test_answer_forwards_filter_under_the_upstream_name() -> None:
    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, filter_domain="frandroid.com")

    body = rig.upstream.json_body()
    assert body["filter"] == "frandroid.com"
    assert "filter_domain" not in body
    assert "sources" not in body
    assert "sources_mode" not in body


@pytest.mark.parametrize("empty_filter", [None, ""])
async def test_answer_omits_unset_filter(empty_filter: str | None) -> None:
    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, filter_domain=empty_filter)

    assert "filter" not in rig.upstream.json_body()


@pytest.mark.parametrize("sources_mode", ["search", "context"])
async def test_answer_sends_sources_with_sources_mode(sources_mode: str) -> None:
    sources = [
        {"url": "https://a.com/x", "title": "A"},
        {"content": "Some text", "title": "B"},
    ]
    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, sources=sources, sources_mode=sources_mode)

    body = rig.upstream.json_body()
    assert body["sources"] == sources
    assert body["sources_mode"] == sources_mode


async def test_answer_defaults_sources_mode_to_search_when_sources_given() -> None:
    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, sources=[{"url": "https://a.com"}])

    assert rig.upstream.json_body()["sources_mode"] == "search"


@pytest.mark.parametrize("empty", [None, []])
async def test_answer_omits_sources_mode_without_sources(empty: list[Any] | None) -> None:
    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, sources=empty, sources_mode="context")

    body = rig.upstream.json_body()
    assert "sources" not in body
    assert "sources_mode" not in body


async def test_answer_copies_source_mappings_into_plain_dicts() -> None:
    class ReadOnly(dict[str, str]):
        """A dict subclass standing in for any Mapping implementation."""

    rig = make_rig(ok(OK_ANSWER))

    await ask(rig, sources=[ReadOnly(url="https://a.com")])

    assert rig.upstream.json_body()["sources"] == [{"url": "https://a.com"}]


async def test_answer_never_sends_history_streaming_or_optional_extras() -> None:
    rig = make_rig(ok(OK_ANSWER))

    await ask(
        rig,
        language="fr",
        mode="long",
        filter_domain="a.com",
        sources=[{"url": "https://b.com"}],
        sources_mode="context",
    )

    body = rig.upstream.json_body()
    assert set(body) == {
        "query",
        "language",
        "mode",
        "stream",
        "markdown",
        "filter",
        "sources",
        "sources_mode",
    }
    assert body["stream"] is False
    assert body["markdown"] is True


@pytest.mark.parametrize("status", [200, 201])
async def test_answer_accepts_200_and_201(status: int) -> None:
    rig = make_rig(ok(OK_ANSWER, status=status))

    assert await ask(rig) == OK_ANSWER


# --------------------------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------------------------

RETRY_STATUSES = [
    (429, StaanRateLimitError),
    (500, StaanServerError),
    (502, StaanServerError),
    (503, StaanServerError),
    (504, StaanServerError),
]


@pytest.mark.parametrize(("status", "_cls"), RETRY_STATUSES)
async def test_transient_status_is_retried_with_exponential_backoff(
    status: int, _cls: type[StaanAPIError]
) -> None:
    rig = make_rig(failure(status), failure(status), ok(), max_retries=2)

    assert await search(rig) == OK_SEARCH

    assert len(rig.upstream.requests) == 3
    assert rig.sleep.delays == [0.5, 1.0]


@pytest.mark.parametrize(("status", "cls"), RETRY_STATUSES)
async def test_transient_status_raises_the_mapped_error_after_retries(
    status: int, cls: type[StaanAPIError]
) -> None:
    rig = make_rig(failure(status), max_retries=2)

    with pytest.raises(StaanAPIError) as excinfo:
        await search(rig)

    assert type(excinfo.value) is cls
    assert excinfo.value.status_code == status
    assert excinfo.value.endpoint == "search"
    assert len(rig.upstream.requests) == 3
    assert rig.sleep.delays == [0.5, 1.0]


async def test_answer_endpoint_is_retried_too_and_errors_name_it() -> None:
    rig = make_rig(failure(503), max_retries=1)

    with pytest.raises(StaanServerError) as excinfo:
        await ask(rig)

    assert excinfo.value.endpoint == "answer"
    assert len(rig.upstream.requests) == 2
    assert rig.sleep.delays == [0.5]


async def test_retried_requests_are_identical() -> None:
    rig = make_rig(failure(500), ok(), max_retries=1)

    await search(rig, offset=20, include_domains=["a.com"])

    first, second = rig.upstream.requests
    assert first.content == second.content
    assert dict(first.headers) == dict(second.headers)
    assert first.url == second.url


async def test_zero_max_retries_means_a_single_attempt_without_sleeping() -> None:
    rig = make_rig(failure(503), ok(), max_retries=0)

    with pytest.raises(StaanServerError):
        await search(rig)

    assert len(rig.upstream.requests) == 1
    assert not rig.sleep.delays


async def test_default_max_retries_is_two_extra_attempts() -> None:
    rig = make_rig(failure(500))

    with pytest.raises(StaanServerError):
        await search(rig)

    assert len(rig.upstream.requests) == 3


async def test_backoff_doubles_up_to_the_eight_second_cap() -> None:
    rig = make_rig(failure(500), max_retries=10)

    with pytest.raises(StaanServerError):
        await search(rig)

    assert len(rig.upstream.requests) == 11
    assert rig.sleep.delays == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0]


async def test_success_after_retry_does_not_sleep_again() -> None:
    rig = make_rig(failure(429), ok(), max_retries=5)

    await search(rig)

    assert rig.sleep.delays == [0.5]
    assert len(rig.upstream.requests) == 2


@pytest.mark.parametrize(
    ("header", "extra"),
    [
        ("3", 3.0),
        ("0", 0.0),
        ("0.5", 0.5),
        ("1.5", 1.5),
        (" 2 ", 2.0),
        ("30", 30.0),
        ("31", 30.0),
        ("100000", 30.0),
    ],
)
async def test_retry_after_is_added_to_the_backoff_and_capped_at_thirty_seconds(
    header: str, extra: float
) -> None:
    rig = make_rig(failure(429, retry_after=header), ok(), max_retries=1)

    await search(rig)

    assert rig.sleep.delays == [0.5 + extra]


@pytest.mark.parametrize(
    "header",
    ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", "", "-5", "-0.1", "nan", "inf", "-inf"],
)
async def test_unusable_retry_after_is_ignored(header: str) -> None:
    rig = make_rig(failure(503, retry_after=header), ok(), max_retries=1)

    await search(rig)

    assert rig.sleep.delays == [0.5]


async def test_retry_after_is_honoured_on_5xx_and_per_attempt() -> None:
    rig = make_rig(
        failure(500, retry_after="2"),
        failure(502, retry_after="4"),
        failure(503),
        ok(),
        max_retries=3,
    )

    await search(rig)

    assert rig.sleep.delays == [0.5 + 2, 1.0 + 4, 2.0]


async def test_retry_after_on_the_final_failure_causes_no_sleep() -> None:
    rig = make_rig(failure(429, retry_after="5"), max_retries=0)

    with pytest.raises(StaanRateLimitError):
        await search(rig)

    assert not rig.sleep.delays


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 422, 501, 505])
async def test_other_statuses_are_never_retried(status: int) -> None:
    rig = make_rig(failure(status), ok(), max_retries=5)

    with pytest.raises(StaanAPIError) as excinfo:
        await search(rig)

    assert excinfo.value.status_code == status
    assert len(rig.upstream.requests) == 1
    assert not rig.sleep.delays


async def test_redirects_are_not_followed_and_map_to_a_server_error() -> None:
    rig = make_rig(failure(302, location="https://evil.example/v2/search/web"), ok())

    with pytest.raises(StaanServerError) as excinfo:
        await search(rig)

    assert excinfo.value.status_code == 302
    assert len(rig.upstream.requests) == 1


async def test_retry_logging_never_contains_the_key(caplog: pytest.LogCaptureFixture) -> None:
    rig = make_rig(failure(503), ok(), max_retries=1)

    with caplog.at_level(logging.DEBUG, logger="staan_mcp.client"):
        await search(rig)

    records = [r for r in caplog.records if r.name == "staan_mcp.client"]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    message = records[0].getMessage()
    assert message == (
        "Staan search request failed (Staan is temporarily unavailable (HTTP 503). "
        "Try again later.); retry 1 of 1 in 0.5 s"
    )
    assert API_KEY not in message
    assert "Bearer" not in message


# --------------------------------------------------------------------------------------
# Transport errors
# --------------------------------------------------------------------------------------


async def test_transport_error_is_retried_then_succeeds() -> None:
    rig = make_rig(httpx2.ConnectError("boom"), httpx2.ReadError("reset"), ok(), max_retries=2)

    assert await search(rig) == OK_SEARCH

    assert len(rig.upstream.requests) == 3
    assert rig.sleep.delays == [0.5, 1.0]


@pytest.mark.parametrize("exc_type", [httpx2.ConnectTimeout, httpx2.PoolTimeout])
async def test_connect_and_pool_timeouts_are_retried(
    exc_type: type[httpx2.TimeoutException],
) -> None:
    # Nothing was sent yet, so a second attempt cannot be billed twice.
    rig = make_rig(exc_type("slow"), ok(), max_retries=1)

    assert await search(rig) == OK_SEARCH

    assert rig.sleep.delays == [0.5]


@pytest.mark.parametrize("exc_type", [httpx2.ReadTimeout, httpx2.WriteTimeout])
async def test_read_and_write_timeouts_are_not_retried(
    exc_type: type[httpx2.TimeoutException],
) -> None:
    # The request may already have been processed (and billed): sending it again would pay twice.
    rig = make_rig(exc_type("slow"), ok(), max_retries=2, timeout=7.5)

    with pytest.raises(StaanTransportError) as excinfo:
        await search(rig)

    assert str(excinfo.value) == "The request to Staan timed out after 7.5 s."
    assert type(excinfo.value.__cause__) is exc_type
    assert len(rig.upstream.requests) == 1
    assert not rig.sleep.delays


async def test_exhausted_transport_errors_raise_a_transport_error() -> None:
    cause = httpx2.ConnectError(f"dns failure for key {API_KEY}")
    rig = make_rig(cause, max_retries=2)

    with pytest.raises(StaanTransportError) as excinfo:
        await search(rig)

    error = excinfo.value
    assert type(error) is StaanTransportError
    assert error.status_code is None
    assert error.code is None
    assert error.endpoint == "search"
    assert str(error) == "Could not complete the request to Staan (ConnectError)."
    assert error.__cause__ is cause
    assert API_KEY not in str(error)
    assert len(rig.upstream.requests) == 3
    assert rig.sleep.delays == [0.5, 1.0]


async def test_exhausted_timeouts_raise_a_transport_error_that_says_timed_out() -> None:
    rig = make_rig(httpx2.ConnectTimeout("slow"), max_retries=1, timeout=12.5)

    with pytest.raises(StaanTransportError) as excinfo:
        await ask(rig)

    assert str(excinfo.value) == "The request to Staan timed out after 12.5 s."
    assert excinfo.value.endpoint == "answer"
    assert type(excinfo.value.__cause__) is httpx2.ConnectTimeout
    assert len(rig.upstream.requests) == 2


async def test_transport_errors_are_not_retried_when_retries_are_off() -> None:
    rig = make_rig(httpx2.ConnectError("boom"), ok(), max_retries=0)

    with pytest.raises(StaanTransportError):
        await search(rig)

    assert len(rig.upstream.requests) == 1
    assert not rig.sleep.delays


async def test_non_transport_request_errors_are_wrapped_without_retrying() -> None:
    cause = httpx2.DecodingError("bad gzip")
    rig = make_rig(cause, ok(), max_retries=3)

    with pytest.raises(StaanTransportError) as excinfo:
        await search(rig)

    assert str(excinfo.value) == "Could not complete the request to Staan (DecodingError)."
    assert excinfo.value.endpoint == "search"
    assert excinfo.value.__cause__ is cause
    assert len(rig.upstream.requests) == 1
    assert not rig.sleep.delays


async def test_mixed_failures_share_one_retry_budget() -> None:
    rig = make_rig(httpx2.ConnectError("x"), failure(429, retry_after="1"), ok(), max_retries=2)

    assert await search(rig) == OK_SEARCH

    assert rig.sleep.delays == [0.5, 1.0 + 1]


async def test_last_failure_decides_the_raised_error() -> None:
    rig = make_rig(failure(429), httpx2.ConnectError("x"), max_retries=1)

    with pytest.raises(StaanTransportError):
        await search(rig)


async def test_timeout_setting_is_applied_to_every_phase() -> None:
    rig = make_rig(timeout=7.5)

    await search(rig)

    assert rig.upstream.only.extensions["timeout"] == {
        "connect": 7.5,
        "read": 7.5,
        "write": 7.5,
        "pool": 7.5,
    }


# --------------------------------------------------------------------------------------
# Error mapping through the client
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorCase:
    """An upstream error response and the exception the client must raise for it."""

    status: int
    body: bytes
    cls: type[StaanAPIError]
    code: str | None
    fragment: str


ERROR_CASES = [
    ErrorCase(
        400, BODY_400, StaanValidationError, None, "count must be equal to 10; offset must be"
    ),
    ErrorCase(422, BODY_400, StaanValidationError, None, "count must be equal to 10"),
    ErrorCase(401, BODY_401, StaanAuthenticationError, None, "STAAN_API_KEY"),
    ErrorCase(
        401, BODY_401_GATEWAY, StaanAuthenticationError, "INVALID_CREDENTIALS", "STAAN_API_KEY"
    ),
    ErrorCase(403, BODY_403_DISABLED, StaanKeyDisabledError, "KEY_DISABLED", "disabled"),
    ErrorCase(404, b"", StaanServerError, None, "HTTP 404"),
    ErrorCase(
        404,
        b'{"message":"Cannot GET /v2/x","error":"Not Found","statusCode":404}',
        StaanServerError,
        None,
        "HTTP 404",
    ),
    ErrorCase(429, b"", StaanRateLimitError, None, "rate limit"),
    ErrorCase(500, b"<html>boom</html>", StaanServerError, None, "HTTP 500"),
    ErrorCase(502, b'{"message":"upstream secret detail"}', StaanServerError, None, "HTTP 502"),
]


@pytest.mark.parametrize("case", ERROR_CASES, ids=lambda c: f"{c.status}-{c.cls.__name__}")
@pytest.mark.parametrize("endpoint", ["search", "answer"])
async def test_every_documented_error_body_maps_to_its_exception(
    endpoint: str, case: ErrorCase
) -> None:
    rig = make_rig(failure(case.status, case.body), max_retries=0)

    with pytest.raises(StaanAPIError) as excinfo:
        await (search(rig) if endpoint == "search" else ask(rig))

    error = excinfo.value
    assert type(error) is case.cls
    assert error.status_code == case.status
    assert error.code == case.code
    assert error.endpoint == endpoint
    assert case.fragment in str(error)
    assert API_KEY not in str(error)
    assert "upstream secret detail" not in str(error)


async def test_search_403_is_a_generic_forbidden_error() -> None:
    rig = make_rig(failure(403, BODY_403_FORBIDDEN))

    with pytest.raises(StaanForbiddenError) as excinfo:
        await search(rig)

    assert "HTTP 403" in str(excinfo.value)
    assert "invitation-only" not in str(excinfo.value)
    assert len(rig.upstream.requests) == 1


async def test_answer_403_explains_that_answer_needs_an_invitation() -> None:
    rig = make_rig(failure(403, BODY_403_FORBIDDEN))

    with pytest.raises(StaanForbiddenError) as excinfo:
        await ask(rig)

    assert "invitation-only" in str(excinfo.value)
    assert "support@staan.ai" in str(excinfo.value)


async def test_error_body_with_a_key_inside_never_leaks_it_via_the_client() -> None:
    body = json.dumps({"message": "ok", "authorization": f"Bearer {API_KEY}"}).encode()
    rig = make_rig(failure(400, body))

    with pytest.raises(StaanValidationError) as excinfo:
        await search(rig)

    assert API_KEY not in str(excinfo.value)
    assert API_KEY not in repr(excinfo.value)


# --------------------------------------------------------------------------------------
# Malformed success bodies
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201])
@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (b"[1, 2]", "Staan returned a response that is not a JSON object."),
        (b'"text"', "Staan returned a response that is not a JSON object."),
        (b"null", "Staan returned a response that is not a JSON object."),
        (b"42", "Staan returned a response that is not a JSON object."),
        (b"true", "Staan returned a response that is not a JSON object."),
        (b"", "Staan returned a response that is not valid JSON."),
        (b"not json", "Staan returned a response that is not valid JSON."),
        (b"<html></html>", "Staan returned a response that is not valid JSON."),
        (b'{"a": ', "Staan returned a response that is not valid JSON."),
        (b"\xff\xfe\x00", "Staan returned a response that is not valid JSON."),
        pytest.param(
            b"[" * 100_000, "Staan returned a response that is not valid JSON.", id="deeply-nested"
        ),
    ],
)
@pytest.mark.parametrize("endpoint", ["search", "answer"])
async def test_success_status_with_a_bad_body_raises_response_error(
    endpoint: str, body: bytes, fragment: str, status: int
) -> None:
    rig = make_rig(httpx2.Response(status, content=body), max_retries=3)

    with pytest.raises(StaanResponseError) as excinfo:
        await (search(rig) if endpoint == "search" else ask(rig))

    error = excinfo.value
    assert type(error) is StaanResponseError
    assert str(error) == fragment
    assert error.status_code == status
    assert error.endpoint == endpoint
    assert len(rig.upstream.requests) == 1
    assert not rig.sleep.delays


async def test_empty_json_object_is_a_valid_body() -> None:
    rig = make_rig(ok({}))

    assert await search(rig) == {}


# --------------------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------------------


async def test_context_manager_returns_self_and_closes_the_pool() -> None:
    rig = make_rig()

    async with rig.client as entered:
        assert entered is rig.client
        assert not rig.client._http.is_closed

    assert rig.client._http.is_closed


async def test_context_manager_closes_the_pool_when_the_body_raises() -> None:
    rig = make_rig()

    async def fail_inside_block() -> None:
        async with rig.client:
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        await fail_inside_block()

    assert rig.client._http.is_closed


async def test_aclose_can_be_called_repeatedly() -> None:
    rig = make_rig()

    await rig.client.aclose()
    await rig.client.aclose()

    assert rig.client._http.is_closed


async def test_client_is_reusable_for_many_calls() -> None:
    rig = make_rig(ok(), ok(OK_ANSWER))

    async with rig.client:
        await rig.client.search_web(query="a", market="en-us")
        await rig.client.answer(query="b")

    assert [r.url.path for r in rig.upstream.requests] == ["/v2/search/web", "/v2/answer"]


async def test_default_sleep_is_anyio_sleep() -> None:
    client = StaanClient(Settings(api_key=API_KEY))

    try:
        assert client._sleep is anyio.sleep
    finally:
        await client.aclose()


async def test_constructor_performs_no_network_io() -> None:
    upstream = Upstream([ok()])

    client = StaanClient(Settings(api_key=None), transport=httpx2.MockTransport(upstream))
    await client.aclose()

    assert not upstream.requests


async def test_signature_forbids_positional_arguments() -> None:
    async with StaanClient(Settings(api_key=API_KEY)) as client:
        with pytest.raises(TypeError):
            await client.search_web("q", "en-gb")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            await client.answer("q")  # type: ignore[call-arg]


# --------------------------------------------------------------------------------------
# Overall deadline
# --------------------------------------------------------------------------------------


def slow_client(handler: Callable[[httpx2.Request], Any], **settings: Any) -> StaanClient:
    """A client over an async handler, with real (anyio) retry sleeps."""
    config = Settings(**{"api_key": API_KEY, **settings})
    return StaanClient(config, transport=httpx2.MockTransport(handler))


async def test_a_slow_response_ends_at_the_call_deadline_with_one_request() -> None:
    requests: list[httpx2.Request] = []

    async def hang(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        await anyio.sleep(30)
        return ok()

    async with slow_client(hang, deadline=0.05, max_retries=2) as client:
        with pytest.raises(StaanTransportError) as excinfo:
            await client.search_web(query="q", market="en-gb")

    assert str(excinfo.value) == "The call to Staan did not finish within 0.05 s."
    assert excinfo.value.endpoint == "search"
    assert isinstance(excinfo.value.__cause__, TimeoutError)
    assert len(requests) == 1


async def test_the_deadline_also_covers_the_retry_backoff() -> None:
    requests: list[httpx2.Request] = []

    async def unavailable(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return failure(503)

    # The first backoff is 0.5 s, far beyond the deadline: no second request is ever sent.
    async with slow_client(unavailable, deadline=0.05, max_retries=2) as client:
        with pytest.raises(StaanTransportError) as excinfo:
            await client.answer(query="why?")

    assert str(excinfo.value) == "The call to Staan did not finish within 0.05 s."
    assert excinfo.value.endpoint == "answer"
    assert len(requests) == 1


async def test_a_call_that_finishes_before_the_deadline_is_unaffected() -> None:
    rig = make_rig(failure(503), ok(), max_retries=1, deadline=5.0)

    assert await search(rig) == OK_SEARCH


# --------------------------------------------------------------------------------------
# Configuration mistakes that only surface when the request is built
# --------------------------------------------------------------------------------------


async def test_an_invalid_base_url_is_a_configuration_error() -> None:
    rig = make_rig(ok(), base_url="https://api.staan.ai:abc/v2")

    with pytest.raises(ConfigurationError) as excinfo:
        await search(rig)

    assert str(excinfo.value) == (
        "The request to Staan could not be built: check STAAN_BASE_URL and STAAN_API_KEY "
        "(InvalidURL)."
    )
    assert isinstance(excinfo.value.__cause__, httpx2.InvalidURL)
    assert not rig.upstream.requests


async def test_an_api_key_that_cannot_be_sent_is_a_configuration_error() -> None:
    bad_key = "sk-“pasted”"
    rig = make_rig(ok(), api_key=bad_key)

    with pytest.raises(ConfigurationError) as excinfo:
        await ask(rig)

    assert str(excinfo.value) == (
        "The request to Staan could not be built: check STAAN_BASE_URL and STAAN_API_KEY "
        "(UnicodeEncodeError)."
    )
    assert "pasted" not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, UnicodeEncodeError)
    assert not rig.upstream.requests


# --------------------------------------------------------------------------------------
# Response size guard
# --------------------------------------------------------------------------------------


def padded_json(total: int) -> bytes:
    """A JSON object of exactly ``total`` bytes."""
    filler = total - len(b'{"p":""}')
    return b'{"p":"' + b"x" * filler + b'"}'


async def test_a_response_larger_than_the_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("staan_mcp.client.MAX_RESPONSE_BYTES", 1 << 20)
    rig = make_rig(httpx2.Response(200, content=padded_json((1 << 20) + 1)), max_retries=2)

    with pytest.raises(StaanResponseError) as excinfo:
        await search(rig)

    assert str(excinfo.value) == "Staan returned a response larger than 1 MiB, which was discarded."
    assert excinfo.value.status_code == 200
    assert excinfo.value.endpoint == ""
    assert len(rig.upstream.requests) == 1  # never retried


async def test_a_response_exactly_at_the_limit_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("staan_mcp.client.MAX_RESPONSE_BYTES", 1 << 20)
    rig = make_rig(httpx2.Response(200, content=padded_json(1 << 20)))

    result = await search(rig)

    assert len(result["p"]) == (1 << 20) - len('{"p":""}')


async def test_an_oversized_error_body_is_rejected_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("staan_mcp.client.MAX_RESPONSE_BYTES", 1 << 20)
    rig = make_rig(httpx2.Response(500, content=b"x" * ((1 << 20) + 1)), max_retries=0)

    with pytest.raises(StaanResponseError):
        await search(rig)


def test_the_default_response_limit_is_32_mib() -> None:
    assert MAX_RESPONSE_BYTES == 32 * 1024 * 1024


async def test_compressed_responses_are_still_decoded_and_keep_their_headers() -> None:
    body = json.dumps(OK_SEARCH).encode()
    compressed = gzip.compress(body)
    rig = make_rig(
        httpx2.Response(
            200, content=compressed, headers={"content-encoding": "gzip", "x-request-id": "r1"}
        )
    )

    assert await search(rig) == OK_SEARCH


async def test_retry_after_survives_the_streamed_read() -> None:
    rig = make_rig(failure(429, b"", retry_after="3"), ok(), max_retries=1)

    assert await search(rig) == OK_SEARCH

    assert rig.sleep.delays == [3.5]
