"""Sanity checks that keep the JSON fixtures honest and secret-free."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent.parent / "fixtures"
SEARCH = sorted(p.name for p in FIXTURES.glob("search_*.json"))
ANSWER = sorted(p.name for p in FIXTURES.glob("answer_*.json"))
MAX_TEXT = 600


def test_expected_fixtures_exist() -> None:
    assert SEARCH == [
        "search_extra_snippets.json",
        "search_full_content.json",
        "search_plain.json",
        "search_short.json",
    ]
    assert ANSWER == [
        "answer_no_citations.json",
        "answer_normal.json",
        "answer_repeated_citations.json",
        "answer_unknown_sources.json",
    ]


@pytest.mark.parametrize("name", [*SEARCH, *ANSWER])
def test_fixtures_hold_no_credentials(name: str) -> None:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    assert not re.search(r"bearer\s+\S{8,}", text, re.IGNORECASE)
    assert "authorization" not in text.lower()


@pytest.mark.parametrize("name", SEARCH)
def test_search_fixtures_have_the_documented_shape(
    name: str, load_fixture: Callable[[str], Any]
) -> None:
    body = load_fixture(name)
    assert set(body) == {"search_id", "query", "web"}
    for result in body["web"]["results"]:
        assert {"title", "url", "snippet"} <= set(result)


def test_search_plain_is_a_full_page_without_enrichment(
    load_fixture: Callable[[str], Any],
) -> None:
    results = load_fixture("search_plain.json")["web"]["results"]
    assert len(results) == 10
    assert all("full_content" not in r and "extra_snippets" not in r for r in results)


def test_search_short_has_fewer_than_ten_results(load_fixture: Callable[[str], Any]) -> None:
    assert 0 < len(load_fixture("search_short.json")["web"]["results"]) < 10


def test_search_full_content_is_truncated_and_has_an_empty_text(
    load_fixture: Callable[[str], Any],
) -> None:
    contents = [
        r["full_content"] for r in load_fixture("search_full_content.json")["web"]["results"]
    ]
    assert {c["format"] for c in contents} == {"markdown"}
    assert max(len(c["text"]) for c in contents) <= MAX_TEXT
    assert any(c["text"] == "" and c["length"] == 0 for c in contents)
    assert any(c["text"] for c in contents)


def test_search_extra_snippets_are_chunk_score_objects(
    load_fixture: Callable[[str], Any],
) -> None:
    snippets = [
        r["extra_snippets"] for r in load_fixture("search_extra_snippets.json")["web"]["results"]
    ]
    assert any(s == [] for s in snippets)
    chunks = [item for s in snippets for item in s]
    assert chunks
    for item in chunks:
        assert set(item) == {"chunk", "score"}
        assert len(item["chunk"]) <= MAX_TEXT
        assert 0 <= item["score"] <= 1


@pytest.mark.parametrize("name", ANSWER)
def test_answer_fixtures_have_the_documented_shape(
    name: str, load_fixture: Callable[[str], Any]
) -> None:
    body = load_fixture(name)
    assert set(body) == {
        "request_id",
        "answer",
        "citations",
        "sources",
        "related_queries",
        "usages",
    }
    assert all(set(c) == {"position", "source_id"} for c in body["citations"])
    assert all(set(s) == {"id", "url", "title"} for s in body["sources"])


@pytest.mark.parametrize("name", ["answer_normal.json", "answer_repeated_citations.json"])
def test_answer_positions_are_inside_the_text(
    name: str, load_fixture: Callable[[str], Any]
) -> None:
    body = load_fixture(name)
    assert all(0 <= c["position"] <= len(body["answer"]) for c in body["citations"])


def test_answer_normal_leaves_one_source_uncited(load_fixture: Callable[[str], Any]) -> None:
    body = load_fixture("answer_normal.json")
    cited = {c["source_id"] for c in body["citations"]}
    assert cited == {"1", "3"}
    assert {s["id"] for s in body["sources"]} == {"1", "2", "3"}
    answer = body["answer"]
    assert answer[body["citations"][0]["position"] - 1] == "."


def test_answer_repeated_has_repeats_shared_positions_and_a_duplicate(
    load_fixture: Callable[[str], Any],
) -> None:
    pairs = [
        (c["position"], c["source_id"])
        for c in load_fixture("answer_repeated_citations.json")["citations"]
    ]
    assert len(pairs) != len(set(pairs))
    assert len({p for p, _ in pairs}) < len(pairs)
    assert [s for _, s in pairs].count("1") >= 2


def test_answer_unknown_sources_mixes_known_fallback_and_unknown_ids(
    load_fixture: Callable[[str], Any],
) -> None:
    body = load_fixture("answer_unknown_sources.json")
    known = {s["id"] for s in body["sources"]}
    ids = [c["source_id"] for c in body["citations"]]
    assert known == {"src_1", "src_2"}
    assert ids == ["2", "7", "x", "src_1"]


def test_answer_no_citations_lists_sources_but_no_citations(
    load_fixture: Callable[[str], Any],
) -> None:
    body = load_fixture("answer_no_citations.json")
    assert body["citations"] == []
    assert body["sources"]
