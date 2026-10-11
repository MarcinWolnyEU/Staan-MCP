"""Live tests against the real Staan API.

Skipped unless ``STAAN_API_KEY`` is set and ``--run-live`` is passed (see ``tests/conftest.py``);
they are excluded from coverage and never run in CI. Each test costs one or two billable
requests. Run them with::

    uv run pytest tests/live --run-live --no-cov

The Answer API is invitation-only, so for most keys the answer tests assert the documented
403 mapping; a key that has Answer enabled skips them instead of failing.
"""
# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from mcp import Client
from mcp.types import TextContent

from staan_mcp.client import StaanClient
from staan_mcp.config import Settings
from staan_mcp.errors import StaanForbiddenError
from staan_mcp.server import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from mcp.types import ContentBlock

pytestmark = [pytest.mark.live, pytest.mark.anyio]

ANSWER_NOT_ENABLED = (
    "The Answer API is not enabled for this API key (invitation-only, contact support@staan.ai), "
    "or Staan has temporarily disabled it (HTTP 403 Forbidden). Web search is not affected."
)


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    """An in-memory MCP client on the real server, configured from the environment."""
    server = create_server(Settings.from_env())
    async with Client(server) as mcp_client:
        yield mcp_client


def _text(result_content: Sequence[ContentBlock]) -> str:
    """Join the text blocks of a tool result."""
    return "".join(block.text for block in result_content if isinstance(block, TextContent))


async def test_search_client_returns_documented_shape() -> None:
    async with StaanClient(Settings.from_env()) as staan:
        body = await staan.search_web(query="python asyncio", market="en-gb", full_content=None)

    results = body["web"]["results"]
    assert 0 < len(results) <= 10
    first = results[0]
    assert first["url"].startswith("http")
    assert isinstance(first["title"], str)
    assert isinstance(first["snippet"], str)


async def test_web_search_tool_simple_response(client: Client) -> None:
    result = await client.call_tool(
        "web_search", {"query": "python asyncio", "market": "en-gb", "full_content": "none"}
    )

    assert result.is_error is False
    text = _text(result.content)
    assert "�" not in text, "the payload must be valid UTF-8 end to end"
    items = json.loads(text)
    assert items
    assert all(set(item) >= {"title", "url", "snippet"} for item in items)
    assert all("full_content" not in item for item in items)


async def test_web_search_tool_full_content_and_domain_filter(client: Client) -> None:
    result = await client.call_tool(
        "web_search",
        {
            "query": "python",
            "market": "en-us",
            "full_content": "markdown",
            "include_domains": ["https://www.python.org/"],
        },
    )

    assert result.is_error is False
    items = json.loads(_text(result.content))
    assert items
    assert all("python.org" in item["url"] for item in items)
    assert any(item.get("full_content") for item in items)


async def test_web_search_tool_wildcard_markets_are_deduplicated(client: Client) -> None:
    result = await client.call_tool(
        "web_search", {"query": "weather", "market": ["fr-fr", "en-fr"], "full_content": "none"}
    )

    assert result.is_error is False
    urls = [item["url"] for item in json.loads(_text(result.content))]
    assert urls
    assert len(urls) == len(set(urls))


async def _answer_denial() -> StaanForbiddenError:
    """Call Answer and return the 403 it is expected to raise (skip if the key may use it)."""
    async with StaanClient(Settings.from_env()) as staan:
        try:
            await staan.answer(query="What is the capital of France?")
        except StaanForbiddenError as error:
            return error
    return pytest.skip("this API key has the Answer API enabled")


async def test_answer_client_maps_403_to_forbidden_error() -> None:
    error = await _answer_denial()

    assert error.status_code == 403
    assert error.endpoint == "answer"
    assert str(error) == ANSWER_NOT_ENABLED


async def test_answer_tool_reports_a_clean_tool_error(client: Client) -> None:
    result = await client.call_tool("answer", {"query": "What is the capital of France?"})

    if not result.is_error:
        pytest.skip("this API key has the Answer API enabled")
    assert _text(result.content) == f"Error executing tool answer: {ANSWER_NOT_ENABLED}"
