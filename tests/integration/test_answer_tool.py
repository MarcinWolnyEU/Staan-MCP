"""Tests of the ``answer`` tool through an in-memory ``mcp.Client`` (Staan API faked)."""
# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from staan_fakes import (
    API_KEY,
    BODY_401,
    BODY_403_DISABLED,
    BODY_403_FORBIDDEN,
    FakeStaan,
    assert_tool_error,
    call,
    error_reply,
    json_reply,
    text_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.types import CallToolResult

pytestmark = pytest.mark.anyio


async def ask(fake: FakeStaan, **arguments: Any) -> CallToolResult:
    """Call ``answer`` with ``query='why?'`` plus ``arguments``."""
    return await call(fake, "answer", {"query": "why?", **arguments})


@pytest.fixture
def answer_fixture(load_fixture: Callable[[str], Any]) -> Callable[[str], dict[str, Any]]:
    """Load ``answer_<name>.json``."""
    return lambda name: load_fixture(f"answer_{name}.json")


NORMAL_GFM = (
    "The Eiffel Tower is about 330 metres tall.[^1] It was completed in 1889 for the World "
    "Fair.[^2] Millions of people visit it every year.\n\n"
    "[^1]: https://example.org/eiffel-tower\n"
    "[^2]: https://example.org/world-fair-1889"
)


REPEATED_GFM = (
    "The Eiffel Tower is about 330 metres tall.[^1][^2] It was completed in 1889 for "
    "the World Fair.[^3][^1] Millions of people visit it every year.[^3]\n\n"
    "[^1]: https://example.org/eiffel-tower\n"
    "[^2]: https://example.org/paris-landmarks\n"
    "[^3]: https://example.org/world-fair-1889"
)
UNKNOWN_GFM = (
    "The Eiffel Tower is about 330 metres tall. It was completed in 1889 for the "
    "World Fair.[^1] Millions of people visit it every year.\n\n"
    "[^1]: https://example.org/eiffel-tower"
)
NO_CITATIONS_TEXT = (
    "The Eiffel Tower is about 330 metres tall. It was completed in 1889 for the World "
    "Fair. Millions of people visit it every year."
)


async def test_answer_simple_response_renders_citations_as_footnotes(
    fake: FakeStaan, answer_fixture: Callable[[str], dict[str, Any]]
) -> None:
    fake.answer = json_reply(answer_fixture("normal"))

    result = await ask(fake)

    assert result.is_error is False
    assert result.structured_content == {"answer": NORMAL_GFM}
    assert json.loads(text_of(result)) == {"answer": NORMAL_GFM}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("repeated_citations", REPEATED_GFM),
        ("unknown_sources", UNKNOWN_GFM),
        ("no_citations", NO_CITATIONS_TEXT),
    ],
)
async def test_answer_simple_response_for_the_other_fixtures(
    fake: FakeStaan,
    answer_fixture: Callable[[str], dict[str, Any]],
    name: str,
    expected: str,
) -> None:
    fake.answer = json_reply(answer_fixture(name))

    result = await ask(fake)

    assert result.structured_content == {"answer": expected}


async def test_answer_full_response_is_the_raw_upstream_object(
    fake: FakeStaan, answer_fixture: Callable[[str], dict[str, Any]]
) -> None:
    raw = answer_fixture("normal")
    fake.answer = json_reply(raw)

    result = await ask(fake, simple_response=False)

    assert result.is_error is False
    assert result.structured_content == raw
    assert json.loads(text_of(result)) == raw


async def test_answer_full_response_without_an_answer_field_is_an_error(
    fake: FakeStaan,
) -> None:
    fake.answer = json_reply({"request_id": "r"})

    result = await ask(fake, simple_response=False)

    assert_tool_error(result, "Staan returned a response without an answer text.")


async def test_answer_sends_the_default_request(fake: FakeStaan) -> None:
    await ask(fake)

    request = fake.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.staan.ai/v2/answer"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert fake.body == {
        "query": "why?",
        "language": "en",
        "mode": "short",
        "stream": False,
        "markdown": True,
    }


@pytest.mark.parametrize(("language", "mode"), [("fr", "long"), ("en", "long"), ("fr", "short")])
async def test_answer_forwards_language_and_mode(fake: FakeStaan, language: str, mode: str) -> None:
    await ask(fake, language=language, mode=mode)

    assert fake.body["language"] == language
    assert fake.body["mode"] == mode


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ("https://www.wikipedia.org/", "wikipedia.org"),
        ("www.LeMonde.fr", "lemonde.fr"),
        ("http://user@Example.com:8080/a?b#c", "example.com"),
        ("en.wikipedia.org", "en.wikipedia.org"),
        ("  frandroid.com  ", "frandroid.com"),
    ],
)
async def test_answer_normalises_the_filter_to_a_bare_domain(
    fake: FakeStaan, given: str, sent: str
) -> None:
    await ask(fake, filter=given)

    assert fake.body["filter"] == sent


@pytest.mark.parametrize("blank", ["", " ", "\t\n", None])
async def test_answer_blank_filter_is_omitted(fake: FakeStaan, blank: str | None) -> None:
    result = await ask(fake, filter=blank)

    assert result.is_error is False
    assert "filter" not in fake.body


@pytest.mark.parametrize("bad", ["not a domain", "localhost", "a..com", "-x.com"])
async def test_answer_invalid_filter_is_a_tool_error(fake: FakeStaan, bad: str) -> None:
    result = await ask(fake, filter=bad)

    assert result.is_error is True
    assert fake.requests == []


async def test_answer_omits_sources_by_default(fake: FakeStaan) -> None:
    await ask(fake)

    assert "sources" not in fake.body
    assert "sources_mode" not in fake.body


async def test_answer_forwards_sources_and_sources_mode(fake: FakeStaan) -> None:
    await ask(
        fake,
        sources=[
            {"url": "https://a.example/page", "title": "A"},
            {"content": "Some text.", "title": "B"},
            {"url": "https://c.example", "content": "More text."},
        ],
        sources_mode="context",
    )

    assert fake.body["sources"] == [
        {"url": "https://a.example/page", "title": "A"},
        {"title": "B", "content": "Some text."},
        {"url": "https://c.example", "content": "More text."},
    ]
    assert fake.body["sources_mode"] == "context"


async def test_answer_sources_mode_defaults_to_search_when_sources_are_given(
    fake: FakeStaan,
) -> None:
    await ask(fake, sources=[{"url": "https://a.example"}])

    assert fake.body["sources_mode"] == "search"


async def test_answer_empty_sources_list_counts_as_no_sources(fake: FakeStaan) -> None:
    await ask(fake, sources=[])

    assert "sources" not in fake.body
    assert "sources_mode" not in fake.body


async def test_answer_accepts_ten_sources(fake: FakeStaan) -> None:
    sources = [{"url": f"https://s{i}.example"} for i in range(10)]

    result = await ask(fake, sources=sources)

    assert result.is_error is False
    assert fake.body["sources"] == sources


@pytest.mark.parametrize(
    "sources",
    [
        [{"url": f"https://s{i}.example"} for i in range(11)],
        [{}],
        [{"title": "only a title"}],
        [{"url": "", "content": ""}],
        [{"url": "   ", "content": "\n"}],
        [{"url": "https://a.example", "title": "t" * 501}],
        [{"content": "c" * 50_001}],
        [{"url": "https://ok.example"}, {}],
        ["https://a.example"],
    ],
)
async def test_answer_invalid_sources_are_rejected_before_any_request(
    fake: FakeStaan, sources: list[Any]
) -> None:
    result = await ask(fake, sources=sources)

    assert result.is_error is True
    assert fake.requests == []


async def test_answer_source_validation_message_names_the_problem(fake: FakeStaan) -> None:
    result = await ask(fake, sources=[{"title": "x"}])

    assert "Each source needs a non-empty 'url' or 'content'." in text_of(result)


async def test_answer_accepts_the_longest_allowed_source_fields(fake: FakeStaan) -> None:
    source = {"content": "c" * 50_000, "title": "t" * 500}

    result = await ask(fake, sources=[source])

    assert result.is_error is False
    assert fake.body["sources"] == [source]


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": "x" * 2001},
        {"language": "de"},
        {"mode": "medium"},
        {"sources_mode": "fetch"},
    ],
)
async def test_answer_schema_violations_never_reach_the_upstream(
    fake: FakeStaan, arguments: dict[str, Any]
) -> None:
    result = await call(fake, "answer", {"query": "why?", **arguments})

    assert result.is_error is True
    assert fake.requests == []


async def test_answer_accepts_the_longest_allowed_query(fake: FakeStaan) -> None:
    result = await ask(fake, query="q" * 2000)

    assert result.is_error is False
    assert fake.body["query"] == "q" * 2000


async def test_answer_forbidden_explains_the_allowlist(fake: FakeStaan) -> None:
    fake.answer = error_reply(403, BODY_403_FORBIDDEN)

    result = await ask(fake)

    assert_tool_error(result, "not enabled for this API key")
    assert "support@staan.ai" in text_of(result)
    assert "invitation-only" in text_of(result)


async def test_answer_disabled_key_is_reported(fake: FakeStaan) -> None:
    fake.answer = error_reply(403, BODY_403_DISABLED)

    assert_tool_error(await ask(fake), "This Staan API key is disabled")


async def test_answer_unauthorized_is_reported(fake: FakeStaan) -> None:
    fake.answer = error_reply(401, BODY_401)

    assert_tool_error(await ask(fake), "Staan authentication failed (HTTP 401)")


async def test_answer_validation_error_joins_the_upstream_messages(fake: FakeStaan) -> None:
    fake.answer = error_reply(
        400, b'{"message":["query must be a string","mode must be short or long"]}'
    )

    result = await ask(fake)

    assert_tool_error(
        result, "Staan rejected the request: query must be a string; mode must be short or long"
    )


@pytest.mark.parametrize("payload", [{}, {"answer": None}, {"answer": 42}, {"answer": ["x"]}])
async def test_answer_without_a_text_answer_is_a_tool_error(
    fake: FakeStaan, payload: dict[str, Any]
) -> None:
    fake.answer = json_reply(payload)

    result = await ask(fake)

    assert_tool_error(result, "Staan returned a response without an answer text.")


@pytest.mark.parametrize(
    ("citations", "sources"),
    [("nope", []), ([], "nope"), (None, None), ({"position": 1}, {})],
)
async def test_answer_ignores_citations_and_sources_of_the_wrong_type(
    fake: FakeStaan, citations: object, sources: object
) -> None:
    fake.answer = json_reply({"answer": "Plain.", "citations": citations, "sources": sources})

    result = await ask(fake)

    assert result.structured_content == {"answer": "Plain."}


async def test_answer_empty_text_is_still_an_answer(fake: FakeStaan) -> None:
    fake.answer = json_reply({"answer": ""})

    assert (await ask(fake)).structured_content == {"answer": ""}


async def test_answer_accepts_200_and_201(fake: FakeStaan) -> None:
    fake.answer = json_reply({"answer": "ok"}, status=200)

    assert (await ask(fake)).structured_content == {"answer": "ok"}


# --------------------------------------------------------------------------------------
# Source URL validation (Staan fetches these URLs: no SSRF through tool arguments)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://files.example/x",
        "gopher://example.org/",
        "javascript:alert(1)",
        "data:text/plain,hi",
        "x",
        "example.org/page",
        "//example.org/page",
        "https://",
        "http:///path",
        "https://exa mple.org/",
        "https://example.org/a b",
        "https://example.org/\nInjected: 1",
        "https://example.org/\x00",
        "https://example.org:notaport/",
        "https://example.org/" + "x" * 2049,
        "x" * 100_000,
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://127.1.2.3:8080/admin",
        "http://localhost/",
        "http://LOCALHOST:3000/",
        "http://localhost./",
        "http://api.localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
    ],
    ids=lambda url: url[:40],
)
async def test_answer_rejects_unsafe_source_urls_before_any_request(
    fake: FakeStaan, url: str
) -> None:
    result = await ask(fake, sources=[{"url": url}], sources_mode="search")

    assert result.is_error is True
    assert fake.requests == []


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("file:///etc/passwd", "must be an absolute http or https URL with a host"),
        ("https://example.org/a b", "must not contain whitespace or control characters"),
        ("https://example.org/" + "x" * 2049, "limited to 2048 characters"),
        ("https://example.org:notaport/", "is not a valid URL"),
        ("http://169.254.169.254/", "must point to a public host"),
    ],
)
async def test_answer_source_url_errors_say_what_is_wrong(
    fake: FakeStaan, url: str, message: str
) -> None:
    result = await ask(fake, sources=[{"url": url}])

    assert message in text_of(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/page",
        "http://example.org/",
        "HTTPS://Example.ORG/Path?q=1#frag",
        "https://sub.example.org:8443/a/b?c=d",
        "https://[2001:4860:4860::8888]/",
        "http://8.8.8.8/",
        "https://example.org/" + "x" * (2048 - len("https://example.org/")),
        "https://café.example/",
    ],
    ids=lambda url: url[:40],
)
async def test_answer_accepts_public_http_and_https_source_urls(fake: FakeStaan, url: str) -> None:
    result = await ask(fake, sources=[{"url": url}])

    assert result.is_error is False
    assert fake.body["sources"] == [{"url": url}]


async def test_answer_strips_the_source_url_and_treats_a_blank_one_as_missing(
    fake: FakeStaan,
) -> None:
    result = await ask(
        fake,
        sources=[{"url": "  https://example.org/a \n"}, {"url": "   ", "content": "Text."}],
        sources_mode="context",
    )

    assert result.is_error is False
    assert fake.body["sources"] == [{"url": "https://example.org/a"}, {"content": "Text."}]


# --------------------------------------------------------------------------------------
# Output contract: {"answer": str} is declared, enforced and compact
# --------------------------------------------------------------------------------------


async def test_answer_text_block_is_compact_json_equal_to_the_structured_content(
    fake: FakeStaan, answer_fixture: Callable[[str], dict[str, Any]]
) -> None:
    fake.answer = json_reply(answer_fixture("normal"))

    result = await ask(fake)

    assert text_of(result) == json.dumps({"answer": NORMAL_GFM}, ensure_ascii=False)
    assert "\n  " not in text_of(result)  # not pretty-printed
    assert json.loads(text_of(result)) == result.structured_content


async def test_answer_text_block_keeps_non_ascii_characters(fake: FakeStaan) -> None:
    fake.answer = json_reply({"answer": "Café crème"})

    result = await ask(fake)

    assert "Café crème" in text_of(result)
    assert "\\u00e9" not in text_of(result)


async def test_answer_raw_response_is_compact_and_structured(
    fake: FakeStaan, answer_fixture: Callable[[str], dict[str, Any]]
) -> None:
    raw = answer_fixture("normal")
    fake.answer = json_reply(raw)

    result = await ask(fake, simple_response=False)

    assert text_of(result) == json.dumps(raw, ensure_ascii=False)
    assert result.structured_content == raw


async def test_answer_failure_has_no_structured_content(fake: FakeStaan) -> None:
    fake.answer = error_reply(401, BODY_401)

    result = await ask(fake)

    assert result.is_error is True
    assert result.structured_content is None


# --------------------------------------------------------------------------------------
# Configuration mistakes reach the model as a clear tool error
# --------------------------------------------------------------------------------------


async def test_answer_with_an_unusable_base_url_is_a_clear_tool_error(fake: FakeStaan) -> None:
    result = await call(fake, "answer", {"query": "why?"}, base_url="https://api.staan.ai:abc/v2")

    assert_tool_error(result, "The request to Staan could not be built")
    assert "STAAN_BASE_URL" in text_of(result)
    assert fake.requests == []


async def test_answer_with_an_unsendable_api_key_is_a_clear_tool_error(fake: FakeStaan) -> None:
    result = await call(fake, "answer", {"query": "why?"}, api_key="sk-“pasted”")

    assert_tool_error(result, "The request to Staan could not be built")
    assert "pasted" not in text_of(result)
