"""The tool contract advertised to MCP clients: metadata, input schemas and fixed messages.

Complements ``test_server_tools.py`` (behaviour) by pinning what a client sees in ``tools/list``
and the exact wording of the errors and warnings the server produces.
"""
# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from mcp import Client
from staan_fakes import (
    BODY_403_DISABLED,
    FakeStaan,
    assert_tool_error,
    call,
    error_reply,
    json_reply,
    make_server,
    search_response,
    text_of,
)

from staan_mcp import contract
from staan_mcp import server as server_module
from staan_mcp.errors import StaanAPIError, StaanAuthenticationError, StaanMCPError

if TYPE_CHECKING:
    from mcp.types import Tool

pytestmark = pytest.mark.anyio


@pytest.fixture
async def tools() -> dict[str, Tool]:
    """The server's tools by name, as a client sees them."""
    async with Client(make_server(FakeStaan())) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


def wanted(tool: Tool, *names: str) -> dict[str, Any]:
    """The advertised ``default`` of each named input property (a missing one raises)."""
    properties = tool.input_schema["properties"]
    return {name: properties[name]["default"] for name in names}


async def test_tool_titles_and_descriptions(tools: dict[str, Tool]) -> None:
    assert tools["web_search"].title == "Staan web search"
    assert tools["web_search"].description == contract.WEB_SEARCH_DESCRIPTION
    assert tools["answer"].title == "Staan answer"
    assert tools["answer"].description == contract.ANSWER_DESCRIPTION


async def test_web_search_schema_defaults(tools: dict[str, Tool]) -> None:
    tool = tools["web_search"]

    assert tool.input_schema["required"] == ["query"]
    assert wanted(tool, "market", "full_content", "offset", "simple_response") == {
        "market": "en-gb",
        "full_content": "none",
        "offset": 0,
        "simple_response": True,
    }


async def test_answer_schema_defaults(tools: dict[str, Tool]) -> None:
    tool = tools["answer"]

    assert tool.input_schema["required"] == ["query"]
    assert wanted(tool, "language", "mode", "sources_mode", "simple_response") == {
        "language": "en",
        "mode": "short",
        "sources_mode": "search",
        "simple_response": True,
    }


async def test_web_search_schema_limits(tools: dict[str, Tool]) -> None:
    properties = tools["web_search"].input_schema["properties"]

    assert properties["query"]["minLength"] == 1
    assert properties["query"]["maxLength"] == 400
    assert properties["full_content"]["enum"] == ["none", "markdown", "html"]
    assert properties["offset"]["enum"] == [0, 10, 20, 30]


async def test_answer_schema_limits(tools: dict[str, Tool]) -> None:
    properties = tools["answer"].input_schema["properties"]

    assert properties["query"]["minLength"] == 1
    assert properties["query"]["maxLength"] == 2000
    assert properties["language"]["enum"] == ["en", "fr"]
    assert properties["mode"]["enum"] == ["short", "long"]
    assert properties["sources_mode"]["enum"] == ["search", "context"]


async def test_results_are_plain_text_blocks() -> None:
    fake = FakeStaan()
    fake.search = json_reply(search_response("https://a.example"))

    result = await call(fake, "web_search", {"query": "python", "full_content": "none"})

    assert [block.type for block in result.content] == ["text"]


async def test_domain_lists_cannot_be_combined() -> None:
    result = await call(
        FakeStaan(),
        "web_search",
        {"query": "python", "include_domains": ["a.com"], "exclude_domains": ["b.com"]},
    )

    assert result.is_error is True
    assert text_of(result) == (
        "Error executing tool web_search: Use include_domains or exclude_domains, not both."
    )


async def test_an_answer_without_text_is_reported_word_for_word() -> None:
    fake = FakeStaan()
    fake.answer = json_reply({"answer": None})

    result = await call(fake, "answer", {"query": "why"})

    assert result.is_error is True
    assert text_of(result) == (
        "Error executing tool answer: Staan returned a response without an answer text."
    )


async def test_partial_failure_warning_names_the_failed_markets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeStaan()
    fake.by_market = {
        "fr-fr": json_reply(search_response("https://fr.example")),
        "de-de": error_reply(500),
        "en-us": error_reply(500),
    }

    with caplog.at_level(logging.WARNING, logger="staan_mcp.server"):
        await call(fake, "web_search", {"query": "python", "market": ["fr-fr", "de-de", "en-us"]})

    messages = [r.getMessage() for r in caplog.records if r.name == "staan_mcp.server"]
    assert messages == ["web_search: 2 of 3 markets failed: de-de, en-us"]


async def test_markets_cancelled_by_a_fatal_error_report_an_incomplete_search() -> None:
    async def search(market: str) -> dict[str, Any]:
        if market == "fr-fr":
            msg = "bad key"
            raise StaanAuthenticationError(msg, endpoint="search", status_code=401)
        await anyio.sleep_forever()
        return {}  # pragma: no cover

    with anyio.fail_after(10):
        outcomes = await server_module._fetch_markets(search, ["fr-fr", "de-de", "en-us"])

    assert isinstance(outcomes[0], StaanAuthenticationError)
    for outcome in outcomes[1:]:
        assert isinstance(outcome, StaanAPIError)
        assert str(outcome) == "The search did not complete."
    assert all(isinstance(outcome, StaanMCPError) for outcome in outcomes)


async def test_disabled_key_in_one_market_is_reported_as_such() -> None:
    fake = FakeStaan()
    fake.by_market = {"fr-fr": error_reply(403, BODY_403_DISABLED)}

    result = await call(fake, "web_search", {"query": "python", "market": ["fr-fr", "de-de"]})

    assert_tool_error(result, "This Staan API key is disabled")
