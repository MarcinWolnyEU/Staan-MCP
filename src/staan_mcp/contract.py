"""What the server tells MCP clients: names, instructions, tool texts and input limits.

Pure data, standard library only. Both implementations of the server (the ``staan_mcp`` package
and the standalone ``stdio_server.py``) import it, so a client sees the same wording whichever
one it launched. :data:`TOOL_LIST` also holds the tools' JSON schemas for the standalone server,
written out by hand; the package generates the same schemas with pydantic and a test compares.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from staan_mcp.markets import DEFAULT_MARKET, MARKETS

__all__ = [
    "ANSWER_DESCRIPTION",
    "ANSWER_FIELD_PARAM",
    "ANSWER_INPUT_SCHEMA",
    "ANSWER_OUTPUT_DESCRIPTION",
    "ANSWER_OUTPUT_SCHEMA",
    "ANSWER_QUERY_PARAM",
    "ANSWER_SIMPLE_PARAM",
    "ANSWER_TITLE",
    "CONTENT_PARAM",
    "EXCLUDE_PARAM",
    "FILTER_PARAM",
    "FULL_CONTENT_CHOICES",
    "FULL_CONTENT_PARAM",
    "INCLUDE_PARAM",
    "INSTRUCTIONS",
    "LANGUAGE_CHOICES",
    "LANGUAGE_PARAM",
    "MARKET_PARAM",
    "MAX_ANSWER_QUERY_LENGTH",
    "MAX_ANSWER_SOURCES",
    "MAX_SEARCH_QUERY_LENGTH",
    "MAX_SOURCE_CONTENT_LENGTH",
    "MAX_SOURCE_TITLE_LENGTH",
    "MAX_SOURCE_URL_LENGTH",
    "MODE_CHOICES",
    "MODE_PARAM",
    "OFFSET_CHOICES",
    "OFFSET_PARAM",
    "SEARCH_QUERY_PARAM",
    "SEARCH_SIMPLE_PARAM",
    "SERVER_NAME",
    "SOURCES_MODE_CHOICES",
    "SOURCES_MODE_PARAM",
    "SOURCES_PARAM",
    "SOURCE_DESCRIPTION",
    "TITLE_PARAM",
    "TOOL_LIST",
    "URL_PARAM",
    "WEB_SEARCH_DESCRIPTION",
    "WEB_SEARCH_INPUT_SCHEMA",
    "WEB_SEARCH_TITLE",
]

SERVER_NAME: Final = "staan"
"""Name the server announces during the MCP handshake."""

MAX_SEARCH_QUERY_LENGTH: Final = 400
MAX_ANSWER_QUERY_LENGTH: Final = 2000
MAX_ANSWER_SOURCES: Final = 10
MAX_SOURCE_URL_LENGTH: Final = 2048
MAX_SOURCE_TITLE_LENGTH: Final = 500
MAX_SOURCE_CONTENT_LENGTH: Final = 50_000

_MARKET_LIST: Final = ", ".join(MARKETS)

INSTRUCTIONS: Final = (
    "web_search finds pages and current facts; answer gives one cited answer to a question. "
    "Calls are billed, and web_search bills one request per market."
)

WEB_SEARCH_TITLE: Final = "Staan web search"
ANSWER_TITLE: Final = "Staan answer"

WEB_SEARCH_DESCRIPTION: Final = """\
Search the internet. \
Returns ranked results as a JSON array of {"title", "url", "snippet", "full_content"?}. \
"""

ANSWER_DESCRIPTION: Final = """\
Answer a question with citations, grounded in web pages or in sources you supply. Use it instead \
of web_search when you want a ready answer created by another LLM.\
"""

SEARCH_QUERY_PARAM: Final = "Search query in natural language."
MARKET_PARAM: Final = (
    f"Language-country market(s) to search: {_MARKET_LIST}. Pass one, a list, or wildcards "
    "('en-*' for all English markets, '*' for all). Several markets are merged by rank and "
    "de-duplicated by URL; each is a separate billable request, so prefer one unless you need "
    "broader coverage."
)
FULL_CONTENT_PARAM: Final = (
    "Page text returned with each result. Default 'none' omits it; 'markdown' adds it and makes "
    "the response very large; 'html' is much larger still."
)
OFFSET_PARAM: Final = (
    "Offset of the first result, for paging: pages hold up to 10 results, at most 40 per query."
)
INCLUDE_PARAM: Final = (
    "Only return results from these sites, e.g. ['wikipedia.org']; subdomains match. At most 10. "
    "Not with exclude_domains."
)
EXCLUDE_PARAM: Final = (
    "Drop results from these sites, e.g. ['pinterest.com']; subdomains too. At most 10. "
    "Not with include_domains."
)
SEARCH_SIMPLE_PARAM: Final = (
    "false returns the raw Staan response, one entry per market, instead of the flat list."
)
ANSWER_QUERY_PARAM: Final = "The question to answer."
FILTER_PARAM: Final = (
    "Restrict the answer to one website, a bare domain such as 'lemonde.fr'. Ignored when "
    "sources is given."
)
LANGUAGE_PARAM: Final = "Language of the answer."
MODE_PARAM: Final = "'short' is concise, 'long' detailed."
SOURCES_PARAM: Final = "Your own sources to answer from, instead of a web search."
SOURCES_MODE_PARAM: Final = (
    "With sources: 'search' fetches and ranks the URLs, 'context' uses the given content "
    "directly without fetching."
)
ANSWER_SIMPLE_PARAM: Final = "false returns the complete raw Staan response instead of {answer}."

SOURCE_DESCRIPTION: Final = "A source: needs a url, content, or both."
URL_PARAM: Final = "Public http(s) URL of the page."
TITLE_PARAM: Final = "Title of the source."
CONTENT_PARAM: Final = "Text of the source."

ANSWER_FIELD_PARAM: Final = (
    "Answer text in Markdown; footnote citations ([^1]) and the cited URLs at the end, "
    "unless simple_response=false."
)
ANSWER_OUTPUT_DESCRIPTION: Final = (
    "The answer; with simple_response=false, the whole upstream response."
)

FULL_CONTENT_CHOICES: Final[tuple[Literal["none", "markdown", "html"], ...]] = (
    "none",
    "markdown",
    "html",
)
OFFSET_CHOICES: Final[tuple[Literal[0, 10, 20, 30], ...]] = (0, 10, 20, 30)
LANGUAGE_CHOICES: Final[tuple[Literal["en", "fr"], ...]] = ("en", "fr")
MODE_CHOICES: Final[tuple[Literal["short", "long"], ...]] = ("short", "long")
SOURCES_MODE_CHOICES: Final[tuple[Literal["search", "context"], ...]] = ("search", "context")
"""Allowed values of the enumerated arguments; the first one of each is the default."""


def _field(name: str, description: str, **schema: object) -> dict[str, Any]:
    """Build one property schema the way pydantic does: its own keys, a description, a title."""
    return {**schema, "description": description, "title": name.replace("_", " ").title()}


_STR_LIST: Final = {"type": "array", "items": {"type": "string"}}
_NULLABLE_STR_LIST: Final = [_STR_LIST, {"type": "null"}]
_NULLABLE_STR: Final = [{"type": "string"}, {"type": "null"}]

WEB_SEARCH_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "title": "web_searchArguments",
    "required": ["query"],
    "properties": {
        "query": _field(
            "query",
            SEARCH_QUERY_PARAM,
            type="string",
            minLength=1,
            maxLength=MAX_SEARCH_QUERY_LENGTH,
        ),
        "market": _field(
            "market", MARKET_PARAM, anyOf=[{"type": "string"}, _STR_LIST], default=DEFAULT_MARKET
        ),
        "full_content": _field(
            "full_content",
            FULL_CONTENT_PARAM,
            type="string",
            enum=list(FULL_CONTENT_CHOICES),
            default=FULL_CONTENT_CHOICES[0],
        ),
        "offset": _field(
            "offset",
            OFFSET_PARAM,
            type="integer",
            enum=list(OFFSET_CHOICES),
            default=OFFSET_CHOICES[0],
        ),
        "include_domains": _field(
            "include_domains", INCLUDE_PARAM, anyOf=_NULLABLE_STR_LIST, default=None
        ),
        "exclude_domains": _field(
            "exclude_domains", EXCLUDE_PARAM, anyOf=_NULLABLE_STR_LIST, default=None
        ),
        "simple_response": _field(
            "simple_response", SEARCH_SIMPLE_PARAM, type="boolean", default=True
        ),
    },
}

_ANSWER_SOURCE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "title": "AnswerSource",
    "description": SOURCE_DESCRIPTION,
    "properties": {
        "url": _field("url", URL_PARAM, anyOf=_NULLABLE_STR, default=None),
        "title": _field(
            "title",
            TITLE_PARAM,
            anyOf=[{"type": "string", "maxLength": MAX_SOURCE_TITLE_LENGTH}, {"type": "null"}],
            default=None,
        ),
        "content": _field(
            "content",
            CONTENT_PARAM,
            anyOf=[{"type": "string", "maxLength": MAX_SOURCE_CONTENT_LENGTH}, {"type": "null"}],
            default=None,
        ),
    },
}

ANSWER_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "title": "answerArguments",
    "required": ["query"],
    "$defs": {"AnswerSource": _ANSWER_SOURCE_SCHEMA},
    "properties": {
        "query": _field(
            "query",
            ANSWER_QUERY_PARAM,
            type="string",
            minLength=1,
            maxLength=MAX_ANSWER_QUERY_LENGTH,
        ),
        "filter": _field("filter", FILTER_PARAM, anyOf=_NULLABLE_STR, default=None),
        "language": _field(
            "language",
            LANGUAGE_PARAM,
            type="string",
            enum=list(LANGUAGE_CHOICES),
            default=LANGUAGE_CHOICES[0],
        ),
        "mode": _field(
            "mode", MODE_PARAM, type="string", enum=list(MODE_CHOICES), default=MODE_CHOICES[0]
        ),
        "sources": _field(
            "sources",
            SOURCES_PARAM,
            anyOf=[
                {
                    "type": "array",
                    "items": {"$ref": "#/$defs/AnswerSource"},
                    "maxItems": MAX_ANSWER_SOURCES,
                },
                {"type": "null"},
            ],
            default=None,
        ),
        "sources_mode": _field(
            "sources_mode",
            SOURCES_MODE_PARAM,
            type="string",
            enum=list(SOURCES_MODE_CHOICES),
            default=SOURCES_MODE_CHOICES[0],
        ),
        "simple_response": _field(
            "simple_response", ANSWER_SIMPLE_PARAM, type="boolean", default=True
        ),
    },
}

ANSWER_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "title": "AnswerOutput",
    "description": ANSWER_OUTPUT_DESCRIPTION,
    "required": ["answer"],
    "additionalProperties": True,
    "properties": {"answer": _field("answer", ANSWER_FIELD_PARAM, type="string")},
}


def _annotations(*, idempotent: bool) -> dict[str, bool]:
    """Build the hints shared by both tools: read-only, open-world, never destructive."""
    return {
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": idempotent,
    }


TOOL_LIST: Final[list[dict[str, Any]]] = [
    {
        "name": "web_search",
        "title": WEB_SEARCH_TITLE,
        "description": WEB_SEARCH_DESCRIPTION,
        "inputSchema": WEB_SEARCH_INPUT_SCHEMA,
        "annotations": _annotations(idempotent=True),
    },
    {
        "name": "answer",
        "title": ANSWER_TITLE,
        "description": ANSWER_DESCRIPTION,
        "inputSchema": ANSWER_INPUT_SCHEMA,
        "outputSchema": ANSWER_OUTPUT_SCHEMA,
        "annotations": _annotations(idempotent=False),
    },
]
"""The ``tools`` of a ``tools/list`` result as the standalone server sends them.

The package lets pydantic generate the same schemas from its tool signatures
(:mod:`staan_mcp.server`); a test compares the two, so they cannot drift apart.
"""
