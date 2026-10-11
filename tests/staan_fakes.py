"""Test doubles shared by the integration tests: a fake Staan upstream and a server factory.

The fake plugs into :class:`staan_mcp.client.StaanClient` through ``httpx2.MockTransport``, so
the whole request path (tool, client, retries, error mapping) runs for real; only the network is
replaced. Nothing here talks to the internet.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx2
from mcp import Client
from mcp.types import CallToolResult, TextContent

from staan_mcp.client import StaanClient
from staan_mcp.config import Settings
from staan_mcp.server import create_server

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from staan_mcp.config import AuthConfig

API_KEY = "test-key-0123456789"

Reply = httpx2.Response | Exception | Callable[[httpx2.Request], Awaitable[httpx2.Response]]

BODY_401 = b'{"message":"Unauthorized","statusCode":401}'
BODY_403_DISABLED = b'{"code":"KEY_DISABLED","message":"API key is disabled"}'
BODY_403_FORBIDDEN = b'{"message":"Forbidden","statusCode":403}'
BODY_400 = b'{"message":["market must be a valid market"],"error":"Bad Request","statusCode":400}'


def search_response(*urls: str) -> dict[str, Any]:
    """Build a Staan search response body with one result per URL."""
    return {
        "search_id": "search-1",
        "query": {"q": "x"},
        "web": {
            "results": [
                {"title": f"Title {url}", "url": url, "snippet": f"Snippet {url}"} for url in urls
            ]
        },
    }


def json_reply(payload: object, status: int = 201) -> httpx2.Response:
    """Build a successful JSON reply (Staan answers a search POST with 201)."""
    return httpx2.Response(status, json=payload)


def error_reply(status: int, body: bytes = b"") -> httpx2.Response:
    """Build an error reply with a raw body."""
    return httpx2.Response(status, content=body)


async def instant_sleep(_delay: float) -> None:
    """Replace the client's retry sleep so tests never wait."""


@dataclass
class FakeStaan:
    """A recording fake of the Staan API.

    Attributes:
        search: Reply for ``/search/web`` when the market has no entry in ``by_market``.
        by_market: Per-market replies for ``/search/web``.
        answer: Reply for ``/answer``.
        requests: Every request received, in arrival order.
    """

    search: Reply = field(default_factory=lambda: json_reply(search_response("https://a.example")))
    by_market: dict[str, Reply] = field(default_factory=dict)
    answer: Reply = field(default_factory=lambda: json_reply({"answer": "Hi"}, 201))
    requests: list[httpx2.Request] = field(default_factory=list)

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Handle one request; this is the ``MockTransport`` handler."""
        self.requests.append(request)
        if request.url.path.endswith("/answer"):
            reply = self.answer
        else:
            market = json.loads(request.content)["market"]
            reply = self.by_market.get(market, self.search)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx2.Response):
            return reply
        return await reply(request)

    def bodies(self) -> list[dict[str, Any]]:
        """Decode the JSON bodies of all recorded requests."""
        return [json.loads(request.content) for request in self.requests]

    @property
    def body(self) -> dict[str, Any]:
        """The JSON body of the only recorded request."""
        assert len(self.requests) == 1
        return self.bodies()[0]

    def markets(self) -> list[str]:
        """The markets requested so far, sorted."""
        return sorted(body["market"] for body in self.bodies())

    def client_factory(self) -> Callable[[Settings], StaanClient]:
        """Return a ``client_factory`` for :func:`create_server` that talks to this fake."""

        def factory(settings: Settings) -> StaanClient:
            return StaanClient(settings, transport=httpx2.MockTransport(self), sleep=instant_sleep)

        return factory


def make_server(
    fake: FakeStaan,
    *,
    api_key: str | None = API_KEY,
    auth: AuthConfig | None = None,
    max_retries: int = 0,
    **settings_overrides: Any,
) -> MCPServer[Any]:
    """Build a real server whose Staan client talks to ``fake``.

    ``settings_overrides`` are extra :class:`Settings` fields, e.g. ``max_content_chars=500``.
    """
    settings = Settings(api_key=api_key, max_retries=max_retries, **settings_overrides)
    return create_server(settings, auth=auth, client_factory=fake.client_factory())


def text_of(result: CallToolResult) -> str:
    """Return the text of the single text block of a tool result."""
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def call(
    fake: FakeStaan, tool: str, arguments: dict[str, Any], **server_options: Any
) -> CallToolResult:
    """Call ``tool`` on a fresh in-memory server wired to ``fake``."""
    async with Client(make_server(fake, **server_options)) as client:
        return await client.call_tool(tool, arguments)


def assert_tool_error(result: CallToolResult, expected: str) -> None:
    """Assert that ``result`` is an error carrying ``expected`` and no structured content."""
    assert result.is_error is True
    assert result.structured_content is None
    assert expected in text_of(result)
