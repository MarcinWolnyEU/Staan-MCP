"""End-to-end tests of the MCP tools through an in-memory ``mcp.Client``.

The server is the real one; only the Staan API is faked (``httpx2.MockTransport``). Tool calls
therefore exercise argument validation, the Staan client, error mapping and output shaping.
"""
# pylint: disable=protected-access

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import anyio
import httpx2
import pytest
from mcp import Client
from staan_fakes import (
    API_KEY,
    BODY_400,
    BODY_401,
    BODY_403_DISABLED,
    BODY_403_FORBIDDEN,
    FakeStaan,
    assert_tool_error,
    call,
    error_reply,
    json_reply,
    make_server,
    search_response,
    text_of,
)

from staan_mcp import __version__
from staan_mcp.client import StaanClient
from staan_mcp.config import AuthConfig, Settings
from staan_mcp.contract import SERVER_NAME
from staan_mcp.errors import StaanTransportError
from staan_mcp.markets import MARKETS
from staan_mcp.server import create_server

if TYPE_CHECKING:
    from mcp.types import CallToolResult, Tool

pytestmark = pytest.mark.anyio

MESSAGE_500 = "Staan is temporarily unavailable (HTTP 500). Try again later."


async def search(fake: FakeStaan, **arguments: Any) -> CallToolResult:
    """Call ``web_search`` with ``query='python'`` plus ``arguments``."""
    return await call(fake, "web_search", {"query": "python", **arguments})


async def list_tools(fake: FakeStaan | None = None) -> dict[str, Tool]:
    """Return the server's tools by name."""
    async with Client(make_server(fake or FakeStaan())) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


# --------------------------------------------------------------------------------------
# Tool listing and schemas
# --------------------------------------------------------------------------------------


async def test_server_exposes_exactly_the_two_tools() -> None:
    assert sorted(await list_tools()) == ["answer", "web_search"]


async def test_server_identifies_itself_with_name_version_and_instructions() -> None:
    async with Client(make_server(FakeStaan())) as client:
        assert client.server_info is not None
        assert client.server_info.name == SERVER_NAME == "staan"
        assert client.server_info.version == __version__
        assert client.instructions
        assert "web_search" in client.instructions
        assert "answer" in client.instructions


async def test_web_search_input_schema_has_the_documented_parameters_and_defaults() -> None:
    schema = (await list_tools())["web_search"].input_schema
    props = schema["properties"]

    assert set(props) == {
        "query",
        "market",
        "full_content",
        "offset",
        "include_domains",
        "exclude_domains",
        "simple_response",
    }
    assert schema["required"] == ["query"]
    assert props["query"]["minLength"] == 1
    assert props["query"]["maxLength"] == 400
    assert props["market"]["default"] == "en-gb"
    assert props["market"]["anyOf"] == [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ]
    assert props["full_content"]["default"] == "none"
    assert props["full_content"]["enum"] == ["none", "markdown", "html"]
    assert props["offset"]["default"] == 0
    assert props["offset"]["enum"] == [0, 10, 20, 30]
    assert props["include_domains"]["default"] is None
    assert props["exclude_domains"]["default"] is None
    assert props["simple_response"]["default"] is True
    assert props["simple_response"]["type"] == "boolean"


async def test_web_search_hides_parameters_the_server_fixes() -> None:
    props = (await list_tools())["web_search"].input_schema["properties"]

    for hidden in ("count", "min_score", "max_snippets", "extra_snippets", "stream", "history"):
        assert hidden not in props
    assert "ctx" not in props


async def test_web_search_descriptions_guide_the_model() -> None:
    tool = (await list_tools())["web_search"]
    props = tool.input_schema["properties"]
    market_help = props["market"]["description"]

    assert tool.description is not None
    assert all(market in market_help for market in MARKETS)  # the enum cannot list them
    assert "*" in market_help  # wildcards exist
    for text in (tool.description, *(prop["description"] for prop in props.values())):
        assert "faster" not in text  # full_content='none' shrinks the response, nothing else
        assert "cheaper" not in text
    for name, prop in props.items():
        assert prop.get("description"), f"{name} has no description"


async def test_answer_input_schema_has_the_documented_parameters_and_defaults() -> None:
    schema = (await list_tools())["answer"].input_schema
    props = schema["properties"]

    assert set(props) == {
        "query",
        "filter",
        "language",
        "mode",
        "sources",
        "sources_mode",
        "simple_response",
    }
    assert schema["required"] == ["query"]
    assert props["query"]["minLength"] == 1
    assert props["query"]["maxLength"] == 2000
    assert props["filter"]["default"] is None
    assert props["language"]["default"] == "en"
    assert props["language"]["enum"] == ["en", "fr"]
    assert props["mode"]["default"] == "short"
    assert props["mode"]["enum"] == ["short", "long"]
    assert props["sources"]["default"] is None
    assert props["sources"]["anyOf"][0]["maxItems"] == 10
    assert props["sources_mode"]["default"] == "search"
    assert props["sources_mode"]["enum"] == ["search", "context"]
    assert props["simple_response"]["default"] is True
    for name, prop in props.items():
        assert prop.get("description"), f"{name} has no description"


async def test_answer_source_schema_limits_title_and_content() -> None:
    schema = (await list_tools())["answer"].input_schema
    source = schema["$defs"]["AnswerSource"]["properties"]

    assert set(source) == {"url", "title", "content"}
    assert source["title"]["anyOf"][0]["maxLength"] == 500
    assert source["content"]["anyOf"][0]["maxLength"] == 50_000
    assert "description" not in schema["$defs"]["AnswerSource"] or (
        "Attributes" not in schema["$defs"]["AnswerSource"]["description"]
    )


async def test_answer_hides_parameters_the_server_fixes() -> None:
    props = (await list_tools())["answer"].input_schema["properties"]

    for hidden in ("stream", "history", "markdown", "related_queries", "query_rewrite", "ctx"):
        assert hidden not in props


async def test_both_tools_are_read_only_open_world_and_not_destructive() -> None:
    tools = await list_tools()

    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True
        assert tool.annotations.destructive_hint is False
    assert tools["web_search"].annotations is not None
    assert tools["web_search"].annotations.idempotent_hint is True
    assert tools["answer"].annotations is not None
    assert tools["answer"].annotations.idempotent_hint is False


async def test_output_schemas_match_the_wire_shapes() -> None:
    tools = await list_tools()

    assert tools["web_search"].output_schema is None
    answer_schema = tools["answer"].output_schema
    assert answer_schema is not None
    assert answer_schema["type"] == "object"
    assert answer_schema["required"] == ["answer"]
    assert answer_schema["properties"]["answer"]["type"] == "string"
    assert answer_schema["additionalProperties"] is True


# --------------------------------------------------------------------------------------
# web_search: single market
# --------------------------------------------------------------------------------------


async def test_web_search_simple_response_for_one_market(fake: FakeStaan) -> None:
    fake.search = json_reply(
        {
            "search_id": "s1",
            "query": {"q": "python"},
            "web": {
                "results": [
                    {
                        "title": "Python",
                        "url": "https://python.org",
                        "snippet": "The language.",
                        "display_url": "python.org",
                        "extra_snippets": [{"chunk": "More text.", "score": 0.9}],
                        "full_content": {"text": "# Python", "format": "markdown", "length": 8},
                    },
                    {
                        "title": "Docs",
                        "url": "https://docs.python.org",
                        "snippet": "Documentation.",
                        "full_content": {"text": "", "format": "markdown", "length": 0},
                    },
                ]
            },
        }
    )

    result = await search(fake)

    assert result.is_error is False
    assert result.structured_content is None
    assert json.loads(text_of(result)) == [
        {
            "title": "Python",
            "url": "https://python.org",
            "snippet": "The language. (...) More text.",
            "full_content": "# Python",
        },
        {"title": "Docs", "url": "https://docs.python.org", "snippet": "Documentation."},
    ]


async def test_web_search_text_is_a_compact_json_array(fake: FakeStaan) -> None:
    fake.search = json_reply(search_response("https://a.example"))

    text = text_of(await search(fake))

    assert text == json.dumps(
        [
            {
                "title": "Title https://a.example",
                "url": "https://a.example",
                "snippet": "Snippet https://a.example",
            }
        ]
    )


async def test_web_search_keeps_non_ascii_text_unescaped(fake: FakeStaan) -> None:
    fake.search = json_reply(
        {
            "web": {
                "results": [{"title": "Café été", "url": "https://a.example", "snippet": "naïve"}]
            }
        }
    )

    text = text_of(await search(fake))

    assert "Café été" in text
    assert "\\u" not in text


async def test_web_search_sends_the_default_request(fake: FakeStaan) -> None:
    await search(fake)

    request = fake.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.staan.ai/v2/search/web"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert fake.body == {
        "q": "python",
        "market": "en-gb",
        "count": 10,
        "offset": 0,
        "extra_snippets": True,
    }


@pytest.mark.parametrize("fmt", ["markdown", "html"])
async def test_web_search_forwards_the_full_content_format(fake: FakeStaan, fmt: str) -> None:
    await search(fake, full_content=fmt)

    assert fake.body["full_content"] == fmt


async def test_web_search_full_content_none_is_not_sent(fake: FakeStaan) -> None:
    result = await search(fake, full_content="none")

    assert result.is_error is False
    assert "full_content" not in fake.body


@pytest.mark.parametrize("offset", [0, 10, 20, 30])
async def test_web_search_forwards_the_offset(fake: FakeStaan, offset: int) -> None:
    await search(fake, offset=offset)

    assert fake.body["offset"] == offset


async def test_web_search_lowercases_and_strips_the_market(fake: FakeStaan) -> None:
    await search(fake, market=" EN-US ")

    assert fake.body["market"] == "en-us"


async def test_web_search_accepts_a_one_item_market_list(fake: FakeStaan) -> None:
    await search(fake, market=["de-de"])

    assert fake.markets() == ["de-de"]


async def test_web_search_normalises_and_forwards_include_domains(fake: FakeStaan) -> None:
    await search(fake, include_domains=["https://www.Wikipedia.org/", "docs.python.org"])

    assert fake.body["include_domains"] == ["wikipedia.org", "docs.python.org"]
    assert "exclude_domains" not in fake.body


async def test_web_search_normalises_and_forwards_exclude_domains(fake: FakeStaan) -> None:
    await search(fake, exclude_domains=["http://www.Pinterest.com/pin/1?x=y"])

    assert fake.body["exclude_domains"] == ["pinterest.com"]
    assert "include_domains" not in fake.body


@pytest.mark.parametrize("empty", [None, []])
async def test_web_search_omits_empty_domain_lists(
    fake: FakeStaan, empty: list[str] | None
) -> None:
    await search(fake, include_domains=empty, exclude_domains=empty)

    assert "include_domains" not in fake.body
    assert "exclude_domains" not in fake.body


async def test_web_search_tolerates_a_malformed_response_body(fake: FakeStaan) -> None:
    fake.search = json_reply({"web": {"results": "nope"}})

    assert text_of(await search(fake)) == "[]"


@pytest.mark.parametrize("payload", [{}, {"web": None}, {"web": []}, {"web": {}}])
async def test_web_search_returns_an_empty_array_without_results(
    fake: FakeStaan, payload: dict[str, Any]
) -> None:
    fake.search = json_reply(payload)

    assert text_of(await search(fake)) == "[]"


async def test_web_search_skips_results_that_are_not_objects(fake: FakeStaan) -> None:
    fake.search = json_reply(
        {
            "web": {
                "results": ["junk", 3, {"title": "T", "url": "https://a.example", "snippet": "s"}]
            }
        }
    )

    assert json.loads(text_of(await search(fake))) == [
        {"title": "T", "url": "https://a.example", "snippet": "s"}
    ]


# --------------------------------------------------------------------------------------
# web_search: several markets
# --------------------------------------------------------------------------------------


async def test_web_search_interleaves_markets_by_rank_and_dedupes(fake: FakeStaan) -> None:
    fake.by_market = {
        "fr-fr": json_reply(search_response("https://u1", "https://u2", "https://u3")),
        "de-de": json_reply(search_response("https://u2", "https://u4")),
    }

    result = await search(fake, market=["fr-fr", "de-de"])

    urls = [item["url"] for item in json.loads(text_of(result))]
    assert urls == ["https://u1", "https://u2", "https://u4", "https://u3"]
    assert fake.markets() == ["de-de", "fr-fr"]


async def test_web_search_wildcard_expands_to_every_matching_market(fake: FakeStaan) -> None:
    await search(fake, market="en-*")

    assert fake.markets() == sorted(m for m in MARKETS if m.startswith("en-"))
    assert len(fake.requests) == 10


async def test_web_search_wildcard_suffix_pattern(fake: FakeStaan) -> None:
    await search(fake, market="*-fr")

    assert fake.markets() == ["en-fr", "fr-fr"]


async def test_web_search_star_means_all_twelve_markets(fake: FakeStaan) -> None:
    await search(fake, market="*")

    assert fake.markets() == sorted(MARKETS)


async def test_web_search_mixes_wildcards_and_exact_markets_without_duplicates(
    fake: FakeStaan,
) -> None:
    await search(fake, market=["fr-fr", "*-fr", "FR-FR"])

    assert fake.markets() == ["en-fr", "fr-fr"]


async def test_web_search_sends_the_same_options_to_every_market(fake: FakeStaan) -> None:
    await search(fake, market=["fr-fr", "de-de"], offset=20, full_content="html")

    for body in fake.bodies():
        assert body["offset"] == 20
        assert body["full_content"] == "html"
        assert body["count"] == 10
        assert body["q"] == "python"


async def test_web_search_full_response_is_one_raw_entry_per_market_in_order(
    fake: FakeStaan,
) -> None:
    fr = search_response("https://fr.example")
    de = search_response("https://de.example")
    fake.by_market = {"fr-fr": json_reply(fr), "de-de": json_reply(de)}

    result = await search(fake, market=["de-de", "fr-fr"], simple_response=False)

    assert result.is_error is False
    assert json.loads(text_of(result)) == [de, fr]


async def test_web_search_full_response_for_one_market_is_still_an_array(fake: FakeStaan) -> None:
    raw = search_response("https://a.example")
    fake.search = json_reply(raw)

    result = await search(fake, simple_response=False)

    assert json.loads(text_of(result)) == [raw]


async def test_web_search_full_response_reports_failed_markets_inline(fake: FakeStaan) -> None:
    fr = search_response("https://fr.example")
    fake.by_market = {"fr-fr": json_reply(fr), "de-de": error_reply(500)}

    result = await search(fake, market=["fr-fr", "de-de"], simple_response=False)

    assert result.is_error is False
    assert json.loads(text_of(result)) == [fr, {"market": "de-de", "error": MESSAGE_500}]


async def test_web_search_simple_response_survives_a_partial_failure(
    fake: FakeStaan, caplog: pytest.LogCaptureFixture
) -> None:
    fake.by_market = {
        "fr-fr": json_reply(search_response("https://fr.example")),
        "de-de": error_reply(500),
    }

    with caplog.at_level(logging.WARNING, logger="staan_mcp.server"):
        result = await search(fake, market=["fr-fr", "de-de"])

    assert result.is_error is False
    assert [item["url"] for item in json.loads(text_of(result))] == ["https://fr.example"]
    warnings = [r for r in caplog.records if r.name == "staan_mcp.server"]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "1 of 2" in warnings[0].getMessage()
    assert "de-de" in warnings[0].getMessage()
    assert "fr-fr" not in warnings[0].getMessage()


async def test_web_search_fails_when_every_market_fails(fake: FakeStaan) -> None:
    fake.search = error_reply(500)

    result = await search(fake, market=["fr-fr", "de-de"])

    assert_tool_error(result, f"fr-fr: {MESSAGE_500}; de-de: {MESSAGE_500}")


async def test_web_search_single_market_failure_is_a_tool_error(fake: FakeStaan) -> None:
    fake.search = error_reply(400, BODY_400)

    result = await search(fake)

    assert_tool_error(result, "en-gb: Staan rejected the request: market must be a valid market")


async def test_web_search_retries_transient_failures_before_reporting(fake: FakeStaan) -> None:
    fake.search = error_reply(503)

    async with Client(make_server(fake, max_retries=2)) as client:
        result = await client.call_tool("web_search", {"query": "python"})

    assert result.is_error is True
    assert len(fake.requests) == 3


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, BODY_401, "Staan authentication failed (HTTP 401)"),
        (403, BODY_403_DISABLED, "This Staan API key is disabled"),
        (403, BODY_403_FORBIDDEN, "Staan refused the request (HTTP 403 Forbidden)"),
    ],
)
async def test_web_search_fatal_errors_are_reported_without_a_market_prefix(
    fake: FakeStaan, status: int, body: bytes, expected: str
) -> None:
    fake.search = error_reply(status, body)

    result = await search(fake, market="fr-fr")

    assert_tool_error(result, expected)
    assert "fr-fr:" not in text_of(result)


async def test_web_search_fatal_error_hides_successful_markets(fake: FakeStaan) -> None:
    fake.by_market = {
        "fr-fr": json_reply(search_response("https://fr.example")),
        "de-de": error_reply(401, BODY_401),
    }

    result = await search(fake, market=["fr-fr", "de-de"])

    assert_tool_error(result, "Staan authentication failed")
    assert "fr.example" not in text_of(result)


async def test_web_search_fatal_error_cancels_the_markets_still_running(fake: FakeStaan) -> None:
    async def hang(_request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep_forever()
        raise AssertionError  # pragma: no cover

    fake.search = hang
    fake.by_market = {"fr-fr": error_reply(403, BODY_403_DISABLED)}

    with anyio.fail_after(10):
        result = await search(fake, market=["en-gb", "de-de", "fr-fr"])

    assert_tool_error(result, "This Staan API key is disabled")


async def test_web_search_non_fatal_error_does_not_cancel_other_markets(fake: FakeStaan) -> None:
    async def slow_ok(_request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(0.05)
        return json_reply(search_response("https://slow.example"))

    fake.search = slow_ok
    fake.by_market = {"fr-fr": error_reply(500)}

    result = await search(fake, market=["en-gb", "fr-fr"])

    assert result.is_error is False
    assert [item["url"] for item in json.loads(text_of(result))] == ["https://slow.example"]


async def test_web_search_reports_a_transport_failure_as_a_market_error(fake: FakeStaan) -> None:
    fake.search = httpx2.ConnectError("boom")

    result = await search(fake)

    assert_tool_error(result, "en-gb: Could not complete the request to Staan (ConnectError).")
    assert "boom" not in text_of(result)


# --------------------------------------------------------------------------------------
# web_search input validation
# --------------------------------------------------------------------------------------


async def test_web_search_rejects_include_and_exclude_together(fake: FakeStaan) -> None:
    result = await search(fake, include_domains=["a.com"], exclude_domains=["b.com"])

    assert_tool_error(result, "Use include_domains or exclude_domains, not both.")
    assert fake.requests == []


@pytest.mark.parametrize("market", ["es-es", "en", "en_GB", "", "  ", [], [""], ["en-gb", "xx-yy"]])
async def test_web_search_rejects_invalid_markets_with_the_valid_list(
    fake: FakeStaan, market: str | list[str]
) -> None:
    result = await search(fake, market=market)

    assert result.is_error is True
    for valid in MARKETS:
        assert valid in text_of(result)
    assert fake.requests == []


async def test_web_search_wildcard_matching_nothing_is_an_error(fake: FakeStaan) -> None:
    result = await search(fake, market="es-*")

    assert_tool_error(result, "es-*")
    assert fake.requests == []


@pytest.mark.parametrize("domain", ["not a domain", "localhost", "-bad-.com", "a..com"])
async def test_web_search_rejects_invalid_domains(fake: FakeStaan, domain: str) -> None:
    result = await search(fake, include_domains=[domain])

    assert result.is_error is True
    assert fake.requests == []


async def test_web_search_rejects_more_than_ten_domains(fake: FakeStaan) -> None:
    domains = [f"site{i}.com" for i in range(11)]

    result = await search(fake, exclude_domains=domains)

    assert_tool_error(result, "10")
    assert fake.requests == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": "x" * 401},
        {"offset": 5},
        {"offset": 40},
        {"full_content": "text"},
        {"full_content": "MARKDOWN"},
        {"simple_response": "maybe"},
        {"include_domains": "a.com"},
        {"market": 5},
    ],
)
async def test_web_search_schema_violations_never_reach_the_upstream(
    fake: FakeStaan, arguments: dict[str, Any]
) -> None:
    result = await call(fake, "web_search", {"query": "python", **arguments})

    assert result.is_error is True
    assert fake.requests == []


async def test_web_search_accepts_the_longest_allowed_query(fake: FakeStaan) -> None:
    result = await search(fake, query="q" * 400)

    assert result.is_error is False
    assert fake.body["q"] == "q" * 400


async def test_web_search_requires_a_query(fake: FakeStaan) -> None:
    async with Client(make_server(fake)) as client:
        result = await client.call_tool("web_search", {})

    assert result.is_error is True
    assert fake.requests == []


# --------------------------------------------------------------------------------------
# Errors that are not about the request
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"), [("web_search", {"query": "x"}), ("answer", {"query": "x"})]
)
async def test_missing_api_key_is_a_clear_tool_error_without_network_io(
    fake: FakeStaan, tool: str, arguments: dict[str, Any]
) -> None:
    result = await call(fake, tool, arguments, api_key=None)

    assert_tool_error(result, "STAAN_API_KEY is not set")
    assert fake.requests == []


async def test_missing_api_key_error_has_no_market_prefix(fake: FakeStaan) -> None:
    arguments = {"query": "x", "market": ["en-gb", "fr-fr"]}

    result = await call(fake, "web_search", arguments, api_key=None)

    assert result.is_error is True
    assert text_of(result).startswith("Error executing tool web_search: STAAN_API_KEY is not set")
    assert "en-gb:" not in text_of(result)
    assert fake.requests == []


async def test_missing_api_key_does_not_stop_tool_listing() -> None:
    async with Client(make_server(FakeStaan(), api_key=None)) as client:
        assert len((await client.list_tools()).tools) == 2


async def test_api_key_never_appears_in_tool_output(fake: FakeStaan) -> None:
    fake.search = error_reply(401, BODY_401)

    result = await search(fake)

    assert API_KEY not in text_of(result)


async def test_unexpected_exceptions_are_reported_generically() -> None:
    class ExplodingClient(StaanClient):
        """A client whose search fails with an unexpected exception."""

        async def search_web(self, **_kwargs: Any) -> dict[str, Any]:
            msg = "secret internal detail"
            raise RuntimeError(msg)

    server = create_server(Settings(api_key=API_KEY), client_factory=ExplodingClient)
    async with Client(server) as client:
        result = await client.call_tool("web_search", {"query": "x"})

    assert result.is_error is True
    assert "secret internal detail" not in text_of(result)


async def test_a_staan_error_raised_by_the_client_becomes_a_tool_error() -> None:
    class FailingClient(StaanClient):
        """A client whose answer fails with a Staan error."""

        async def answer(self, **_kwargs: Any) -> dict[str, Any]:
            msg = "custom failure"
            raise StaanTransportError(msg, endpoint="answer")

    server = create_server(Settings(api_key=API_KEY), client_factory=FailingClient)
    async with Client(server) as client:
        result = await client.call_tool("answer", {"query": "x"})

    assert_tool_error(result, "custom failure")


async def test_one_staan_client_serves_the_whole_server_lifetime() -> None:
    created: list[StaanClient] = []
    fake = FakeStaan()

    def factory(settings: Settings) -> StaanClient:
        client = fake.client_factory()(settings)
        created.append(client)
        return client

    server = create_server(Settings(api_key=API_KEY), client_factory=factory)
    async with Client(server) as client:
        await client.call_tool("web_search", {"query": "a"})
        await client.call_tool("web_search", {"query": "b"})
        await client.call_tool("answer", {"query": "c"})

    assert len(created) == 1
    assert len(fake.requests) == 3


async def test_the_client_is_closed_when_the_server_stops() -> None:
    created: list[StaanClient] = []
    fake = FakeStaan()

    def factory(settings: Settings) -> StaanClient:
        client = fake.client_factory()(settings)
        created.append(client)
        return client

    server = create_server(Settings(api_key=API_KEY), client_factory=factory)
    async with Client(server) as client:
        await client.list_tools()
        assert created[0]._http.is_closed is False

    assert created[0]._http.is_closed is True


async def test_default_client_factory_builds_a_real_staan_client() -> None:
    server = create_server(Settings(api_key=None))

    async with Client(server) as client:
        result = await client.call_tool("web_search", {"query": "x"})

    assert_tool_error(result, "STAAN_API_KEY is not set")


# --------------------------------------------------------------------------------------
# Server construction
# --------------------------------------------------------------------------------------


async def test_create_server_without_auth_has_no_token_verifier() -> None:
    server = create_server(Settings(api_key=API_KEY))

    assert server.settings.auth is None


def test_create_server_with_auth_uses_the_configured_gate() -> None:
    config = AuthConfig(
        issuer_url="https://auth.example.com",
        resource_url="https://mcp.example.com/mcp",
        required_scopes=("staan",),
        jwks_url="https://auth.example.com/jwks",
    )

    server = create_server(Settings(api_key=API_KEY), auth=config)

    auth = server.settings.auth
    assert auth is not None
    assert str(auth.issuer_url) == "https://auth.example.com/"
    assert str(auth.resource_server_url) == "https://mcp.example.com/mcp"
    assert auth.required_scopes == ["staan"]
    assert auth.validate_token_resource is True


# --------------------------------------------------------------------------------------
# Output size: STAAN_MCP_MAX_CONTENT_CHARS
# --------------------------------------------------------------------------------------


def page_response(*texts: str) -> dict[str, Any]:
    """A search response with one result per text, each with extra snippets and full content."""
    results = [
        {
            "title": f"T{index}",
            "url": f"https://page{index}.example",
            "snippet": "Snippet.",
            "extra_snippets": [{"chunk": "Chunk text.", "score": 0.5}],
            "full_content": {"text": text, "format": "markdown", "length": len(text)},
        }
        for index, text in enumerate(texts)
    ]
    return {"search_id": "s", "web": {"results": results}}


LONG_PAGE = "A" * 200 + "\n\n" + "B " * 400
CUT_PAGE = "A" * 200 + f"\n\n[... truncated: 200 of {len(LONG_PAGE)} characters shown ...]"


async def test_content_is_not_truncated_by_default(fake: FakeStaan) -> None:
    fake.search = json_reply(page_response(LONG_PAGE * 20))

    result = await search(fake)

    (item,) = json.loads(text_of(result))
    assert item["full_content"] == LONG_PAGE * 20
    assert "truncated" not in text_of(result)


async def test_content_limit_cuts_each_page_at_a_paragraph_with_a_visible_marker(
    fake: FakeStaan,
) -> None:
    fake.search = json_reply(page_response(LONG_PAGE, "Short page."))

    result = await call_with(fake, max_content_chars=300)

    long_item, short_item = json.loads(text_of(result))
    assert long_item["full_content"] == CUT_PAGE
    assert short_item["full_content"] == "Short page."
    # Snippets and extra snippets are never touched.
    assert long_item["snippet"] == "Snippet. (...) Chunk text."


async def test_content_limit_also_applies_to_the_raw_response(fake: FakeStaan) -> None:
    fake.by_market = {
        "en-gb": json_reply(page_response(LONG_PAGE)),
        "de-de": json_reply(page_response("Short page.")),
        "fr-fr": error_reply(500),
    }

    result = await call_with(
        fake, market=["en-gb", "de-de", "fr-fr"], max_content_chars=300, simple_response=False
    )

    gb, de, fr = json.loads(text_of(result))
    text = gb["web"]["results"][0]["full_content"]["text"]
    assert text == CUT_PAGE
    assert gb["web"]["results"][0]["full_content"]["length"] == len(LONG_PAGE)  # upstream value
    assert gb["web"]["results"][0]["extra_snippets"] == [{"chunk": "Chunk text.", "score": 0.5}]
    assert de["web"]["results"][0]["full_content"]["text"] == "Short page."
    assert fr == {"market": "fr-fr", "error": MESSAGE_500}


async def test_content_limit_ignores_results_without_text_content(fake: FakeStaan) -> None:
    fake.search = json_reply(
        {
            "web": {
                "results": [
                    {"url": "https://a.example", "full_content": {"text": 5}},
                    {"url": "https://b.example", "full_content": "plain"},
                    {"url": "https://c.example"},
                ]
            }
        }
    )

    result = await call_with(fake, max_content_chars=300, simple_response=False)

    (market,) = json.loads(text_of(result))
    assert market["web"]["results"][0]["full_content"] == {"text": 5}
    assert market["web"]["results"][1]["full_content"] == "plain"


async def call_with(
    fake: FakeStaan, *, max_content_chars: int = 0, **arguments: Any
) -> CallToolResult:
    """Call ``web_search`` on a server limited to ``max_content_chars`` per page."""
    async with Client(make_server(fake, max_content_chars=max_content_chars)) as client:
        return await client.call_tool("web_search", {"query": "python", **arguments})


# --------------------------------------------------------------------------------------
# Configuration mistakes reach the model as a clear tool error
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("market", ["en-gb", ["en-gb", "en-us"], "en-*"])
async def test_an_unusable_base_url_is_one_clear_tool_error_for_any_number_of_markets(
    fake: FakeStaan, market: str | list[str]
) -> None:
    result = await call(
        fake,
        "web_search",
        {"query": "python", "market": market},
        base_url="https://api.staan.ai:abc/v2",
    )

    assert_tool_error(result, "The request to Staan could not be built")
    assert "ExceptionGroup" not in text_of(result)
    assert fake.requests == []


async def test_an_api_key_that_cannot_be_sent_is_a_clear_tool_error(fake: FakeStaan) -> None:
    result = await call(
        fake,
        "web_search",
        {"query": "python", "market": ["en-gb", "en-us"]},
        api_key="sk-“pasted”",
    )

    assert_tool_error(result, "The request to Staan could not be built")
    assert "pasted" not in text_of(result)


# --------------------------------------------------------------------------------------
# Server wording
# --------------------------------------------------------------------------------------


async def test_instructions_do_not_promise_that_none_is_cheaper_or_faster() -> None:
    async with Client(make_server(FakeStaan())) as client:
        instructions = client.instructions

    assert instructions is not None
    assert "faster" not in instructions
    assert "cheaper" not in instructions
