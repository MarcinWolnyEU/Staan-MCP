"""A fake Staan upstream on a loopback socket, for the tests of the standalone stdio server.

``stdio_server.py`` talks to the network through ``urllib``, which ``httpx2.MockTransport`` cannot
intercept, so unlike :class:`staan_fakes.FakeStaan` this fake is a real HTTP server running in a
thread. It records every request and replies from the same kind of script as ``FakeStaan``: one
reply for the search endpoint, optional per-market replies and one for the answer endpoint. A
reply may also be a list; its items are used in turn and the last one repeats. Nothing here
leaves the machine: the base URL is ``http://127.0.0.1:<port>``, which the configuration accepts
because the host is loopback.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import TracebackType

ARRIVAL_TIMEOUT = 5.0
"""How long a reply with ``after`` waits for the other requests before it answers anyway."""


@dataclass(frozen=True)
class Reply:
    """One scripted HTTP reply.

    Attributes:
        status: HTTP status line code.
        body: Raw body bytes.
        headers: Extra response headers (for example ``Retry-After`` or ``Content-Encoding``).
        delay: Seconds to wait before answering.
        after: Hold the answer until the upstream has received this many requests in all (this
            one included), as a real network does: its answers cannot arrive before the other
            concurrent requests have been sent. Gives up after ``ARRIVAL_TIMEOUT`` seconds.
        drop: Close the connection without answering at all.
        declared_length: Content-Length to announce instead of the real body length (a larger
            value makes the client see a truncated body).
    """

    status: int = 201
    body: bytes = b"{}"
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    after: int = 0
    drop: bool = False
    declared_length: int | None = None


def json_reply(payload: object, status: int = 201) -> Reply:
    """Build a JSON reply (Staan answers a POST with 201)."""
    return Reply(status, json.dumps(payload).encode())


Script = Reply | list[Reply]


@dataclass(frozen=True)
class Recorded:
    """A request the fake received.

    ``at`` is when its handler started, on the monotonic clock (``time.monotonic()``): the
    closest the tests get to "when Staan saw it", for the rate limit tests.
    """

    path: str
    headers: dict[str, str]
    body: dict[str, Any]
    at: float = 0.0


class _Server(ThreadingHTTPServer):
    """The HTTP server; its listen backlog is long enough for a burst of 20 connections.

    The default of 5 lets the operating system drop the surplus connection attempts of a burst
    when the machine is busy, and the client's retransmission arrives a second late: that would
    be a late arrival the server under test never caused.
    """

    request_queue_size = 128
    daemon_threads = True


class LocalStaan:
    """A recording Staan upstream listening on an ephemeral loopback port."""

    def __init__(self) -> None:
        """Start listening; use it as a context manager so the socket is closed again."""
        self.search: Script = json_reply({"web": {"results": []}})
        self.by_market: dict[str, Script] = {}
        self.answer: Script = json_reply({"answer": "Hi"})
        self.requests: list[Recorded] = []
        self._lock = threading.Condition()
        self._used: dict[str, int] = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            """Answers every POST from the script."""

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                """Record the request and play the scripted reply."""
                arrived = time.monotonic()
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                reply = owner.pick(self.path, body)
                owner.record(Recorded(self.path, dict(self.headers.items()), body, arrived))
                owner.wait_for_requests(reply.after)
                time.sleep(reply.delay)
                try:
                    self.respond(reply)
                except OSError:  # the client gave up (a timeout test)
                    self.close_connection = True

            def respond(self, reply: Reply) -> None:
                """Write ``reply``, or drop the connection."""
                if reply.drop:
                    self.connection.close()
                    return
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                length = len(reply.body) if reply.declared_length is None else reply.declared_length
                self.send_header("Content-Length", str(length))
                self.end_headers()
                self.wfile.write(reply.body)
                self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # pylint: disable=redefined-builtin
                """Stay quiet."""

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        """The URL to put in ``STAAN_BASE_URL``."""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def pick(self, path: str, body: dict[str, Any]) -> Reply:
        """Choose the scripted reply for a request."""
        if path.endswith("/answer"):
            key, script = "answer", self.answer
        else:
            key = body["market"]
            script = self.by_market.get(key, self.search)
        if isinstance(script, Reply):
            return script
        with self._lock:
            index = self._used.get(key, 0)
            self._used[key] = index + 1
        return script[min(index, len(script) - 1)]

    def record(self, request: Recorded) -> None:
        """Remember a request (called from the server threads)."""
        with self._lock:
            self.requests.append(request)
            self._lock.notify_all()

    def wait_for_requests(self, count: int) -> None:
        """Block until ``count`` requests have arrived (or ``ARRIVAL_TIMEOUT`` has passed)."""
        with self._lock:
            self._lock.wait_for(lambda: len(self.requests) >= count, ARRIVAL_TIMEOUT)

    def arrivals(self) -> list[float]:
        """When the requests arrived (monotonic seconds), earliest first."""
        with self._lock:
            return sorted(request.at for request in self.requests)

    def bodies(self) -> list[dict[str, Any]]:
        """The JSON bodies of all recorded requests."""
        return [request.body for request in self.requests]

    def close(self) -> None:
        """Stop listening and release the socket."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def __enter__(self) -> Self:
        """Return the running fake."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the fake."""
        self.close()
