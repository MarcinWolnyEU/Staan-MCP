"""Tools, the JSON-RPC loop and the entry point of ``stdio_server.py``, in process."""
# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

import io
import json
import runpy
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from local_staan import LocalStaan, Reply, json_reply
from stdio_helpers import (
    API_KEY,
    OK_SEARCH,
    SIMPLE_HIT,
    run_tool,
    settings_for,
    text_of,
)

from staan_mcp import stdio_server
from staan_mcp.contract import INSTRUCTIONS, TOOL_LIST
from staan_mcp.errors import (
    ConfigurationError,
)

if TYPE_CHECKING:
    from staan_mcp.config import Settings

# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


def test_web_search_sends_the_documented_body_per_market(local_staan: LocalStaan) -> None:
    run_tool(
        local_staan,
        "web_search",
        {
            "query": "python",
            "market": ["EN-GB", "fr-*"],
            "full_content": "none",
            "offset": 20,
            "exclude_domains": ["https://www.Spam.example/x", "spam.example"],
        },
    )

    assert sorted(local_staan.bodies(), key=lambda body: str(body["market"])) == [
        {
            "q": "python",
            "market": market,
            "count": 10,
            "offset": 20,
            "extra_snippets": True,
            "exclude_domains": ["spam.example"],
        }
        for market in ("en-gb", "fr-fr")
    ]


def test_web_search_asks_for_no_page_text_unless_told_to(local_staan: LocalStaan) -> None:
    run_tool(local_staan, "web_search", {"query": "x", "include_domains": ["a.com"]})

    (body,) = local_staan.bodies()
    assert "full_content" not in body
    assert body["include_domains"] == ["a.com"]
    assert "exclude_domains" not in body


def test_web_search_full_content_is_sent_as_asked(local_staan: LocalStaan) -> None:
    run_tool(local_staan, "web_search", {"query": "x", "full_content": "markdown"})

    (body,) = local_staan.bodies()
    assert body["full_content"] == "markdown"


def test_web_search_shapes_and_truncates_results(local_staan: LocalStaan) -> None:
    page = "A paragraph.\n\n" * 100
    hit = {"title": "T", "url": "https://a.example", "snippet": "S", "full_content": {"text": page}}
    local_staan.search = json_reply({"web": {"results": [hit]}})

    result = run_tool(local_staan, "web_search", {"query": "x"}, max_content_chars=300)

    (simple,) = json.loads(text_of(result))
    assert simple["full_content"].endswith("characters shown ...]")
    assert len(simple["full_content"]) < len(page)
    assert "structuredContent" not in result


def test_web_search_raw_responses_are_listed_per_market(local_staan: LocalStaan) -> None:
    local_staan.search = json_reply(OK_SEARCH)
    local_staan.by_market = {"de-de": Reply(500)}

    result = run_tool(
        local_staan,
        "web_search",
        {"query": "x", "market": ["en-gb", "de-de"], "simple_response": False},
    )

    assert json.loads(text_of(result)) == [
        OK_SEARCH,
        {
            "market": "de-de",
            "error": "Staan is temporarily unavailable (HTTP 500). Try again later.",
        },
    ]


def test_web_search_survives_a_failed_market_and_says_so_on_stderr(
    local_staan: LocalStaan, capsys: pytest.CaptureFixture[str]
) -> None:
    local_staan.search = json_reply(OK_SEARCH)
    local_staan.by_market = {"de-de": Reply(500)}

    result = run_tool(local_staan, "web_search", {"query": "x", "market": ["en-gb", "de-de"]})

    assert json.loads(text_of(result)) == [SIMPLE_HIT]
    assert capsys.readouterr().err == "staan-stdio: web_search: 1 of 2 markets failed: de-de\n"


def test_web_search_fails_when_every_market_failed(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(500)

    result = run_tool(local_staan, "web_search", {"query": "x", "market": ["en-gb", "de-de"]})

    unavailable = "Staan is temporarily unavailable (HTTP 500). Try again later."
    assert text_of(result) == (
        f"Error executing tool web_search: en-gb: {unavailable}; de-de: {unavailable}"
    )
    assert result["isError"] is True


def test_a_rejected_key_is_reported_once_for_all_markets(local_staan: LocalStaan) -> None:
    local_staan.search = Reply(401, b"{}")

    result = run_tool(local_staan, "web_search", {"query": "x", "market": ["en-gb", "de-de"]})

    assert text_of(result).startswith(
        "Error executing tool web_search: Staan authentication failed (HTTP 401)."
    )


def test_a_missing_key_is_reported_on_a_tool_call(local_staan: LocalStaan) -> None:
    result = run_tool(local_staan, "web_search", {"query": "x"}, api_key=None)

    assert text_of(result).startswith("Error executing tool web_search: STAAN_API_KEY is not set.")
    assert local_staan.requests == []


def test_a_bug_is_logged_and_reported_without_details(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_args: object) -> dict[str, Any]:
        msg = "secret detail"
        raise RuntimeError(msg)

    monkeypatch.setattr(stdio_server, "_post", broken)

    for tool in ("web_search", "answer"):
        result = run_tool(local_staan, tool, {"query": "x"})
        assert result == {
            "content": [{"type": "text", "text": f"Error executing tool {tool}"}],
            "isError": True,
        }

    err = capsys.readouterr().err
    assert "internal error in tool 'web_search'" in err
    assert "internal error in tool 'answer'" in err
    assert "secret detail" in err


def test_answer_returns_text_with_footnotes_and_structured_content(
    local_staan: LocalStaan,
) -> None:
    local_staan.answer = json_reply(
        {
            "answer": "Paris is the capital.",
            "citations": [{"position": 21, "source_id": "s1"}],
            "sources": [{"id": "s1", "url": "https://a.example"}],
        }
    )

    result = run_tool(local_staan, "answer", {"query": "capital of France?"})

    expected = {"answer": "Paris is the capital.[^1]\n\n[^1]: https://a.example"}
    assert result == {
        "content": [{"type": "text", "text": json.dumps(expected)}],
        "structuredContent": expected,
    }
    assert local_staan.bodies() == [
        {
            "query": "capital of France?",
            "language": "en",
            "mode": "short",
            "stream": False,
            "markdown": True,
        }
    ]


def test_answer_sends_the_filter_and_the_cleaned_sources(local_staan: LocalStaan) -> None:
    run_tool(
        local_staan,
        "answer",
        {
            "query": "q",
            "filter": "https://www.LeMonde.fr/politique",
            "language": "fr",
            "mode": "long",
            "sources": [
                {"url": " https://a.example/x ", "title": "A", "extra": "ignored"},
                {"url": "   ", "content": "some text"},
                {"url": None, "title": None, "content": "more"},
            ],
            "sources_mode": "context",
        },
    )

    assert local_staan.bodies() == [
        {
            "query": "q",
            "language": "fr",
            "mode": "long",
            "stream": False,
            "markdown": True,
            "filter": "lemonde.fr",
            "sources": [
                {"url": "https://a.example/x", "title": "A"},
                {"content": "some text"},
                {"content": "more"},
            ],
            "sources_mode": "context",
        }
    ]


def test_answer_without_sources_sends_no_sources_mode(local_staan: LocalStaan) -> None:
    run_tool(
        local_staan,
        "answer",
        {"query": "q", "sources": [], "sources_mode": "context", "filter": " "},
    )

    (body,) = local_staan.bodies()
    assert "sources" not in body
    assert "sources_mode" not in body
    assert "filter" not in body


def test_answer_returns_the_whole_response_when_asked(local_staan: LocalStaan) -> None:
    response = {"answer": "A", "citations": [], "usage": {"tokens": 3}}
    local_staan.answer = json_reply(response)

    result = run_tool(local_staan, "answer", {"query": "q", "simple_response": False})

    assert result["structuredContent"] == response
    assert json.loads(text_of(result)) == response


@pytest.mark.parametrize("simple_response", [True, False])
def test_answer_without_text_is_an_error_in_either_mode(
    local_staan: LocalStaan, *, simple_response: bool
) -> None:
    local_staan.answer = json_reply({"answer": None})

    result = run_tool(local_staan, "answer", {"query": "q", "simple_response": simple_response})

    assert text_of(result) == (
        "Error executing tool answer: Staan returned a response without an answer text."
    )


def test_answer_reports_an_upstream_refusal(local_staan: LocalStaan) -> None:
    local_staan.answer = Reply(403, b'{"message":"Forbidden"}')

    result = run_tool(local_staan, "answer", {"query": "q"})

    assert text_of(result) == (
        "Error executing tool answer: The Answer API is not enabled for this API key "
        "(invitation-only, contact support@staan.ai), or Staan has temporarily disabled it "
        "(HTTP 403 Forbidden). Web search is not affected."
    )


def test_an_unknown_tool_is_an_error_result(local_staan: LocalStaan) -> None:
    result = run_tool(local_staan, "nope", {})

    assert result == {"content": [{"type": "text", "text": "Unknown tool: nope"}], "isError": True}


def test_a_configuration_error_is_reported_on_every_tool_call() -> None:
    problem = ConfigurationError("STAAN_TIMEOUT must be a number of seconds, got 'x'.")

    result = stdio_server.call_tool("answer", {"query": "q"}, problem, threading.Event())

    assert text_of(result) == (
        "Error executing tool answer: STAAN_TIMEOUT must be a number of seconds, got 'x'."
    )


def test_non_ascii_text_is_kept_as_it_is(local_staan: LocalStaan) -> None:
    hit = {"title": "Café", "url": "https://a.example", "snippet": "\u00e7a va"}
    local_staan.search = json_reply({"web": {"results": [hit]}})

    result = run_tool(local_staan, "web_search", {"query": "café"})

    assert "Café" in text_of(result)
    assert local_staan.bodies()[0]["q"] == "café"


# --------------------------------------------------------------------------------------
# The JSON-RPC loop
# --------------------------------------------------------------------------------------

CLIENT_INFO = {"name": "test-client", "version": "1.0"}
GOOD_INITIALIZE: dict[str, Any] = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": CLIENT_INFO,
}
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": GOOD_INITIALIZE,
}
HANDSHAKE_ID = "handshake"
HANDSHAKE = [
    {**INITIALIZE, "id": HANDSHAKE_ID},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


def request(request_id: object, method: str, params: object = None) -> dict[str, Any]:
    """Build a JSON-RPC request; ``params`` is left out when ``None``."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def converse(
    settings: Settings | ConfigurationError, *messages: object, handshake: bool = True
) -> list[dict[str, Any]]:
    """Feed ``messages`` (objects, or raw bytes) to a server and return what it wrote.

    A handshake comes first, and its answer is left out, unless ``handshake`` is false: like the
    SDK, the server answers nothing but ``ping`` before it.
    """
    sent = [*HANDSHAKE, *messages] if handshake else messages
    lines = [m if isinstance(m, bytes) else json.dumps(m).encode() + b"\n" for m in sent]
    stdout = io.BytesIO()
    stdio_server.Server(settings, io.BytesIO(b"".join(lines)), stdout).serve()
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return [reply for reply in replies if reply.get("id") != HANDSHAKE_ID]


@pytest.fixture
def settings(local_staan: LocalStaan) -> Settings:
    """Settings for a server that talks to the fake upstream."""
    return settings_for(local_staan)


def test_initialize_negotiates_the_protocol_version(settings: Settings) -> None:
    (reply,) = converse(settings, INITIALIZE, handshake=False)

    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 1
    result = reply["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"] == {"name": "staan", "version": stdio_server.VERSION}
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["instructions"] == INSTRUCTIONS


@pytest.mark.parametrize(
    ("requested", "negotiated"),
    [
        ("2024-11-05", "2024-11-05"),
        ("2025-11-25", "2025-11-25"),
        ("1999-01-01", "2025-11-25"),
        ("", "2025-11-25"),
    ],
)
def test_an_unknown_protocol_version_gets_the_newest_one(
    settings: Settings, requested: str, negotiated: str
) -> None:
    params = {**GOOD_INITIALIZE, "protocolVersion": requested}

    (reply,) = converse(settings, request(1, "initialize", params), handshake=False)

    assert reply["result"]["protocolVersion"] == negotiated


INVALID_INITIALIZE_PARAMS: list[object] = [
    *({k: v for k, v in GOOD_INITIALIZE.items() if k != missing} for missing in GOOD_INITIALIZE),
    None,
    [],
    "x",
    {},
    {**GOOD_INITIALIZE, "protocolVersion": None},
    {**GOOD_INITIALIZE, "protocolVersion": 5},
    {**GOOD_INITIALIZE, "protocolVersion": ["2025-06-18"]},
    {**GOOD_INITIALIZE, "capabilities": None},
    {**GOOD_INITIALIZE, "capabilities": []},
    {**GOOD_INITIALIZE, "clientInfo": None},
    {**GOOD_INITIALIZE, "clientInfo": []},
    {**GOOD_INITIALIZE, "clientInfo": {}},
    {**GOOD_INITIALIZE, "clientInfo": {"name": "t"}},
    {**GOOD_INITIALIZE, "clientInfo": {"version": "1"}},
    {**GOOD_INITIALIZE, "clientInfo": {"name": 5, "version": "1"}},
    {**GOOD_INITIALIZE, "clientInfo": {"name": "t", "version": 1}},
]


@pytest.mark.parametrize("params", INVALID_INITIALIZE_PARAMS)
def test_initialize_needs_what_the_sdk_needs_and_a_refused_one_opens_nothing(
    settings: Settings, params: object
) -> None:
    replies = converse(
        settings, request(1, "initialize", params), request(2, "tools/list"), handshake=False
    )

    invalid = {"code": -32602, "message": "Invalid request parameters", "data": ""}
    assert [reply["error"] for reply in replies] == [invalid, invalid]


@pytest.mark.parametrize(
    "params",
    [
        {**GOOD_INITIALIZE, "clientInfo": {**CLIENT_INFO, "title": "T", "websiteUrl": "x"}},
        {**GOOD_INITIALIZE, "capabilities": {"roots": {"listChanged": "not checked"}}},
        {**GOOD_INITIALIZE, "_meta": {"progressToken": 1}, "extra": 1},
    ],
)
def test_initialize_does_not_check_what_is_nested(settings: Settings, params: object) -> None:
    (reply,) = converse(settings, request(1, "initialize", params), handshake=False)

    assert reply["result"]["protocolVersion"] == "2025-06-18"


def test_ping_and_the_empty_lists(settings: Settings) -> None:
    replies = converse(
        settings,
        request(1, "ping"),
        request(2, "prompts/list"),
        request(3, "resources/list"),
        request(4, "resources/templates/list"),
    )

    assert [reply["result"] for reply in replies] == [
        {},
        {"prompts": []},
        {"resources": []},
        {"resourceTemplates": []},
    ]


def test_tools_list_is_the_shared_contract(settings: Settings) -> None:
    (reply,) = converse(settings, request("a", "tools/list"))

    assert reply["id"] == "a"
    assert reply["result"] == {"tools": TOOL_LIST}


def test_an_unknown_method_is_not_found(settings: Settings) -> None:
    (reply,) = converse(settings, request(7, "server/discover"))

    assert reply["error"] == {
        "code": -32601,
        "message": "Method not found",
        "data": "server/discover",
    }


@pytest.mark.parametrize(
    "params",
    [None, [], {}, {"name": 5}, {"name": "web_search", "arguments": []}],
)
def test_tools_call_needs_a_name_and_object_arguments(settings: Settings, params: object) -> None:
    (reply,) = converse(settings, request(1, "tools/call", params))

    assert reply["error"] == {"code": -32602, "message": "Invalid request parameters", "data": ""}


def test_a_tool_call_without_arguments_reports_the_missing_query(settings: Settings) -> None:
    (reply,) = converse(settings, request(1, "tools/call", {"name": "web_search"}))

    assert reply["result"]["isError"] is True
    assert text_of(reply["result"]) == (
        "Error executing tool web_search: Missing required argument 'query'."
    )


def test_a_tool_call_returns_its_result(local_staan: LocalStaan, settings: Settings) -> None:
    local_staan.search = json_reply(OK_SEARCH)

    (reply,) = converse(
        settings,
        request("call-1", "tools/call", {"name": "web_search", "arguments": {"query": "x"}}),
    )

    assert reply["id"] == "call-1"
    assert json.loads(text_of(reply["result"])) == [SIMPLE_HIT]


def test_a_call_still_running_when_stdin_closes_is_answered(
    local_staan: LocalStaan, settings: Settings
) -> None:
    local_staan.search = Reply(201, json.dumps(OK_SEARCH).encode(), delay=0.3)

    (reply,) = converse(
        settings, request(1, "tools/call", {"name": "web_search", "arguments": {"query": "x"}})
    )

    assert json.loads(text_of(reply["result"])) == [SIMPLE_HIT]


def test_a_cancelled_call_gets_no_response(local_staan: LocalStaan, settings: Settings) -> None:
    local_staan.search = Reply(201, json.dumps(OK_SEARCH).encode(), delay=0.3)

    replies = converse(
        settings,
        request(5, "tools/call", {"name": "web_search", "arguments": {"query": "x"}}),
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 5}},
    )

    assert replies == []


def test_a_request_id_is_matched_by_type_when_cancelling(
    local_staan: LocalStaan, settings: Settings
) -> None:
    local_staan.search = Reply(201, json.dumps(OK_SEARCH).encode(), delay=0.2)

    replies = converse(
        settings,
        request(5, "tools/call", {"name": "web_search", "arguments": {"query": "x"}}),
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "5"}},
    )

    assert len(replies) == 1


@pytest.mark.parametrize(
    "notification",
    [
        {"jsonrpc": "2.0", "method": "notifications/cancelled"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": True}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99}},
        {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}},
        {"jsonrpc": "2.0", "method": "tools/list"},
    ],
)
def test_notifications_never_get_a_reply(settings: Settings, notification: object) -> None:
    assert converse(settings, notification) == []


def test_malformed_input_gets_the_standard_errors(settings: Settings) -> None:
    replies = converse(
        settings,
        b"\n",
        b"not json\n",
        b"\xff\xfe\n",
        b"[1]\n",
        b"5\n",
        request(request_id=True, method="ping"),
        request(1.5, "ping"),
        request(None, "ping"),
        {"jsonrpc": "2.0", "id": 3, "method": 5},
        {"jsonrpc": "2.0", "id": [3], "method": 5},
    )

    assert [reply["error"]["code"] for reply in replies] == [
        -32700,
        -32700,
        -32600,
        -32600,
        -32600,
        -32600,
        -32600,
        -32600,
        -32600,
    ]
    assert replies[2]["error"]["message"] == "Batch requests are not supported."
    assert replies[3]["error"]["message"] == "Invalid request: expected a JSON object."
    assert replies[4]["error"]["message"] == "Invalid request: 'id' must be a string or an integer."
    assert [reply["id"] for reply in replies[-2:]] == [3, None]


def test_responses_from_the_client_are_ignored(settings: Settings) -> None:
    assert converse(settings, {"jsonrpc": "2.0", "id": 9, "result": {}}) == []
    assert converse(settings, {"jsonrpc": "2.0", "id": 9, "error": {"code": 1}}) == []


INVALID_PARAMS = {"code": -32602, "message": "Invalid request parameters", "data": ""}
GATED_REQUESTS: list[tuple[str, object]] = [
    ("tools/list", None),
    ("tools/call", {"name": "nope"}),
    ("prompts/list", None),
    ("prompts/get", {"name": "x"}),
    ("resources/list", None),
    ("resources/templates/list", None),
    ("resources/read", {"uri": "file:///x"}),
]


@pytest.mark.parametrize(("method", "params"), GATED_REQUESTS)
def test_a_request_before_the_handshake_is_refused_like_the_sdk_does(
    settings: Settings, method: str, params: object
) -> None:
    (reply,) = converse(settings, request(1, method, params), handshake=False)

    assert reply["error"] == INVALID_PARAMS


def test_ping_and_an_unknown_method_are_answered_before_the_handshake(settings: Settings) -> None:
    ping, unknown = converse(
        settings, request(1, "ping"), request(2, "bogus/method"), handshake=False
    )

    assert ping["result"] == {}
    assert unknown["error"]["code"] == -32601


def test_the_initialize_request_alone_opens_the_server(settings: Settings) -> None:
    _, listed = converse(settings, INITIALIZE, request(2, "tools/list"), handshake=False)

    assert listed["result"] == {"tools": TOOL_LIST}


def test_the_initialized_notification_alone_opens_the_server(settings: Settings) -> None:
    (listed,) = converse(
        settings,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request(2, "tools/list"),
        handshake=False,
    )

    assert listed["result"] == {"tools": TOOL_LIST}


@pytest.mark.parametrize("arguments", [None, {}, {"a": "b"}])
def test_an_unknown_prompt_is_reported_like_the_sdk_does(
    settings: Settings, arguments: object
) -> None:
    params = {"name": "x", "arguments": arguments, "_meta": {"progressToken": 1}}

    (reply,) = converse(settings, request(1, "prompts/get", params))

    assert reply == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": 0, "message": "Unknown prompt: x"},
    }


@pytest.mark.parametrize(
    "params",
    [
        None,
        [],
        {},
        {"name": None},
        {"name": 5},
        {"name": True},
        {"name": "x", "arguments": 5},
        {"name": "x", "arguments": ["a"]},
        {"name": "x", "arguments": {"a": 1}},
        {"name": "x", "arguments": {"a": None}},
    ],
)
def test_a_prompt_request_without_a_name_or_with_odd_arguments_is_invalid(
    settings: Settings, params: object
) -> None:
    (reply,) = converse(settings, request(1, "prompts/get", params))

    assert reply["error"] == INVALID_PARAMS


@pytest.mark.parametrize("uri", ["file:///x", "x", "", "café://\u2603"])
def test_an_unknown_resource_is_reported_like_the_sdk_does(settings: Settings, uri: str) -> None:
    (reply,) = converse(settings, request(1, "resources/read", {"uri": uri, "bogus": 1}))

    assert reply["error"] == {
        "code": -32602,
        "message": f"Unknown resource: {uri}",
        "data": {"uri": uri},
    }


@pytest.mark.parametrize("params", [None, [], {}, {"uri": None}, {"uri": 5}])
def test_a_resource_request_without_a_uri_is_invalid(settings: Settings, params: object) -> None:
    (reply,) = converse(settings, request(1, "resources/read", params))

    assert reply["error"] == INVALID_PARAMS


def test_a_line_that_is_not_utf8_is_read_with_replacement_characters(settings: Settings) -> None:
    line = b'{"jsonrpc":"2.0","id":1,"method":"pi\xffng"}\n'

    (reply,) = converse(settings, line)

    assert reply["error"] == {
        "code": -32601,
        "message": "Method not found",
        "data": "pi\ufffdng",
    }


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def serve_main(monkeypatch: pytest.MonkeyPatch, *lines: object) -> list[dict[str, Any]]:
    """Run ``main()`` after a handshake, with ``lines`` on stdin; return what it wrote after it."""
    sent = [*HANDSHAKE, *lines]
    stdin = io.TextIOWrapper(io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in sent)))
    written = io.BytesIO()
    stdout = io.TextIOWrapper(written)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    assert stdio_server.main([]) == 0
    stdout.flush()
    replies = [json.loads(line) for line in written.getvalue().splitlines()]
    return [reply for reply in replies if reply.get("id") != HANDSHAKE_ID]


def test_main_serves_with_the_settings_from_the_environment(
    local_staan: LocalStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", API_KEY)
    monkeypatch.setenv("STAAN_BASE_URL", local_staan.base_url)
    local_staan.search = json_reply(OK_SEARCH)

    (reply,) = serve_main(
        monkeypatch, request(1, "tools/call", {"name": "web_search", "arguments": {"query": "x"}})
    )

    assert json.loads(text_of(reply["result"])) == [SIMPLE_HIT]
    assert local_staan.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"


def test_main_keeps_serving_after_a_configuration_problem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", API_KEY)
    monkeypatch.setenv("STAAN_TIMEOUT", "abc")
    problem = "STAAN_TIMEOUT must be a number of seconds, got 'abc'."

    (listing, call) = serve_main(
        monkeypatch,
        request(1, "tools/list"),
        request(2, "tools/call", {"name": "web_search", "arguments": {"query": "x"}}),
    )

    assert listing["result"] == {"tools": TOOL_LIST}
    assert text_of(call["result"]) == f"Error executing tool web_search: {problem}"
    assert capsys.readouterr().err == (
        f"staan-stdio: configuration problem (tools will report it): {problem}\n"
    )


def test_main_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert stdio_server.main(["--version"]) == 0
    assert stdio_server.main(["-V"]) == 0

    assert capsys.readouterr().out == f"staan-stdio {stdio_server.VERSION}\n" * 2


@pytest.mark.parametrize(("args", "status"), [(["-h"], 0), (["--help"], 0), (["--bogus"], 2)])
def test_main_prints_usage_for_other_arguments(
    capsys: pytest.CaptureFixture[str], args: list[str], status: int
) -> None:
    assert stdio_server.main(args) == status

    assert capsys.readouterr().err.startswith("usage: staan-mcp-stdio [--version]")


def test_main_reads_the_command_line_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["stdio_server.py", "--version"])

    assert stdio_server.main() == 0
    assert capsys.readouterr().out == f"staan-stdio {stdio_server.VERSION}\n"


def test_running_the_file_as_a_script_exits_with_the_status_of_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["stdio_server.py", "--version"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(stdio_server.__file__, run_name="__main__")

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"staan-stdio {stdio_server.VERSION}\n"


def test_a_logging_failure_never_breaks_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = io.StringIO()
    closed.close()  # writing to it raises ValueError
    monkeypatch.setattr(sys, "stderr", closed)

    stdio_server._log("nobody hears this")


# --------------------------------------------------------------------------------------
# Finding the package when run by path
# --------------------------------------------------------------------------------------


def test_the_directory_that_holds_the_package_is_added_to_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = str(Path(stdio_server.__file__).resolve().parent.parent)
    monkeypatch.setattr(sys, "path", ["/somewhere/else", ""])

    stdio_server._make_package_importable()

    assert sys.path == [root, "/somewhere/else", ""]


def test_the_path_is_left_alone_when_the_package_is_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = str(Path(stdio_server.__file__).resolve().parent.parent)
    monkeypatch.setattr(sys, "path", ["/somewhere/else", root])

    stdio_server._make_package_importable()

    assert sys.path == ["/somewhere/else", root]


def test_a_missing_package_is_explained_in_one_line_and_exits_with_status_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "staan_mcp.config", None)  # `import` now fails

    with pytest.raises(SystemExit) as excinfo:
        stdio_server._make_package_importable()

    assert excinfo.value.code == 2
    message = capsys.readouterr().err
    assert message.startswith("staan-mcp: cannot import the staan_mcp package.")
    assert message.count("\n") == 1  # one line, no traceback
    assert str(Path(stdio_server.__file__).resolve().parent) in message
    assert "src/staan_mcp" in message
    assert "pip install staan-mcp" in message
    assert "staan-mcp-stdio.pyz" in message
