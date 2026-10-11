"""The parts of the Staan HTTP exchange that do not depend on the HTTP library.

Request bodies, paths, headers, retry and backoff constants and the wording of transport
failures. :class:`~staan_mcp.client.StaanClient` (``httpx2``, asynchronous) and the standalone
``stdio_server.py`` (``urllib``, synchronous) both build on this module, so the two send the same
requests and report failures in the same words. Standard library only.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any, Final, Literal

from staan_mcp.errors import StaanResponseError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "BACKOFF_BASE",
    "MAX_RESPONSE_BYTES",
    "MISSING_API_KEY_MESSAGE",
    "PATHS",
    "RETRY_AFTER_CAP",
    "RETRY_STATUSES",
    "SEARCH_COUNT",
    "SUCCESS_STATUSES",
    "Endpoint",
    "FullContentFormat",
    "answer_body",
    "backoff_delay",
    "deadline_message",
    "json_object",
    "parse_retry_after",
    "request_headers",
    "request_not_built_message",
    "response_too_large_message",
    "search_body",
    "transport_failure_message",
]

Endpoint = Literal["search", "answer"]
"""The two logical endpoints of the Staan API."""

FullContentFormat = Literal["markdown", "html"]
"""Formats in which Staan can return the full text of a result page."""

SEARCH_COUNT: Final = 10
"""The only ``count`` Staan accepts; it is always sent and never exposed."""

MAX_RESPONSE_BYTES: Final = 32 * 1024 * 1024
"""Largest response body (32 MiB) a client reads; a bigger one is a response error."""

PATHS: Final[dict[Endpoint, str]] = {"search": "/search/web", "answer": "/answer"}
SUCCESS_STATUSES: Final = frozenset({200, 201})
RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE: Final = 0.5
BACKOFF_CAP: Final = 8.0
RETRY_AFTER_CAP: Final = 30.0

MISSING_API_KEY_MESSAGE: Final = (
    "STAAN_API_KEY is not set. Create a key in the Staan console and set the "
    "STAAN_API_KEY environment variable for the MCP server."
)


def search_body(  # noqa: PLR0913  (mirrors the request parameters one to one)
    *,
    query: str,
    market: str,
    offset: int,
    full_content: FullContentFormat | None,
    include_domains: Sequence[str] | None,
    exclude_domains: Sequence[str] | None,
) -> dict[str, Any]:
    """Build the JSON body of ``POST /search/web``."""
    body: dict[str, Any] = {
        "q": query,
        "market": market,
        "count": SEARCH_COUNT,
        "offset": offset,
        "extra_snippets": True,
    }
    if full_content is not None:
        body["full_content"] = full_content
    if include_domains:
        body["include_domains"] = list(include_domains)
    if exclude_domains:
        body["exclude_domains"] = list(exclude_domains)
    return body


def answer_body(  # noqa: PLR0913  (mirrors the request parameters one to one)
    *,
    query: str,
    language: Literal["en", "fr"],
    mode: Literal["short", "long"],
    filter_domain: str | None,
    sources: Sequence[Mapping[str, str]] | None,
    sources_mode: Literal["search", "context"],
) -> dict[str, Any]:
    """Build the JSON body of ``POST /answer``."""
    body: dict[str, Any] = {
        "query": query,
        "language": language,
        "mode": mode,
        "stream": False,
        "markdown": True,
    }
    if filter_domain:
        body["filter"] = filter_domain
    if sources:
        body["sources"] = [dict(source) for source in sources]
        body["sources_mode"] = sources_mode
    return body


def request_headers(api_key: str, user_agent: str) -> dict[str, str]:
    """Build the request headers every client sends."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": user_agent,
    }


def backoff_delay(attempt: int, retry_after: float) -> float:
    """Return how long to wait after failed attempt number ``attempt`` (0-based).

    Args:
        attempt: Zero-based index of the attempt that just failed.
        retry_after: Extra delay requested by the server, in seconds.

    Returns:
        ``min(2 ** attempt * 0.5, 8)`` plus ``retry_after``.
    """
    return min(BACKOFF_BASE * 2.0**attempt, BACKOFF_CAP) + retry_after


def parse_retry_after(value: str) -> float:
    """Parse a numeric ``Retry-After`` header value; anything else counts as zero.

    The result is capped at 30 seconds. HTTP-date values, negative and non-finite numbers are
    ignored.
    """
    try:
        seconds = float(value)
    except ValueError:
        return 0.0
    if not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return min(seconds, RETRY_AFTER_CAP)


def json_object(content: bytes, status_code: int, endpoint: Endpoint) -> dict[str, Any]:
    """Decode a successful response body, which must be a JSON object.

    Raises:
        StaanResponseError: If the body is not valid JSON or not a JSON object.
    """
    try:
        payload = json.loads(content)
    except (ValueError, RecursionError) as exc:
        msg = "Staan returned a response that is not valid JSON."
        raise StaanResponseError(msg, status_code=status_code, endpoint=endpoint) from exc
    if not isinstance(payload, dict):
        msg = "Staan returned a response that is not a JSON object."
        raise StaanResponseError(msg, status_code=status_code, endpoint=endpoint)
    return payload


def deadline_message(deadline: float) -> str:
    """Describe a call that did not finish within its overall deadline."""
    return f"The call to Staan did not finish within {deadline:g} s."


def transport_failure_message(error_name: str, *, timed_out: bool, timeout: float) -> str:
    """Describe a failed HTTP exchange without echoing exception text.

    Args:
        error_name: Class name of the library exception, shown for a failure other than a timeout.
        timed_out: Whether the exchange ran into the per-attempt timeout.
        timeout: The per-attempt timeout in seconds.
    """
    if timed_out:
        return f"The request to Staan timed out after {timeout:g} s."
    return f"Could not complete the request to Staan ({error_name})."


def request_not_built_message(error_name: str) -> str:
    """Describe a request that could not be built from the configured URL or key."""
    return (
        "The request to Staan could not be built: check STAAN_BASE_URL and STAAN_API_KEY "
        f"({error_name})."
    )


def response_too_large_message(limit: int) -> str:
    """Describe a response body that passed the ``limit`` (in bytes) a client reads."""
    return f"Staan returned a response larger than {limit >> 20} MiB, which was discarded."
