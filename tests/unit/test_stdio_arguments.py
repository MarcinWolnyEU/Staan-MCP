"""The argument checks of ``stdio_server.py``: the same rules as the package's pydantic models."""

from __future__ import annotations

import json
from typing import Any

import pytest
from local_staan import LocalStaan, json_reply
from stdio_helpers import (
    OK_SEARCH,
    SIMPLE_HIT,
    run_tool,
    text_of,
)

# --------------------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------------------

LONG_QUERY = "x" * 401
BAD_ARGUMENTS: list[tuple[str, dict[str, Any], str]] = [
    ("web_search", {}, "Missing required argument 'query'."),
    ("web_search", {"query": ""}, "Invalid argument 'query': must be at least 1 character long."),
    ("web_search", {"query": 5}, "Invalid argument 'query': expected a string."),
    ("web_search", {"query": None}, "Invalid argument 'query': expected a string."),
    (
        "web_search",
        {"query": LONG_QUERY},
        "Invalid argument 'query': must be at most 400 characters long (got 401).",
    ),
    (
        "web_search",
        {"query": "x", "market": 5},
        "Invalid argument 'market': expected a string or a list of strings.",
    ),
    (
        "web_search",
        {"query": "x", "market": None},
        "Invalid argument 'market': expected a string or a list of strings.",
    ),
    (
        "web_search",
        {"query": "x", "market": [1]},
        "Invalid argument 'market': expected a string or a list of strings.",
    ),
    (
        "web_search",
        {"query": "x", "offset": 5},
        "Invalid argument 'offset': must be one of 0, 10, 20, 30.",
    ),
    (
        "web_search",
        {"query": "x", "offset": "10"},
        "Invalid argument 'offset': must be one of 0, 10, 20, 30.",
    ),
    (
        "web_search",
        {"query": "x", "offset": True},
        "Invalid argument 'offset': must be one of 0, 10, 20, 30.",
    ),
    (
        "web_search",
        {"query": "x", "offset": None},
        "Invalid argument 'offset': must be one of 0, 10, 20, 30.",
    ),
    (
        "web_search",
        {"query": "x", "offset": 10.5},
        "Invalid argument 'offset': must be one of 0, 10, 20, 30.",
    ),
    (
        "web_search",
        {"query": "x", "full_content": "pdf"},
        "Invalid argument 'full_content': must be one of 'none', 'markdown', 'html'.",
    ),
    (
        "web_search",
        {"query": "x", "include_domains": "a.com"},
        "Invalid argument 'include_domains': expected a list of strings.",
    ),
    (
        "web_search",
        {"query": "x", "exclude_domains": [1]},
        "Invalid argument 'exclude_domains': expected a list of strings.",
    ),
    (
        "web_search",
        {"query": "x", "simple_response": None},
        "Invalid argument 'simple_response': expected true or false.",
    ),
    (
        "web_search",
        {"query": "x", "simple_response": "maybe"},
        "Invalid argument 'simple_response': expected true or false.",
    ),
    (
        "web_search",
        {"query": "x", "simple_response": 2},
        "Invalid argument 'simple_response': expected true or false.",
    ),
    (
        "web_search",
        {"query": "x", "include_domains": ["a.com"], "exclude_domains": ["b.com"]},
        "Use include_domains or exclude_domains, not both.",
    ),
    (
        "answer",
        {"query": "x", "language": "de"},
        "Invalid argument 'language': must be one of 'en', 'fr'.",
    ),
    (
        "answer",
        {"query": "x", "mode": "medium"},
        "Invalid argument 'mode': must be one of 'short', 'long'.",
    ),
    ("answer", {"query": "x", "filter": 5}, "Invalid argument 'filter': expected a string."),
    (
        "answer",
        {"query": "x" * 2001},
        "Invalid argument 'query': must be at most 2000 characters long (got 2001).",
    ),
    (
        "answer",
        {"query": "x", "sources": "a"},
        "Invalid argument 'sources': expected a list of objects.",
    ),
    (
        "answer",
        {"query": "x", "sources": [{"url": "https://a.example"}] * 11},
        "Invalid argument 'sources': at most 10 items are allowed, got 11.",
    ),
    (
        "answer",
        {"query": "x", "sources": [1]},
        "Invalid argument 'sources': item 0 must be an object with 'url' and/or 'content'.",
    ),
    (
        "answer",
        {"query": "x", "sources": [{"url": 5}]},
        "Invalid argument 'sources': 'url' of item 0 must be a string.",
    ),
    (
        "answer",
        {"query": "x", "sources": [{"url": "https://a.example", "title": "t" * 501}]},
        "Invalid argument 'title': must be at most 500 characters long (got 501).",
    ),
    (
        "answer",
        {"query": "x", "sources": [{}]},
        "Each source needs a non-empty 'url' or 'content'.",
    ),
    (
        "answer",
        {"query": "x", "sources": [{"url": "http://localhost/x"}]},
        "A source 'url' must point to a public host, not a local or private address.",
    ),
    (
        "answer",
        {"query": "x", "filter": "not a domain"},
        (
            "Invalid domain 'not a domain': it needs at least one dot. "
            "Expected a bare domain such as 'example.com'."
        ),
    ),
]


@pytest.mark.parametrize(("tool", "arguments", "problem"), BAD_ARGUMENTS)
def test_a_bad_argument_is_reported_to_the_model_before_any_request(
    local_staan: LocalStaan, tool: str, arguments: dict[str, Any], problem: str
) -> None:
    result = run_tool(local_staan, tool, arguments)

    assert result == {
        "content": [{"type": "text", "text": f"Error executing tool {tool}: {problem}"}],
        "isError": True,
    }
    assert local_staan.requests == []


def test_an_unknown_market_names_the_valid_ones(local_staan: LocalStaan) -> None:
    result = run_tool(local_staan, "web_search", {"query": "x", "market": "zz"})

    assert result["isError"] is True
    assert text_of(result).startswith("Error executing tool web_search: Unknown market 'zz'. Valid")
    assert local_staan.requests == []


def test_unknown_arguments_are_ignored_like_the_package_does(local_staan: LocalStaan) -> None:
    result = run_tool(local_staan, "web_search", {"query": "x", "bogus": 1})

    assert "isError" not in result
    assert len(local_staan.requests) == 1


@pytest.mark.parametrize(
    ("value", "simple"),
    [
        *[(value, True) for value in (True, "Yes", "on", "1", 1, 1.0)],
        *[(value, False) for value in (False, "NO", "off", "0", 0, 0.0)],
    ],
)
def test_a_boolean_argument_accepts_what_pydantic_accepts(
    local_staan: LocalStaan, value: object, *, simple: bool
) -> None:
    local_staan.search = json_reply(OK_SEARCH)

    result = run_tool(local_staan, "web_search", {"query": "x", "simple_response": value})

    assert json.loads(text_of(result)) == ([SIMPLE_HIT] if simple else [OK_SEARCH])


@pytest.mark.parametrize(
    ("value", "sent"), [(10.0, 10), (30.0, 30), (False, 0), (0.0, 0), (-0.0, 0)]
)
def test_an_integral_float_or_false_is_a_valid_offset(
    local_staan: LocalStaan, value: object, sent: int
) -> None:
    run_tool(local_staan, "web_search", {"query": "x", "offset": value})

    (body,) = local_staan.bodies()
    assert body["offset"] == sent
    assert not isinstance(body["offset"], bool)  # JSON ``0``, never ``false``
