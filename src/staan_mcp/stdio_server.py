#!/usr/bin/env python3
r"""Staan MCP server for stdio that needs nothing installed.

This is the second way to run the server (the first is ``pip install staan-mcp`` or
``uvx staan-mcp``). Download the repository and point an MCP client at this one file: plain
Python 3.11+ is all it takes, with no ``pip install``, no virtual environment and no third-party
package. The file puts its own ``src`` directory on ``sys.path`` and reuses the standard-library
modules of the ``staan_mcp`` package (markets, domains, formatting, errors, configuration and
the shared tool contract), so it must stay inside the repository; copied alone it says so and
exits with status 2. The third way is the release bundle ``staan-mcp-stdio.pyz``: the same
modules zipped into one file (``scripts/build_zipapp.py``) that Python runs directly. The file
never imports the MCP SDK, ``httpx2``, ``pydantic``, ``anyio`` or ``pyjwt``; a test enforces
that.

It offers the same two read-only tools as the package (``web_search`` and ``answer``), calls the
same Staan endpoints with the same request bodies, retry policy and error messages, and shapes
results the same way. What it leaves out on purpose: the HTTP transports and the OAuth gate (use
the installed package for those). The only parts written twice are the ones that cannot be
shared: a synchronous ``urllib`` transport instead of ``httpx2``, hand-written argument checks
instead of pydantic's, and the JSON-RPC loop instead of the MCP SDK.

Configuration comes from environment variables, exactly as for the package:
``STAAN_API_KEY`` (required to call a tool), ``STAAN_BASE_URL``, ``STAAN_TIMEOUT``,
``STAAN_DEADLINE``, ``STAAN_MAX_RETRIES``, ``STAAN_RATE_LIMIT`` and
``STAAN_MCP_MAX_CONTENT_CHARS``.

Messages are newline-delimited JSON-RPC 2.0 on stdin/stdout, logs go to stderr.

Exit status: 0 when the client closes stdin, 1 when it vanished (closed its end of stdout) before
it had its replies, 130 after Ctrl-C and 2 for bad arguments or a copy of the file alone. The
vanished client and Ctrl-C leave quietly: nothing is printed, not even a traceback.

Sample config:
```
    "staan": {
      "type": "stdio",
      "command": ["python", "C:\\example-path\\staan-mcp\\src\\staan_mcp\\stdio_server.py"],
      "env": {
        "STAAN_API_KEY": "your-staan-api-key"
      }
    }
```
"""
# The whole point of this file is that it stands alone (download it, run it): it is not split.
# pylint: disable=too-many-lines

from __future__ import annotations

import http.client
import importlib
import json
import os
import queue
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Final, NoReturn, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from email.message import Message
    from typing import IO, TypeGuard

_MIN_PYTHON: Final = (3, 11)

if sys.version_info < _MIN_PYTHON:  # pragma: no cover  (the gates run on 3.11+)
    sys.stderr.write(
        f"staan-mcp needs Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]} or newer; this is "
        f"{sys.version.split()[0]} ({sys.executable}). Point the MCP client's command at a newer "
        "Python.\n"
    )
    raise SystemExit(2)


_PACKAGE_NOT_FOUND: Final = (
    "staan-mcp: cannot import the staan_mcp package. stdio_server.py must stay inside its "
    "repository, in src/staan_mcp next to the other staan_mcp modules (this copy is in "
    "{directory}). Otherwise use the installed package (pip install staan-mcp, or uvx staan-mcp) "
    "or the single-file release bundle staan-mcp-stdio.pyz.\n"
)


def _make_package_importable() -> None:
    """Make ``staan_mcp`` importable, or explain on stderr and exit with status 2.

    Run by path from a source checkout, the script's own directory is ``sys.path[0]``, so the
    package is not importable: its parent directory is put on the path. Installed, run with
    ``python -m``, or run from the release bundle (a zip archive that is already on the path),
    nothing changes. A copy of this one file taken out of its repository finds no package at
    all; that is reported in one line instead of a ``ModuleNotFoundError`` traceback.
    """
    root = Path(__file__).resolve().parent.parent
    if not any(Path(entry or ".").resolve() == root for entry in sys.path):
        sys.path.insert(0, str(root))
    try:
        importlib.import_module("staan_mcp.config")
    except ModuleNotFoundError:
        sys.stderr.write(_PACKAGE_NOT_FOUND.format(directory=Path(__file__).resolve().parent))
        raise SystemExit(2) from None


_make_package_importable()

# pylint: disable=wrong-import-position

# Only modules of the package that import nothing but the standard library may be used here: not
# ``client``, ``server``, ``auth`` or ``cli`` (tests/integration/test_stdio_standalone.py checks).
from staan_mcp.config import DEFAULT_RATE_LIMIT, Settings  # noqa: E402
from staan_mcp.contract import (  # noqa: E402
    FULL_CONTENT_CHOICES,
    INSTRUCTIONS,
    LANGUAGE_CHOICES,
    MAX_ANSWER_QUERY_LENGTH,
    MAX_ANSWER_SOURCES,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_SOURCE_CONTENT_LENGTH,
    MAX_SOURCE_TITLE_LENGTH,
    MODE_CHOICES,
    OFFSET_CHOICES,
    SERVER_NAME,
    SOURCES_MODE_CHOICES,
    TOOL_LIST,
)
from staan_mcp.domains import (  # noqa: E402
    InvalidDomainError,
    normalize_domain,
    normalize_domain_filters,
)
from staan_mcp.errors import (  # noqa: E402
    ConfigurationError,
    StaanMCPError,
    StaanResponseError,
    StaanTransportError,
    error_from_response,
    is_fatal,
)
from staan_mcp.markets import DEFAULT_MARKET, InvalidMarketError, expand_markets  # noqa: E402
from staan_mcp.payloads import (  # noqa: E402
    Outcome,
    answer_payload,
    raise_for_failures,
    search_payload,
)
from staan_mcp.ratelimit import RateLimiter  # noqa: E402
from staan_mcp.sources import check_source_url, require_url_or_content  # noqa: E402
from staan_mcp.upstream import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    MISSING_API_KEY_MESSAGE,
    PATHS,
    RETRY_STATUSES,
    SUCCESS_STATUSES,
    Endpoint,
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

# pylint: enable=wrong-import-position

VERSION: Final = "1.0.1"
"""Same as the package version; ``cz bump`` rewrites it and a test compares it."""

SUPPORTED_PROTOCOL_VERSIONS: Final = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
"""Protocol revisions reachable through the ``initialize`` handshake, oldest first."""

_USER_AGENT: Final = f"staan-mcp-stdio/{VERSION} (+https://github.com/MarcinWolnyEU/Staan-MCP)"
_HARD_DEADLINE_GRACE: Final = 1.0
_READ_CHUNK: Final = 65536
_CANCELLED: Final = "The call was cancelled."
_EXIT_CLIENT_GONE: Final = 1
"""Exit status when the client vanished (a reply could not be written): nothing is printed."""
_EXIT_INTERRUPTED: Final = 130
"""Exit status after Ctrl-C (SIGINT): 128 plus the signal number, as a shell reports it."""
_UNLIMITED: Final = RateLimiter(0)
"""The limiter of a caller that has none: it never delays and remembers nothing.

``Server`` always passes its own; this default is for direct callers of ``call_tool`` and ``_post``.
"""

_JSON = dict[str, Any]
_T = TypeVar("_T")


_LOG_LOCK = threading.Lock()
_LOG_CLOSED = threading.Event()


def _log(message: str) -> None:
    """Write one diagnostic line to stderr (stdout carries the protocol and nothing else)."""
    with _LOG_LOCK:  # held for the whole write: ``_close_log`` waits for a line in progress
        if _LOG_CLOSED.is_set():
            return
        try:
            sys.stderr.write(f"staan-stdio: {message}\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


def _close_log() -> None:
    """Never write to stderr again, once any line in progress is finished.

    Tool calls run on daemon threads that may still be running when the process leaves. A thread
    that holds the lock of ``sys.stderr`` while the interpreter shuts down aborts it ("could not
    acquire lock for <stderr> at interpreter shutdown", SIGABRT), so after this nothing writes.
    """
    with _LOG_LOCK:
        _LOG_CLOSED.set()


class ToolInputError(Exception):
    """A tool argument is wrong; the message is safe to show to the model."""


# --------------------------------------------------------------------------------------
# Upstream transport: urllib instead of httpx2, with the same retry policy as StaanClient
# --------------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: the API key must never follow a redirect to another host."""

    def redirect_request(  # noqa: PLR0913, PLR0917  # pylint: disable=too-many-positional-arguments
        self,
        req: urllib.request.Request,  # noqa: ARG002
        fp: IO[bytes],  # noqa: ARG002
        code: int,  # noqa: ARG002
        msg: str,  # noqa: ARG002
        headers: Message,  # noqa: ARG002
        newurl: str,  # noqa: ARG002
    ) -> None:
        """Never build the redirected request, so the 3xx answer is reported as it is."""
        return


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass(frozen=True, slots=True)
class _Retry:
    """A failed attempt that may be retried."""

    error: StaanMCPError
    retry_after: float = 0.0


def _read_body(response: http.client.HTTPResponse | urllib.error.HTTPError) -> bytes:
    """Read a response body (gzip-decoded), refusing anything above ``MAX_RESPONSE_BYTES``."""
    encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding not in {"identity", "gzip", "x-gzip"}:
        msg = "Staan returned a response in an unsupported encoding."
        raise StaanResponseError(msg)
    decoder = None if encoding == "identity" else zlib.decompressobj(16 + zlib.MAX_WBITS)
    received = bytearray()
    sent = 0  # bytes read off the wire, which is what Content-Length counts
    while chunk := response.read(_READ_CHUNK):
        sent += len(chunk)
        if decoder is None:
            received += chunk
        else:  # never inflate more than one byte past the limit: that is enough to refuse it
            received += decoder.decompress(chunk, MAX_RESPONSE_BYTES - len(received) + 1)
        if len(received) > MAX_RESPONSE_BYTES:
            raise StaanResponseError(response_too_large_message(MAX_RESPONSE_BYTES))
    declared = response.headers.get("Content-Length", "")
    if declared.isdecimal() and sent < int(declared):  # read(amt) does not notice a cut-off body
        raise http.client.IncompleteRead(bytes(received), int(declared) - sent)
    return bytes(received)


def _exchange(
    url: str, headers: dict[str, str], payload: bytes, timeout: float
) -> tuple[int, Message, bytes]:
    """Send one POST and read the whole answer; returns ``(status, headers, body)``."""
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")  # noqa: S310
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.headers, _read_body(response)
    except urllib.error.HTTPError as err:
        try:
            return err.code, err.headers, _read_body(err)
        finally:
            err.close()


def _transport_error(exc: BaseException, endpoint: Endpoint, timeout: float) -> StaanTransportError:
    """Describe a failure to complete the HTTP exchange, without echoing exception text."""
    message = transport_failure_message(
        type(exc).__name__, timed_out=isinstance(exc, TimeoutError), timeout=timeout
    )
    error = StaanTransportError(message, endpoint=endpoint)
    error.__cause__ = exc
    return error


def _attempt(
    endpoint: Endpoint, url: str, headers: dict[str, str], payload: bytes, timeout: float
) -> _JSON | _Retry:
    """Send the request once: the decoded body, or a :class:`_Retry` for a transient failure.

    Raises:
        StaanMCPError: For a failure that must not be retried.
    """
    try:
        status, response_headers, body = _exchange(url, headers, payload, timeout)
    except urllib.error.URLError as exc:  # could not connect or send (DNS, refused, TLS, ...)
        reason = exc.reason if isinstance(exc.reason, BaseException) else exc
        return _Retry(_transport_error(reason, endpoint, timeout))
    except TimeoutError as exc:
        # The request was (or may have been) sent, and every request is billable: a second
        # attempt would pay twice for the same slow answer.
        raise _transport_error(exc, endpoint, timeout) from exc
    except zlib.error as exc:  # what httpx2 reports as a DecodingError
        message = transport_failure_message("DecodingError", timed_out=False, timeout=timeout)
        raise StaanTransportError(message, endpoint=endpoint) from exc
    except (http.client.InvalidURL, ValueError) as exc:  # a URL or header that cannot be built
        raise ConfigurationError(request_not_built_message(type(exc).__name__)) from exc
    except (OSError, http.client.HTTPException) as exc:  # connection dropped mid-exchange
        return _Retry(_transport_error(exc, endpoint, timeout))
    if status in SUCCESS_STATUSES:
        return json_object(body, status, endpoint)
    error = error_from_response(status, body, endpoint=endpoint)
    if status in RETRY_STATUSES:
        return _Retry(error, parse_retry_after(response_headers.get("Retry-After", "")))
    raise error


def _cut_short(
    error: StaanMCPError, settings: Settings, endpoint: Endpoint, attempt_timeout: float
) -> StaanMCPError:
    """``error``, or the deadline error when the call deadline (not the timeout) ended the attempt.

    ``_post`` shortens an attempt's socket timeout to what is left of the deadline. An attempt
    that timed out with such a shortened timeout ended exactly at the deadline, where the
    package's ``anyio.fail_after`` would have fired first, so the answer is the same sentence,
    not "timed out after <the shortened timeout>". No clock is read: a coarse clock (15.6 ms
    ticks on Windows before Python 3.13) could still show the deadline as not yet reached.
    """
    if isinstance(error.__cause__, TimeoutError) and attempt_timeout < settings.timeout:
        return StaanTransportError(deadline_message(settings.deadline), endpoint=endpoint)
    return error


def _wait_for_slot(
    limiter: RateLimiter,
    settings: Settings,
    endpoint: Endpoint,
    stop: threading.Event,
    cutoff: float,
) -> None:
    """Wait until the rate limit lets one more request start (see ``RateLimiter``).

    Called right before every send, so every retry takes a slot of its own. The wait is part of
    the call: a slot that would come after ``cutoff`` (the call deadline) is refused at once
    with the deadline error, nothing booked, like the package's ``anyio.fail_after`` would end
    the call. ``stop`` ends the wait (the call was cancelled) and gives the slot back.

    Raises:
        StaanTransportError: If ``stop`` is set or the slot would come after the deadline.
    """
    if stop.is_set():
        raise StaanTransportError(_CANCELLED, endpoint=endpoint)
    slot = limiter.reserve(cutoff - time.monotonic())
    if slot is None:
        raise StaanTransportError(deadline_message(settings.deadline), endpoint=endpoint)
    if slot.delay <= 0:
        return
    _log(
        f"rate limit: {endpoint} request delayed {slot.delay:.2f} s to keep to "
        f"{settings.rate_limit:g} requests per second"
    )
    while (left := limiter.remaining(slot)) > 0:  # a wait may end early; the clock decides
        if stop.wait(left):
            limiter.release(slot)
            raise StaanTransportError(_CANCELLED, endpoint=endpoint)


def _post(
    settings: Settings,
    endpoint: Endpoint,
    body: _JSON,
    stop: threading.Event,
    limiter: RateLimiter = _UNLIMITED,
) -> _JSON:
    """POST ``body`` to ``endpoint``, retrying transient failures within the call deadline.

    ``stop`` is the cancellation of the call (the client cancelled it, or a sibling market failed
    fatally): once set, nothing more is sent, which is what cancelling a sibling does in the
    package. A request already on the wire cannot be recalled. Each send first takes a slot from
    ``limiter``, the server's request rate limit; waiting for it counts against the deadline and
    not against the attempt timeout.

    Raises:
        StaanMCPError: If the key is missing (nothing is sent), the request fails for good, the
            deadline passes or ``stop`` is set.
    """
    if not settings.api_key:
        raise ConfigurationError(MISSING_API_KEY_MESSAGE)
    headers = {
        **request_headers(settings.api_key, _USER_AGENT),
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }
    url = settings.base_url + PATHS[endpoint]
    payload = json.dumps(body).encode("utf-8")
    cutoff = time.monotonic() + settings.deadline
    attempt = 0
    while True:
        _wait_for_slot(limiter, settings, endpoint, stop, cutoff)
        remaining = cutoff - time.monotonic()
        if remaining <= 0:
            raise StaanTransportError(deadline_message(settings.deadline), endpoint=endpoint)
        attempt_timeout = min(settings.timeout, remaining)
        try:
            outcome = _attempt(endpoint, url, headers, payload, attempt_timeout)
        except StaanTransportError as error:
            raise _cut_short(error, settings, endpoint, attempt_timeout) from error.__cause__
        if not isinstance(outcome, _Retry):
            return outcome
        if attempt >= settings.max_retries:
            raise _cut_short(outcome.error, settings, endpoint, attempt_timeout) from (
                outcome.error.__cause__
            )
        delay = backoff_delay(attempt, outcome.retry_after)
        stop.wait(min(delay, max(cutoff - time.monotonic(), 0.0)))  # the top of the loop checks it
        attempt += 1


def _run_parallel(
    jobs: Sequence[Callable[[], _JSON]], deadline: float, stop: threading.Event
) -> list[_JSON | Exception]:
    """Run ``jobs`` in threads and collect their results (or exceptions), in job order.

    A fatal error (key or configuration problem) sets ``stop`` and ends the wait, since every
    other job would fail the same way; a job that has not sent its request yet then sends nothing
    (see ``_post``), while one already waiting for its answer cannot be taken back. Jobs still
    running at the hard deadline are abandoned and reported as timed out.
    """
    results: queue.Queue[tuple[int, _JSON | Exception]] = queue.Queue()

    def worker(index: int, job: Callable[[], _JSON]) -> None:
        try:
            results.put((index, job()))
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            results.put((index, exc))

    for index, job in enumerate(jobs):
        threading.Thread(target=worker, args=(index, job), daemon=True).start()
    outcomes: list[_JSON | Exception] = [StaanTransportError(deadline_message(deadline))] * len(
        jobs
    )
    pending = set(range(len(jobs)))
    hard_stop = time.monotonic() + deadline + _HARD_DEADLINE_GRACE
    while pending:
        try:
            index, outcome = results.get(timeout=max(hard_stop - time.monotonic(), 0.0))
        except queue.Empty:
            break
        outcomes[index] = outcome
        pending.discard(index)
        if isinstance(outcome, StaanMCPError) and is_fatal(outcome):
            stop.set()
            break
    return outcomes


# --------------------------------------------------------------------------------------
# Tool arguments: the checks pydantic does for the package, with the same rules
# --------------------------------------------------------------------------------------

_TRUE_WORDS: Final = frozenset({"1", "on", "t", "true", "y", "yes"})
_FALSE_WORDS: Final = frozenset({"0", "off", "f", "false", "n", "no"})


def _fail(name: str, problem: str) -> NoReturn:
    """Reject argument ``name``; the message is safe to show to the model."""
    msg = f"Invalid argument '{name}': {problem}."
    raise ToolInputError(msg)


def _text(
    args: Mapping[str, Any], name: str, *, required: bool = False, max_length: int | None = None
) -> str | None:
    """Read a required (non-empty) or optional (nullable) string argument."""
    if name not in args:
        if required:
            msg = f"Missing required argument '{name}'."
            raise ToolInputError(msg)
        return None
    value = args[name]
    if value is None and not required:
        return None
    if not isinstance(value, str):
        _fail(name, "expected a string")
    if required and not value:
        _fail(name, "must be at least 1 character long")
    if max_length is not None and len(value) > max_length:
        _fail(name, f"must be at most {max_length} characters long (got {len(value)})")
    return value


def _choice(args: Mapping[str, Any], name: str, choices: Sequence[_T]) -> _T:
    """Read an argument that must be one of ``choices``; the first one is the default."""
    if name not in args:
        return choices[0]
    value = args[name]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    for choice in choices:
        if value == choice:  # by value, as pydantic does: ``False`` is the choice ``0``
            return choice
    _fail(name, f"must be one of {', '.join(repr(choice) for choice in choices)}")


def _flag(args: Mapping[str, Any], name: str) -> bool:
    """Read a boolean argument (default true); accepts what pydantic's lax mode accepts."""
    if name not in args:
        return True
    value = args[name]
    if isinstance(value, str):
        if value.lower() in _TRUE_WORDS:
            return True
        if value.lower() in _FALSE_WORDS:
            return False
    elif isinstance(value, int | float) and value in {0, 1}:
        return bool(value)
    _fail(name, "expected true or false")


def _str_list(args: Mapping[str, Any], name: str) -> list[str] | None:
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _fail(name, "expected a list of strings")
    return value


def _market(args: Mapping[str, Any]) -> str | list[str]:
    value = args.get("market", DEFAULT_MARKET)
    if isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(item, str) for item in value)
    ):
        return value
    _fail("market", "expected a string or a list of strings")


def _source(value: object, index: int) -> _JSON:
    """Validate one ``sources`` item; unknown keys are ignored."""
    if not isinstance(value, dict):
        _fail("sources", f"item {index} must be an object with 'url' and/or 'content'")
    url = value.get("url")
    if url is not None and not isinstance(url, str):
        _fail("sources", f"'url' of item {index} must be a string")
    title = _text(value, "title", max_length=MAX_SOURCE_TITLE_LENGTH)
    content = _text(value, "content", max_length=MAX_SOURCE_CONTENT_LENGTH)
    try:
        clean_url = None if url is None else check_source_url(url)
        require_url_or_content(clean_url, content)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    fields = {"url": clean_url, "title": title, "content": content}
    return {key: field for key, field in fields.items() if field is not None}


def _sources(args: Mapping[str, Any]) -> list[_JSON] | None:
    value = args.get("sources")
    if value is None:
        return None
    if not isinstance(value, list):
        _fail("sources", "expected a list of objects")
    if len(value) > MAX_ANSWER_SOURCES:
        problem = f"at most {MAX_ANSWER_SOURCES} items are allowed, got {len(value)}"
        _fail("sources", problem)
    return [_source(item, index) for index, item in enumerate(value)]


# --------------------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------------------


def _expect_outcome(outcome: _JSON | Exception) -> Outcome:
    """Pass on an expected outcome of a search; anything else is a bug and is raised."""
    if isinstance(outcome, dict | StaanMCPError):
        return outcome
    raise outcome


def _search_requests(args: _JSON) -> tuple[list[str], list[_JSON], bool]:
    """Check the ``web_search`` arguments: the markets, a request body each, ``simple_response``."""
    query = _text(args, "query", required=True, max_length=MAX_SEARCH_QUERY_LENGTH)
    market = _market(args)
    full_content = _choice(args, "full_content", FULL_CONTENT_CHOICES)
    offset = _choice(args, "offset", OFFSET_CHOICES)
    include_domains = _str_list(args, "include_domains")
    exclude_domains = _str_list(args, "exclude_domains")
    simple_response = _flag(args, "simple_response")

    markets = expand_markets(market)
    include, exclude = normalize_domain_filters(include_domains, exclude_domains)
    bodies = [
        search_body(
            query=str(query),
            market=single_market,
            offset=offset,
            full_content=None if full_content == "none" else full_content,
            include_domains=include,
            exclude_domains=exclude,
        )
        for single_market in markets
    ]
    return markets, bodies, simple_response


def _tool_web_search(
    args: _JSON, settings: Settings, stop: threading.Event, limiter: RateLimiter
) -> list[Any]:
    markets, bodies, simple_response = _search_requests(args)
    jobs = [partial(_post, settings, "search", body, stop, limiter) for body in bodies]
    outcomes = [_expect_outcome(o) for o in _run_parallel(jobs, settings.deadline, stop)]
    failed = raise_for_failures(markets, outcomes)
    if failed:
        _log(f"web_search: {len(failed)} of {len(markets)} markets failed: {', '.join(failed)}")
    return search_payload(
        markets,
        outcomes,
        simple_response=simple_response,
        max_content_chars=settings.max_content_chars,
    )


def _tool_answer(
    args: _JSON, settings: Settings, stop: threading.Event, limiter: RateLimiter
) -> _JSON:
    query = _text(args, "query", required=True, max_length=MAX_ANSWER_QUERY_LENGTH)
    filter_domain = _text(args, "filter")
    language = _choice(args, "language", LANGUAGE_CHOICES)
    mode = _choice(args, "mode", MODE_CHOICES)
    sources = _sources(args)
    sources_mode = _choice(args, "sources_mode", SOURCES_MODE_CHOICES)
    simple_response = _flag(args, "simple_response")

    domain = normalize_domain(filter_domain) if filter_domain and filter_domain.strip() else None
    body = answer_body(
        query=str(query),
        language=language,
        mode=mode,
        filter_domain=domain,
        sources=sources,
        sources_mode=sources_mode,
    )
    job = partial(_post, settings, "answer", body, stop, limiter)
    (response,) = _run_parallel([job], settings.deadline, stop)
    if isinstance(response, Exception):
        raise response
    return answer_payload(response, simple_response=simple_response)


_TOOLS: Final[dict[str, Callable[[_JSON, Settings, threading.Event, RateLimiter], Any]]] = {
    "web_search": _tool_web_search,
    "answer": _tool_answer,
}
_EXPECTED_FAILURES: Final = (StaanMCPError, InvalidMarketError, InvalidDomainError, ToolInputError)


def _error_result(text: str) -> _JSON:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def call_tool(
    name: str,
    arguments: _JSON,
    settings: Settings | ConfigurationError,
    stop: threading.Event,
    limiter: RateLimiter = _UNLIMITED,
) -> _JSON:
    """Run one tool and build its ``tools/call`` result; a failure becomes ``isError`` text."""
    if name not in _TOOLS:
        return _error_result(f"Unknown tool: {name}")
    if isinstance(settings, ConfigurationError):
        return _error_result(f"Error executing tool {name}: {settings}")
    try:
        payload = _TOOLS[name](arguments, settings, stop, limiter)
    except _EXPECTED_FAILURES as exc:
        return _error_result(f"Error executing tool {name}: {exc}")
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # A bug: log the details to stderr and tell the model only that the tool failed.
        _log(f"internal error in tool {name!r}:\n{traceback.format_exc()}")
        return _error_result(f"Error executing tool {name}")
    text = json.dumps(payload, ensure_ascii=False)
    result: _JSON = {"content": [{"type": "text", "text": text}]}
    if name == "answer":  # the only tool with an output schema
        result["structuredContent"] = payload
    return result


# --------------------------------------------------------------------------------------
# MCP protocol: newline-delimited JSON-RPC 2.0 over stdin/stdout
# --------------------------------------------------------------------------------------

_PARSE_ERROR: Final = -32700
_INVALID_REQUEST: Final = -32600
_METHOD_NOT_FOUND: Final = -32601
_INVALID_PARAMS: Final = -32602
_UNKNOWN_PROMPT: Final = 0  # the code the SDK answers for a prompt that does not exist

_CAPABILITIES: Final = {
    "prompts": {"listChanged": False},
    "resources": {"listChanged": False, "subscribe": False},
    "tools": {"listChanged": False},
}
_STATIC_RESULTS: Final[dict[str, _JSON]] = {
    "ping": {},
    "tools/list": {"tools": TOOL_LIST},
    "prompts/list": {"prompts": []},
    "resources/list": {"resources": []},
    "resources/templates/list": {"resourceTemplates": []},
}
_OPEN_BEFORE_INITIALIZE: Final = frozenset({"initialize", "ping"})

_RequestKey = tuple[str, object]


def _valid_id(request_id: object) -> bool:
    return isinstance(request_id, str | int) and not isinstance(request_id, bool)


def _request_key(request_id: object) -> _RequestKey:
    return (type(request_id).__name__, request_id)


def _valid_initialize(params: object) -> TypeGuard[_JSON]:
    """Whether ``params`` has what the SDK requires of ``initialize``.

    Only the top level is checked: not the nested content of ``capabilities``, nor the optional
    ``clientInfo`` fields.
    """
    if not isinstance(params, dict):
        return False
    info = params.get("clientInfo")
    return (
        isinstance(params.get("protocolVersion"), str)
        and isinstance(params.get("capabilities"), dict)
        and isinstance(info, dict)
        and isinstance(info.get("name"), str)
        and isinstance(info.get("version"), str)
    )


def _string_map(value: object) -> bool:
    """Whether ``value`` is absent or an object whose values are all strings."""
    return value is None or (
        isinstance(value, dict) and all(isinstance(item, str) for item in value.values())
    )


# One object by nature: the two streams, two locks and the registry of the calls still running.
class Server:  # pylint: disable=too-many-instance-attributes
    """Reads JSON-RPC messages, answers them, and runs each tool call on its own thread."""

    def __init__(
        self, settings: Settings | ConfigurationError, stdin: BinaryIO, stdout: BinaryIO
    ) -> None:
        """Create the server.

        Args:
            settings: The validated settings, or the configuration error to report on tool calls.
            stdin: Binary input stream.
            stdout: Binary output stream.
        """
        self._settings = settings
        # One budget for every tool call of this server, whatever thread it runs on.
        self._limiter = RateLimiter(
            settings.rate_limit if isinstance(settings, Settings) else DEFAULT_RATE_LIMIT
        )
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancelled: set[_RequestKey] = set()
        self._inflight: dict[_RequestKey, threading.Event] = {}
        self._workers: dict[_RequestKey, threading.Thread] = {}
        self._ready = False  # a valid ``initialize`` (or ``notifications/initialized``) opens it
        # Nothing can or may be written any more: the client hung up, or the process is leaving.
        self._closed = False
        self._handlers: dict[str, Callable[[object, object], None]] = {
            "initialize": self._initialize,
            "tools/call": self._start_tool_call,
            "prompts/get": self._get_prompt,
            "resources/read": self._read_resource,
            **{
                method: partial(self._reply_with, result)
                for method, result in _STATIC_RESULTS.items()
            },
        }

    def _send(self, message: _JSON) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            if self._closed:
                return
            try:
                self._stdout.write(line.encode("utf-8", "replace") + b"\n")
                self._stdout.flush()
            except (OSError, ValueError):
                # BrokenPipeError or ConnectionResetError: the client is gone. ValueError: stdout
                # was closed. Either way nobody reads what is written, so stop writing.
                self._closed = True
            else:
                return
        self._stop_calls()

    def _stop_calls(self) -> None:
        """Tell every call that is still running that nobody wants its result any more."""
        with self._state_lock:
            stops = list(self._inflight.values())
        for stop in stops:
            stop.set()

    def close(self) -> None:
        """Stop all output and the calls still running; for the process to leave.

        Waits for a reply that is being written, so that no daemon thread is in the middle of a
        write (and holds the lock of ``sys.stdout``) when the interpreter shuts down.
        """
        with self._write_lock:
            self._closed = True
        self._stop_calls()

    def _reply(self, request_id: object, result: _JSON) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: object, code: int, message: str, data: object = "") -> None:
        error: _JSON = {"code": code, "message": message}
        if data is not None:  # the SDK leaves ``data`` out of the error for an unknown prompt
            error["data"] = data
        self._send({"jsonrpc": "2.0", "id": request_id, "error": error})

    def _invalid_params(self, request_id: object) -> None:
        self._error(request_id, _INVALID_PARAMS, "Invalid request parameters")

    def _reply_with(self, result: _JSON, request_id: object, _params: object) -> None:
        self._reply(request_id, result)

    def serve(self) -> bool:
        """Process messages until the client closes stdin, then let running tool calls finish.

        A client that has hung up (a reply cannot be written) ends the loop at once: the calls
        still running are told to stop and are abandoned, since nobody waits for their results.

        Returns:
            Whether the client was still there at the end; ``False`` if it hung up.
        """
        while not self._closed and (line := self._stdin.readline()):
            if line.strip():
                self._handle_line(line)
        if not self._closed:
            with self._state_lock:
                running = list(self._workers.values())
            for worker in running:
                worker.join()
        return not self._closed

    def _handle_line(self, line: bytes) -> None:
        try:
            message = json.loads(line.decode("utf-8", "replace"))  # as the SDK reads stdin
        except (ValueError, RecursionError):
            self._error(None, _PARSE_ERROR, "Parse error: the message is not valid JSON.")
            return
        if isinstance(message, list):
            self._error(None, _INVALID_REQUEST, "Batch requests are not supported.")
        elif not isinstance(message, dict):
            self._error(None, _INVALID_REQUEST, "Invalid request: expected a JSON object.")
        else:
            self._dispatch(message)

    def _dispatch(self, message: _JSON) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            # A response to a server-initiated request (we send none) is ignored.
            if "id" in message and "result" not in message and "error" not in message:
                request_id = message["id"] if _valid_id(message["id"]) else None
                text = "Invalid request: 'method' must be a string."
                self._error(request_id, _INVALID_REQUEST, text)
        elif "id" not in message:
            self._handle_notification(method, message.get("params"))
        elif _valid_id(message["id"]):
            self._handle_request(message["id"], method, message.get("params"))
        else:
            text = "Invalid request: 'id' must be a string or an integer."
            self._error(None, _INVALID_REQUEST, text)

    def _handle_notification(self, method: str, params: object) -> None:
        # Anything but these two needs no action.
        if method == "notifications/initialized":
            self._ready = True
        elif method == "notifications/cancelled" and isinstance(params, dict):
            self._cancel(params.get("requestId"))

    def _cancel(self, request_id: object) -> None:
        if not _valid_id(request_id):
            return
        key = _request_key(request_id)
        with self._state_lock:
            stop = self._inflight.get(key)
            if stop is not None:
                self._cancelled.add(key)
        if stop is not None:
            stop.set()

    def _handle_request(self, request_id: object, method: str, params: object) -> None:
        handler = self._handlers.get(method)
        if handler is None:
            # Includes ``server/discover``: modern clients then fall back to ``initialize``.
            self._error(request_id, _METHOD_NOT_FOUND, "Method not found", method)
        elif self._ready or method in _OPEN_BEFORE_INITIALIZE:
            handler(request_id, params)
        else:  # like the SDK, which wants the handshake before anything but ``ping``
            self._invalid_params(request_id)

    def _initialize(self, request_id: object, params: object) -> None:
        if not _valid_initialize(params):
            self._invalid_params(request_id)
            return
        requested = params["protocolVersion"]
        version = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[-1]
        )
        self._ready = True
        self._reply(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": _CAPABILITIES,
                "serverInfo": {"name": SERVER_NAME, "version": VERSION},
                "instructions": INSTRUCTIONS,
            },
        )

    def _get_prompt(self, request_id: object, params: object) -> None:
        # No prompts exist, but a request is still checked the way the SDK checks it.
        fields = params if isinstance(params, dict) else {}
        name = fields.get("name")
        if not isinstance(name, str) or not _string_map(fields.get("arguments")):
            self._invalid_params(request_id)
        else:
            self._error(request_id, _UNKNOWN_PROMPT, f"Unknown prompt: {name}", None)

    def _read_resource(self, request_id: object, params: object) -> None:
        uri = params.get("uri") if isinstance(params, dict) else None
        if not isinstance(uri, str):
            self._invalid_params(request_id)
        else:
            self._error(request_id, _INVALID_PARAMS, f"Unknown resource: {uri}", {"uri": uri})

    def _start_tool_call(self, request_id: object, params: object) -> None:
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else None
        if not isinstance(name, str) or not (arguments is None or isinstance(arguments, dict)):
            self._invalid_params(request_id)
            return
        key = _request_key(request_id)
        stop = threading.Event()
        with self._state_lock:
            self._inflight[key] = stop
        worker = threading.Thread(
            target=self._tool_worker,
            args=(request_id, key, name, arguments or {}, stop),
            daemon=True,
        )
        with self._state_lock:
            self._workers[key] = worker
        worker.start()

    def _tool_worker(
        self,
        request_id: object,
        key: _RequestKey,
        name: str,
        arguments: _JSON,
        stop: threading.Event,
    ) -> None:
        result = call_tool(name, arguments, self._settings, stop, self._limiter)
        with self._state_lock:
            self._inflight.pop(key, None)
            cancelled = key in self._cancelled
            self._cancelled.discard(key)
        try:
            if not cancelled:  # a cancelled request gets no response
                self._reply(request_id, result)
        finally:
            # Only now: ``serve`` joins the registered workers at EOF, and one that is still
            # writing its reply must be among them.
            with self._state_lock:
                self._workers.pop(key, None)


def _discard_stdout() -> None:
    """Point file descriptor 1 at the null device, so that the flush at exit cannot fail again.

    A write that failed with a broken pipe leaves its bytes in the buffer of ``sys.stdout``, and
    Python flushes it once more when it exits: that fails again, prints "Exception ignored" and
    turns the exit status into 120.
    """
    try:
        descriptor = sys.stdout.fileno()
    except (OSError, ValueError):  # a stream without a descriptor: nothing to redirect
        return
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, descriptor)
    finally:
        os.close(null)


def _serve(settings: Settings | ConfigurationError) -> int:
    """Serve on stdin and stdout; the exit status says how it ended, and nothing is printed."""
    server = Server(settings, sys.stdin.buffer, sys.stdout.buffer)
    try:
        stayed = server.serve()
    except KeyboardInterrupt:
        server.close()
        _close_log()
        return _EXIT_INTERRUPTED
    if stayed:
        return 0
    _discard_stdout()
    _close_log()
    return _EXIT_CLIENT_GONE


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: serve MCP over stdio, or print the version.

    Returns:
        The exit status: 0 when the client closed stdin (or after ``--version`` and ``--help``), 1
        when the client hung up before it had its replies, 2 for bad arguments and 130 after
        Ctrl-C. Neither 1 nor 130 prints anything.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["-V"]):
        sys.stdout.write(f"staan-stdio {VERSION}\n")
        return 0
    if args:
        usage = (
            "usage: staan-mcp-stdio [--version]\n\n"
            "Serves MCP over stdio; configure with STAAN_* variables.\n"
        )
        sys.stderr.write(usage)
        return 0 if args in (["-h"], ["--help"]) else 2
    settings: Settings | ConfigurationError
    try:
        settings = Settings.from_env(os.environ)
    except ConfigurationError as exc:
        _log(f"configuration problem (tools will report it): {exc}")
        settings = exc
    return _serve(settings)


if __name__ == "__main__":
    raise SystemExit(main())
