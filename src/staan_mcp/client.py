"""Asynchronous client for the Staan API (``/search/web`` and ``/answer``).

:class:`StaanClient` owns one :mod:`httpx2` connection pool. It builds the exact request bodies
the server contract fixes (``count`` is always 10, ``extra_snippets`` is always on, ``stream`` is
always off), retries transient failures with exponential backoff and maps every failure to an
exception from :mod:`staan_mcp.errors`. Neither the API key nor the ``Authorization`` header is
ever logged or placed in an exception message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, Self

import anyio
import httpx2

from staan_mcp import __version__
from staan_mcp.errors import (
    ConfigurationError,
    StaanAPIError,
    StaanResponseError,
    StaanTransportError,
    error_from_response,
)
from staan_mcp.ratelimit import RateLimiter
from staan_mcp.upstream import (
    MAX_RESPONSE_BYTES,
    MISSING_API_KEY_MESSAGE,
    PATHS,
    RETRY_STATUSES,
    SUCCESS_STATUSES,
    answer_body,
    backoff_delay,
    deadline_message,
    json_object,
    parse_retry_after,
    request_headers,
    request_not_built_message,
    response_too_large_message,
    search_body,
    transport_failure_message,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from types import TracebackType

    from staan_mcp.config import Settings
    from staan_mcp.upstream import Endpoint, FullContentFormat

__all__ = ["StaanClient"]

_logger = logging.getLogger(__name__)

_NOT_RETRIED_TIMEOUTS: Final = (httpx2.ReadTimeout, httpx2.WriteTimeout)
_STALE_BODY_HEADERS: Final = frozenset({"content-encoding", "content-length", "transfer-encoding"})
_USER_AGENT: Final = f"staan-mcp/{__version__} (+https://github.com/MarcinWolnyEU/Staan-MCP)"


@dataclass(frozen=True, slots=True)
class _Retryable:
    """A failed attempt that may be retried.

    Attributes:
        error: What to raise if the retry budget is exhausted.
        retry_after: Seconds requested by the server's ``Retry-After`` header, already capped.
    """

    error: StaanAPIError
    retry_after: float = 0.0


def _transport_error(exc: httpx2.RequestError, endpoint: Endpoint, timeout: float) -> StaanAPIError:
    """Describe a failure to complete the HTTP exchange, without echoing exception text."""
    msg = transport_failure_message(
        type(exc).__name__, timed_out=isinstance(exc, httpx2.TimeoutException), timeout=timeout
    )
    error = StaanTransportError(msg, endpoint=endpoint)
    error.__cause__ = exc
    return error


class StaanClient:
    """Client for the Staan web search and Answer endpoints.

    Use it as an async context manager, or call :meth:`aclose` when done.

    Args:
        settings: API key, base URL, timeout, retry budget and request rate.
        transport: Optional :mod:`httpx2` transport, mainly for tests (``MockTransport``).
        sleep: Coroutine function used to wait between retries; injectable for tests.
        limiter: The request rate limiter. By default the client makes its own from
            ``settings.rate_limit``, so every client (and so every server) has its own budget,
            shared by all of its calls, markets and retries.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        limiter: RateLimiter | None = None,
    ) -> None:
        """Create the client and its connection pool; no network I/O happens here."""
        self._settings = settings
        self._base_url = settings.base_url.rstrip("/")
        self._sleep = sleep
        self._limiter = RateLimiter(settings.rate_limit) if limiter is None else limiter
        self._http = httpx2.AsyncClient(
            transport=transport,
            timeout=httpx2.Timeout(settings.timeout),
            follow_redirects=False,
        )

    async def __aenter__(self) -> Self:
        """Return the client itself."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection pool."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool. Safe to call more than once."""
        await self._http.aclose()

    async def search_web(  # noqa: PLR0913  (signature fixed by the design contract)
        self,
        *,
        query: str,
        market: str,
        offset: int = 0,
        full_content: FullContentFormat | None = None,
        include_domains: Sequence[str] | None = None,
        exclude_domains: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Run one web search (one billable request) in a single market.

        Args:
            query: The search query.
            market: One concrete market such as ``"en-gb"``; wildcards are expanded elsewhere.
            offset: Result offset, one of 0, 10, 20 or 30.
            full_content: Ask Staan to return each page's full text in this format, or ``None``
                to leave it out.
            include_domains: Only return results from these bare domains.
            exclude_domains: Never return results from these bare domains.

        Returns:
            The decoded JSON response.

        Raises:
            ConfigurationError: If no API key is configured (raised before any request).
            StaanAPIError: If the request fails. See :mod:`staan_mcp.errors` for the subclasses.
        """
        body = search_body(
            query=query,
            market=market,
            offset=offset,
            full_content=full_content,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
        return await self._post("search", body)

    async def answer(  # noqa: PLR0913  (signature fixed by the design contract)
        self,
        *,
        query: str,
        language: Literal["en", "fr"] = "en",
        mode: Literal["short", "long"] = "short",
        filter_domain: str | None = None,
        sources: Sequence[Mapping[str, str]] | None = None,
        sources_mode: Literal["search", "context"] = "search",
    ) -> dict[str, Any]:
        """Ask the Answer endpoint for a grounded, Markdown-formatted answer.

        Streaming is always off and no conversation history is sent.

        Args:
            query: The question.
            language: Language of the answer.
            mode: ``"short"`` or ``"long"`` answer.
            filter_domain: Bare domain to restrict the search to; ignored upstream when
                ``sources`` is given.
            sources: Caller-provided sources (``url``, ``title``, ``content``) that replace the
                search.
            sources_mode: ``"search"`` fetches and reranks the sources, ``"context"`` injects
                their ``content`` directly. Only sent together with ``sources``.

        Returns:
            The decoded JSON response.

        Raises:
            ConfigurationError: If no API key is configured (raised before any request).
            StaanAPIError: If the request fails. See :mod:`staan_mcp.errors` for the subclasses.
        """
        body = answer_body(
            query=query,
            language=language,
            mode=mode,
            filter_domain=filter_domain,
            sources=sources,
            sources_mode=sources_mode,
        )
        return await self._post("answer", body)

    def _headers(self) -> dict[str, str]:
        """Build the request headers.

        Raises:
            ConfigurationError: If no API key is configured.
        """
        api_key = self._settings.api_key
        if not api_key:
            raise ConfigurationError(MISSING_API_KEY_MESSAGE)
        return request_headers(api_key, _USER_AGENT)

    async def _post(self, endpoint: Endpoint, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``body`` to ``endpoint``, retrying transient failures within the call deadline.

        Raises:
            ConfigurationError: If no API key is configured; nothing is sent in that case.
            StaanAPIError: If the request fails for good or the deadline passes.
        """
        headers = self._headers()
        url = f"{self._base_url}{PATHS[endpoint]}"
        try:
            with anyio.fail_after(self._settings.deadline) as scope:
                return await self._send_with_retries(endpoint, url, headers, body, scope.deadline)
        except TimeoutError as exc:
            msg = deadline_message(self._settings.deadline)
            raise StaanTransportError(msg, endpoint=endpoint) from exc

    async def _send_with_retries(
        self,
        endpoint: Endpoint,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        """Send the request, retrying transient failures with exponential backoff.

        ``deadline`` is when the call must end, on the :func:`anyio.current_time` clock.
        """
        attempt = 0
        while True:
            await self._wait_for_slot(endpoint, deadline)
            outcome = await self._attempt(endpoint, url, headers, body)
            if not isinstance(outcome, _Retryable):
                return outcome
            if attempt >= self._settings.max_retries:
                raise outcome.error
            delay = backoff_delay(attempt, outcome.retry_after)
            _logger.debug(
                "Staan %s request failed (%s); retry %d of %d in %.1f s",
                endpoint,
                outcome.error,
                attempt + 1,
                self._settings.max_retries,
                delay,
            )
            await self._sleep(delay)
            attempt += 1

    async def _wait_for_slot(self, endpoint: Endpoint, deadline: float) -> None:
        """Wait until the rate limit lets one more request start.

        Called right before every send, so every retry takes its own slot. The wait is part of
        the call: it runs inside the deadline, and one that could not end before the deadline
        fails at once instead of sleeping, without having booked anything. A wait that is
        cancelled gives its slot back.

        Raises:
            StaanTransportError: If the slot would come after the call deadline.
        """
        limiter = self._limiter
        slot = limiter.reserve(deadline - anyio.current_time())
        if slot is None:
            msg = deadline_message(self._settings.deadline)
            raise StaanTransportError(msg, endpoint=endpoint)
        if slot.delay <= 0:
            return
        _logger.debug(
            "Staan %s request delayed %.2f s to keep to %g requests per second",
            endpoint,
            slot.delay,
            self._settings.rate_limit,
        )
        try:
            while (left := limiter.remaining(slot)) > 0:  # noqa: ASYNC110  (the clock decides)
                await anyio.sleep(left)
        except BaseException:
            limiter.release(slot)
            raise

    async def _attempt(
        self, endpoint: Endpoint, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any] | _Retryable:
        """Send the request once.

        Returns:
            The decoded body on success, or a :class:`_Retryable` for a transient failure.

        Raises:
            StaanAPIError: For a failure that must not be retried.
        """
        try:
            response = await self._exchange(url, headers, body)
        except _NOT_RETRIED_TIMEOUTS as exc:
            # The request was (or may have been) sent, and every request is billable: a second
            # attempt would pay twice for the same slow answer.
            raise _transport_error(exc, endpoint, self._settings.timeout) from exc
        except httpx2.TransportError as exc:
            return _Retryable(_transport_error(exc, endpoint, self._settings.timeout))
        except httpx2.RequestError as exc:
            raise _transport_error(exc, endpoint, self._settings.timeout) from exc
        except (httpx2.InvalidURL, UnicodeError) as exc:
            # Configuration mistakes that httpx2 only notices when it builds the request.
            msg = request_not_built_message(type(exc).__name__)
            raise ConfigurationError(msg) from exc
        status = response.status_code
        if status in SUCCESS_STATUSES:
            return json_object(response.content, status, endpoint)
        error = error_from_response(status, response.content, endpoint=endpoint)
        if status in RETRY_STATUSES:
            return _Retryable(error, parse_retry_after(response.headers.get("retry-after", "")))
        raise error

    async def _exchange(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> httpx2.Response:
        """Send one request and read the body, giving up on one larger than the size limit.

        Raises:
            StaanResponseError: If the body exceeds :data:`MAX_RESPONSE_BYTES`.
        """
        async with self._http.stream("POST", url, headers=headers, json=body) as response:
            received = bytearray()
            async for chunk in response.aiter_bytes():
                received.extend(chunk)
                if len(received) > MAX_RESPONSE_BYTES:
                    msg = response_too_large_message(MAX_RESPONSE_BYTES)
                    raise StaanResponseError(msg, status_code=response.status_code)
        # The body is already decoded, so the encoding and length headers no longer describe it.
        kept = [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in _STALE_BODY_HEADERS
        ]
        return httpx2.Response(response.status_code, headers=kept, content=bytes(received))
