"""The urllib transport and the thread runner of ``stdio_server.py``, against a loopback fake."""
# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
from typing import TYPE_CHECKING, Any

import pytest
from local_staan import LocalStaan, Reply, json_reply
from stdio_helpers import (
    API_KEY,
    OK_SEARCH,
    SEARCH_BODY,
    settings_for,
)

from staan_mcp import stdio_server
from staan_mcp.errors import (
    ConfigurationError,
    StaanAuthenticationError,
    StaanRateLimitError,
    StaanResponseError,
    StaanServerError,
    StaanTransportError,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def post(upstream: LocalStaan, endpoint: Any = "search", **overrides: Any) -> dict[str, Any]:
    """Send ``SEARCH_BODY`` through the transport and return the decoded response."""
    stop = overrides.pop("stop", None) or threading.Event()
    return stdio_server._post(settings_for(upstream, **overrides), endpoint, SEARCH_BODY, stop)


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, float]]:
    """Skip the retry waits; returns the ``(attempt, retry_after)`` pairs that were asked for."""
    asked: list[tuple[int, float]] = []

    def delay(attempt: int, retry_after: float) -> float:
        asked.append((attempt, retry_after))
        return 0.0

    monkeypatch.setattr(stdio_server, "backoff_delay", delay)
    return asked


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------


def test_a_request_carries_the_documented_headers_and_body(local_staan: LocalStaan) -> None:
    local_staan.search = json_reply(OK_SEARCH)

    assert post(local_staan) == OK_SEARCH

    (request,) = local_staan.requests
    assert request.path == "/search/web"
    assert request.body == SEARCH_BODY
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Accept-Encoding"] == "gzip"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["User-Agent"] == (
        f"staan-mcp-stdio/{stdio_server.VERSION} (+https://github.com/MarcinWolnyEU/Staan-MCP)"
    )


def test_the_answer_endpoint_has_its_own_path(local_staan: LocalStaan) -> None:
    post(local_staan, "answer")

    assert [request.path for request in local_staan.requests] == ["/answer"]


def test_a_missing_key_sends_nothing(local_staan: LocalStaan) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        post(local_staan, api_key=None)

    assert str(excinfo.value).startswith("STAAN_API_KEY is not set.")
    assert local_staan.requests == []


@pytest.mark.parametrize("encoding", ["gzip", "GZIP", "x-gzip"])
def test_a_gzip_body_is_decoded(local_staan: LocalStaan, encoding: str) -> None:
    body = gzip.compress(json.dumps(OK_SEARCH).encode())
    local_staan.search = Reply(201, body, {"Content-Encoding": encoding})

    assert post(local_staan) == OK_SEARCH


def test_a_body_larger_than_one_read_chunk_is_assembled(local_staan: LocalStaan) -> None:
    payload = {"p": "x" * 300_000}
    local_staan.search = json_reply(payload)
    assert post(local_staan) == payload

    local_staan.search = Reply(
        201, gzip.compress(json.dumps(payload).encode()), {"Content-Encoding": "gzip"}
    )
    assert post(local_staan) == payload


def test_an_unsupported_encoding_is_refused(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(201, b"{}", {"Content-Encoding": "br"})

    with pytest.raises(StaanResponseError) as excinfo:
        post(local_staan)

    assert str(excinfo.value) == "Staan returned a response in an unsupported encoding."


def test_a_gzip_body_that_cannot_be_decoded_is_a_transport_error(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(201, b"this is not gzip", {"Content-Encoding": "gzip"})

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, max_retries=2)

    assert str(excinfo.value) == "Could not complete the request to Staan (DecodingError)."
    assert len(local_staan.requests) == 1  # never retried


def padded_json(total: int) -> bytes:
    """A JSON object of exactly ``total`` bytes."""
    return b'{"p":"' + b"x" * (total - len(b'{"p":""}')) + b'"}'


TOO_LARGE = "Staan returned a response larger than 1 MiB, which was discarded."


def test_a_response_larger_than_the_limit_is_refused(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stdio_server, "MAX_RESPONSE_BYTES", 1 << 20)
    local_staan.search = Reply(201, padded_json((1 << 20) + 1))

    with pytest.raises(StaanResponseError) as excinfo:
        post(local_staan, max_retries=2)

    assert str(excinfo.value) == TOO_LARGE
    assert len(local_staan.requests) == 1  # never retried


def test_a_response_exactly_at_the_limit_is_accepted(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stdio_server, "MAX_RESPONSE_BYTES", 1 << 20)
    local_staan.search = Reply(201, padded_json(1 << 20))

    assert len(post(local_staan)["p"]) == (1 << 20) - len('{"p":""}')


def test_a_gzip_bomb_is_refused_without_inflating_it(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stdio_server, "MAX_RESPONSE_BYTES", 1 << 20)
    bomb = gzip.compress(padded_json(8 << 20))
    assert len(bomb) < 1 << 16
    local_staan.search = Reply(201, bomb, {"Content-Encoding": "gzip"})

    with pytest.raises(StaanResponseError) as excinfo:
        post(local_staan)

    assert str(excinfo.value) == TOO_LARGE


def test_an_oversized_error_body_is_refused_too(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stdio_server, "MAX_RESPONSE_BYTES", 1 << 20)
    local_staan.search = Reply(500, b"x" * ((1 << 20) + 1))

    with pytest.raises(StaanResponseError):
        post(local_staan)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not json", "Staan returned a response that is not valid JSON."),
        (b"[1]", "Staan returned a response that is not a JSON object."),
    ],
)
def test_a_success_without_a_json_object_is_a_response_error(
    local_staan: LocalStaan, body: bytes, message: str
) -> None:
    local_staan.search = Reply(200, body)

    with pytest.raises(StaanResponseError) as excinfo:
        post(local_staan, max_retries=2)

    assert str(excinfo.value) == message
    assert excinfo.value.status_code == 200
    assert len(local_staan.requests) == 1


def test_a_redirect_is_not_followed(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(302, b"", {"Location": "https://elsewhere.example/search/web"})

    with pytest.raises(StaanServerError) as excinfo:
        post(local_staan)

    assert str(excinfo.value) == "Staan returned an unexpected response (HTTP 302)."
    assert len(local_staan.requests) == 1


@pytest.mark.parametrize(
    ("reason", "name"),
    [(ConnectionRefusedError(), "ConnectionRefusedError"), ("unknown url type", "URLError")],
)
def test_a_connection_failure_is_a_transport_error_and_is_retried(
    local_staan: LocalStaan,
    monkeypatch: pytest.MonkeyPatch,
    no_backoff: list[tuple[int, float]],
    reason: object,
    name: str,
) -> None:
    def refuse(*_args: object) -> None:
        raise urllib.error.URLError(reason)  # type: ignore[arg-type]

    monkeypatch.setattr(stdio_server, "_exchange", refuse)

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, max_retries=2)

    assert str(excinfo.value) == f"Could not complete the request to Staan ({name})."
    assert no_backoff == [(0, 0.0), (1, 0.0)]


def test_a_slow_answer_times_out_and_is_never_retried(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(201, b"{}", delay=1.0)

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, timeout=0.2, max_retries=2)

    assert str(excinfo.value) == "The request to Staan timed out after 0.2 s."
    assert len(local_staan.requests) == 1


def test_a_dropped_connection_is_retried(
    local_staan: LocalStaan, no_backoff: list[tuple[int, float]]
) -> None:
    local_staan.search = [Reply(drop=True), json_reply(OK_SEARCH)]

    assert post(local_staan, max_retries=1) == OK_SEARCH
    assert len(local_staan.requests) == 2
    assert no_backoff == [(0, 0.0)]


def test_a_dropped_connection_is_reported_when_the_retries_run_out(
    local_staan: LocalStaan,
) -> None:
    local_staan.search = Reply(drop=True)

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan)

    assert str(excinfo.value).startswith("Could not complete the request to Staan (")


def test_a_truncated_body_is_a_transport_error(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(201, b'{"a":', declared_length=100)

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan)

    assert str(excinfo.value) == "Could not complete the request to Staan (IncompleteRead)."


@pytest.mark.parametrize("path", ["/\u00e9", "/a b"])
def test_a_url_that_cannot_be_built_points_at_the_configuration(
    local_staan: LocalStaan, path: str
) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        post(local_staan, base_url=local_staan.base_url + path, max_retries=2)

    assert str(excinfo.value).startswith(
        "The request to Staan could not be built: check STAAN_BASE_URL and STAAN_API_KEY ("
    )
    assert local_staan.requests == []


def test_transient_statuses_are_retried_with_the_retry_after_hint(
    local_staan: LocalStaan, no_backoff: list[tuple[int, float]]
) -> None:
    local_staan.search = [
        Reply(503, headers={"Retry-After": "2"}),
        Reply(429, headers={"Retry-After": "soon"}),
        json_reply(OK_SEARCH),
    ]

    assert post(local_staan, max_retries=2) == OK_SEARCH
    assert no_backoff == [(0, 2.0), (1, 0.0)]


def test_the_last_transient_error_is_raised_when_the_retries_run_out(
    local_staan: LocalStaan, no_backoff: list[tuple[int, float]]
) -> None:
    local_staan.search = Reply(429, b'{"message":"slow down"}')

    with pytest.raises(StaanRateLimitError) as excinfo:
        post(local_staan, max_retries=1)

    assert str(excinfo.value) == (
        "Staan rate limit reached (HTTP 429). Try again shortly. Staan said: slow down"
    )
    assert len(local_staan.requests) == 2
    assert no_backoff == [(0, 0.0)]


def test_a_permanent_error_is_raised_at_once(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(401, b'{"message":"Unauthorized"}')

    with pytest.raises(StaanAuthenticationError):
        post(local_staan, max_retries=2)

    assert len(local_staan.requests) == 1


def test_the_call_deadline_ends_the_retries(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(503, headers={"Retry-After": "30"})

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, deadline=0.3, max_retries=5)

    assert str(excinfo.value) == "The call to Staan did not finish within 0.3 s."


def test_an_attempt_cut_short_by_the_deadline_reports_the_deadline_like_the_package(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The retry wait leaves 0.5 s of the deadline, less than the 0.6 s timeout: the second
    # attempt hangs until the deadline ends it. The package says so in the deadline's words.
    monkeypatch.setattr(stdio_server, "backoff_delay", lambda _attempt, _retry_after: 0.3)
    local_staan.search = [Reply(503), Reply(201, b"{}", delay=2.0)]

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, timeout=0.6, deadline=0.8, max_retries=2)

    assert str(excinfo.value) == "The call to Staan did not finish within 0.8 s."
    assert len(local_staan.requests) == 2


def test_a_retried_timeout_cut_short_by_the_deadline_reports_the_deadline_too(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    def connect_times_out(
        _url: str, _headers: dict[str, str], _body: bytes, timeout: float
    ) -> None:
        time.sleep(timeout + 0.05)
        raise urllib.error.URLError(TimeoutError())

    monkeypatch.setattr(stdio_server, "_exchange", connect_times_out)

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, timeout=2.0, deadline=0.2, max_retries=0)

    assert str(excinfo.value) == "The call to Staan did not finish within 0.2 s."


def test_a_call_that_is_already_stopped_sends_nothing(local_staan: LocalStaan) -> None:
    stop = threading.Event()
    stop.set()

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, max_retries=2, stop=stop)

    assert str(excinfo.value) == "The call was cancelled."
    assert local_staan.requests == []


def test_a_cancelled_call_stops_waiting_to_retry(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()

    def cancel_during_the_wait(_attempt: int, _retry_after: float) -> float:
        stop.set()
        return 30.0

    monkeypatch.setattr(stdio_server, "backoff_delay", cancel_during_the_wait)
    local_staan.search = Reply(503)

    with pytest.raises(StaanTransportError) as excinfo:
        post(local_staan, max_retries=2, stop=stop)

    assert str(excinfo.value) == "The call was cancelled."
    assert len(local_staan.requests) == 1


# --------------------------------------------------------------------------------------
# Running jobs in threads
# --------------------------------------------------------------------------------------


def test_jobs_run_concurrently_and_come_back_in_order() -> None:
    def job(value: int, delay: float) -> Callable[[], dict[str, Any]]:
        def run() -> dict[str, Any]:
            time.sleep(delay)
            return {"value": value}

        return run

    outcomes = stdio_server._run_parallel(
        [job(0, 0.2), job(1, 0.0)], deadline=10.0, stop=threading.Event()
    )

    assert outcomes == [{"value": 0}, {"value": 1}]


def test_an_unexpected_exception_is_handed_back_not_lost() -> None:
    def broken() -> dict[str, Any]:
        msg = "boom"
        raise RuntimeError(msg)

    (outcome,) = stdio_server._run_parallel([broken], deadline=10.0, stop=threading.Event())

    assert isinstance(outcome, RuntimeError)


def test_a_fatal_error_stops_the_other_jobs() -> None:
    stop = threading.Event()

    def fatal() -> dict[str, Any]:
        msg = "key rejected"
        raise StaanAuthenticationError(msg)

    def waiting() -> dict[str, Any]:
        stop.wait(10)
        return {}

    outcomes = stdio_server._run_parallel([waiting, fatal], deadline=10.0, stop=stop)

    assert stop.is_set()
    assert isinstance(outcomes[1], StaanAuthenticationError)
    assert str(outcomes[0]) == "The call to Staan did not finish within 10 s."


def test_a_job_that_outlives_the_hard_deadline_is_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio_server, "_HARD_DEADLINE_GRACE", 0.05)
    release = threading.Event()

    def hangs() -> dict[str, Any]:
        release.wait(10)
        return {}

    try:
        (outcome,) = stdio_server._run_parallel([hangs], deadline=0.05, stop=threading.Event())
    finally:
        release.set()

    assert isinstance(outcome, StaanTransportError)
    assert str(outcome) == "The call to Staan did not finish within 0.05 s."


def test_a_job_that_starts_after_a_fatal_error_sends_nothing(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(401, b"{}")
    stop = threading.Event()
    finished = threading.Event()
    cancelled: list[str] = []

    def late() -> dict[str, Any]:
        stop.wait(10)  # gets going only once the fatal error has stopped the call
        try:
            return post(local_staan, stop=stop)
        except StaanTransportError as error:
            cancelled.append(str(error))
            raise
        finally:
            finished.set()

    outcomes = stdio_server._run_parallel(
        [late, lambda: post(local_staan, stop=stop)], deadline=10.0, stop=stop
    )

    assert isinstance(outcomes[1], StaanAuthenticationError)
    assert finished.wait(10)
    assert cancelled == ["The call was cancelled."]
    assert len(local_staan.requests) == 1  # the 401, and nothing for the job that came late
