"""Unit and property tests for ``staan_mcp.formatting``."""
# pylint: disable=redefined-outer-name,missing-function-docstring,too-many-lines,use-implicit-booleaness-not-comparison

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from staan_mcp.formatting import (
    SNIPPET_SEPARATOR,
    _index_for,
    _snap_index,
    citations_to_gfm,
    interleave_results,
    simplify_result,
    truncate_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

NBSP = chr(0xA0)
EM_SPACE = chr(0x2003)
ARABIC_INDIC_ONE = chr(0x661)
URL_A = "https://example.org/a"
URL_B = "https://example.org/b"
URL_C = "https://example.org/c"
SOURCES: list[dict[str, str]] = [
    {"id": "1", "url": URL_A, "title": "A"},
    {"id": "2", "url": URL_B, "title": "B"},
    {"id": "3", "url": URL_C, "title": "C"},
]


def cite(position: object, source_id: object) -> dict[str, Any]:
    return {"position": position, "source_id": source_id}


# --------------------------------------------------------------------------------------
# simplify_result
# --------------------------------------------------------------------------------------


def test_separator_constant() -> None:
    assert SNIPPET_SEPARATOR == " (...) "


def test_simplify_basic_result_has_exactly_three_keys() -> None:
    result = {"title": "T", "url": "https://x.org", "snippet": "S", "hostname": "x.org"}
    assert simplify_result(result) == {"title": "T", "url": "https://x.org", "snippet": "S"}


def test_simplify_missing_fields_become_empty_strings() -> None:
    assert simplify_result({}) == {"title": "", "url": "", "snippet": ""}


@pytest.mark.parametrize("junk", [None, 5, 1.5, True, ["a"], {"a": 1}])
def test_simplify_non_string_text_fields_become_empty(junk: object) -> None:
    assert simplify_result({"title": junk, "url": junk, "snippet": junk}) == {
        "title": "",
        "url": "",
        "snippet": "",
    }


def test_simplify_without_extra_snippets_keeps_snippet_untouched() -> None:
    assert simplify_result({"snippet": "  padded  "})["snippet"] == "  padded  "


@pytest.mark.parametrize("extra", [[], (), None, "not a list", 7, {"chunk": "x"}])
def test_simplify_empty_or_invalid_extra_snippets_are_ignored(extra: object) -> None:
    assert simplify_result({"snippet": "S", "extra_snippets": extra})["snippet"] == "S"


def test_simplify_dict_chunks_are_joined_with_separator() -> None:
    extra = [{"chunk": "one", "score": 0.9}, {"chunk": "two", "score": 0.5}]
    result = simplify_result({"snippet": "S", "extra_snippets": extra})
    assert result["snippet"] == "S (...) one (...) two"


def test_simplify_string_chunks_are_joined_with_separator() -> None:
    result = simplify_result({"snippet": "S", "extra_snippets": ["one", "two"]})
    assert result["snippet"] == "S (...) one (...) two"


def test_simplify_tuple_of_chunks_is_accepted() -> None:
    result = simplify_result({"snippet": "S", "extra_snippets": ({"chunk": "one"},)})
    assert result["snippet"] == "S (...) one"


def test_simplify_mixed_chunk_types_keep_order() -> None:
    extra = ["first", {"chunk": "second"}, "third"]
    assert simplify_result({"snippet": "S", "extra_snippets": extra})["snippet"] == (
        "S (...) first (...) second (...) third"
    )


@pytest.mark.parametrize(
    "junk",
    [
        None,
        42,
        3.5,
        True,
        ["nested"],
        {"score": 0.5},
        {"chunk": 123},
        {"chunk": None},
        {"chunk": ["x"]},
    ],
)
def test_simplify_junk_chunks_are_skipped(junk: object) -> None:
    extra = ["before", junk, {"chunk": "after"}]
    result = simplify_result({"snippet": "S", "extra_snippets": extra})
    assert result["snippet"] == "S (...) before (...) after"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t", {"chunk": ""}, {"chunk": "  \n"}])
def test_simplify_blank_chunks_are_dropped(blank: object) -> None:
    extra = ["one", blank, "two"]
    assert simplify_result({"snippet": "S", "extra_snippets": extra})["snippet"] == (
        "S (...) one (...) two"
    )


def test_simplify_blank_main_snippet_is_dropped_without_leading_separator() -> None:
    result = simplify_result({"snippet": "  ", "extra_snippets": [{"chunk": "only"}]})
    assert result["snippet"] == "only"


def test_simplify_missing_main_snippet_still_uses_chunks() -> None:
    assert simplify_result({"extra_snippets": ["a", "b"]})["snippet"] == "a (...) b"


def test_simplify_everything_blank_gives_empty_snippet() -> None:
    result = simplify_result({"snippet": "", "extra_snippets": ["", " "]})
    assert result["snippet"] == ""


def test_simplify_keeps_inner_whitespace_of_kept_parts() -> None:
    result = simplify_result({"snippet": " a ", "extra_snippets": [" b "]})
    assert result["snippet"] == " a  (...)  b "


def test_simplify_full_content_dict_with_text() -> None:
    full = {"text": "# Page", "format": "markdown", "length": 6}
    result = simplify_result({"title": "T", "full_content": full})
    assert result["full_content"] == "# Page"


def test_simplify_full_content_string() -> None:
    assert simplify_result({"full_content": "plain"})["full_content"] == "plain"


@pytest.mark.parametrize(
    "full",
    [
        {"text": "", "format": "markdown", "length": 0},
        {"format": "markdown", "length": 3},
        {"text": None},
        {"text": 12},
        {"text": ["x"]},
        "",
        None,
        0,
        12,
        ["text"],
    ],
)
def test_simplify_full_content_empty_or_invalid_is_omitted(full: object) -> None:
    result = simplify_result({"title": "T", "full_content": full})
    assert "full_content" not in result


def test_simplify_without_full_content_key_has_no_full_content() -> None:
    assert "full_content" not in simplify_result({"title": "T"})


def test_simplify_whitespace_only_full_content_text_is_kept() -> None:
    # The contract only drops empty text; trimming is not its job.
    assert simplify_result({"full_content": {"text": " "}})["full_content"] == " "


def test_simplify_does_not_mutate_input() -> None:
    result = {"snippet": "S", "extra_snippets": [{"chunk": "c"}], "full_content": {"text": "t"}}
    snapshot = {
        "snippet": "S",
        "extra_snippets": [{"chunk": "c"}],
        "full_content": {"text": "t"},
    }
    simplify_result(result)
    assert result == snapshot


def test_simplify_plain_fixture(load_fixture: Callable[[str], Any]) -> None:
    results = load_fixture("search_plain.json")["web"]["results"]
    simple = [simplify_result(r) for r in results]
    assert len(simple) == 10
    for raw, item in zip(results, simple, strict=True):
        assert item == {"title": raw["title"], "url": raw["url"], "snippet": raw["snippet"]}


def test_simplify_extra_snippets_fixture(load_fixture: Callable[[str], Any]) -> None:
    results = load_fixture("search_extra_snippets.json")["web"]["results"]
    first, *_, last = (simplify_result(r) for r in results)
    raw_first = results[0]
    expected = SNIPPET_SEPARATOR.join(
        [raw_first["snippet"], *(e["chunk"] for e in raw_first["extra_snippets"])]
    )
    assert first["snippet"] == expected
    assert first["snippet"].count(SNIPPET_SEPARATOR) == 3
    # The last result has `extra_snippets: []`, so its snippet is the plain one.
    assert last["snippet"] == results[-1]["snippet"]
    assert SNIPPET_SEPARATOR not in last["snippet"]


def test_simplify_full_content_fixture(load_fixture: Callable[[str], Any]) -> None:
    results = load_fixture("search_full_content.json")["web"]["results"]
    simple = [simplify_result(r) for r in results]
    with_content = [s for s in simple if "full_content" in s]
    # Exactly one upstream result (the video page) carries an empty `text`.
    assert len(simple) == 10
    assert len(with_content) == 9
    assert "full_content" not in simple[4]
    assert simple[0]["full_content"] == results[0]["full_content"]["text"]


# --------------------------------------------------------------------------------------
# interleave_results
# --------------------------------------------------------------------------------------


def item(url: object, tag: str = "") -> dict[str, Any]:
    return {"url": url, "tag": tag}


def urls(results: Sequence[Mapping[str, Any]]) -> list[object]:
    return [r["url"] for r in results]


def test_interleave_empty_input() -> None:
    assert interleave_results([]) == []


def test_interleave_only_empty_markets() -> None:
    assert interleave_results([[], []]) == []


def test_interleave_single_market_keeps_order() -> None:
    market = [item("a"), item("b"), item("c")]
    assert interleave_results([market]) == market


def test_interleave_round_robin_equal_lengths() -> None:
    m1 = [item("a1"), item("a2")]
    m2 = [item("b1"), item("b2")]
    m3 = [item("c1"), item("c2")]
    assert urls(interleave_results([m1, m2, m3])) == ["a1", "b1", "c1", "a2", "b2", "c2"]


def test_interleave_uneven_lengths() -> None:
    m1 = [item("a1"), item("a2"), item("a3"), item("a4")]
    m2 = [item("b1")]
    m3: list[dict[str, Any]] = []
    m4 = [item("d1"), item("d2")]
    assert urls(interleave_results([m1, m2, m3, m4])) == [
        "a1",
        "b1",
        "d1",
        "a2",
        "d2",
        "a3",
        "a4",
    ]


def test_interleave_shorter_first_list() -> None:
    m1 = [item("a1")]
    m2 = [item("b1"), item("b2"), item("b3")]
    assert urls(interleave_results([m1, m2])) == ["a1", "b1", "b2", "b3"]


def test_interleave_dedupes_by_url_first_occurrence_wins() -> None:
    m1 = [item("same", "m1"), item("a")]
    m2 = [item("same", "m2"), item("b")]
    merged = interleave_results([m1, m2])
    assert urls(merged) == ["same", "a", "b"]
    assert merged[0]["tag"] == "m1"


def test_interleave_dedupe_prefers_better_rank_over_market_order() -> None:
    m1 = [item("x"), item("dup", "m1")]
    m2 = [item("dup", "m2"), item("y")]
    merged = interleave_results([m1, m2])
    assert urls(merged) == ["x", "dup", "y"]
    assert merged[1]["tag"] == "m2"


def test_interleave_dedupes_within_one_market() -> None:
    assert urls(interleave_results([[item("a"), item("a"), item("b")]])) == ["a", "b"]


def test_interleave_url_comparison_is_exact() -> None:
    market = [item("https://x.org/a"), item("https://x.org/a/"), item("https://X.org/a")]
    assert len(interleave_results([market])) == 3


@pytest.mark.parametrize("bad", [None, 5, ["u"], b"bytes", True])
def test_interleave_drops_items_without_str_url(bad: object) -> None:
    market = [item("a"), item(bad), {"title": "no url"}, item("b")]
    assert urls(interleave_results([market])) == ["a", "b"]


def test_interleave_missing_url_does_not_shift_other_ranks() -> None:
    m1 = [{"title": "no url"}, item("a2")]
    m2 = [item("b1"), item("b2")]
    assert urls(interleave_results([m1, m2])) == ["b1", "a2", "b2"]


def test_interleave_returns_the_original_objects() -> None:
    first = item("a")
    merged = interleave_results([[first]])
    assert merged[0] is first


def test_interleave_accepts_tuples_and_does_not_mutate_inputs() -> None:
    m1 = (item("a"), item("b"))
    m2 = (item("c"),)
    assert urls(interleave_results((m1, m2))) == ["a", "c", "b"]
    assert m1 == (item("a"), item("b"))


def test_interleave_fixtures_across_markets(load_fixture: Callable[[str], Any]) -> None:
    plain = load_fixture("search_plain.json")["web"]["results"]
    short = load_fixture("search_short.json")["web"]["results"]
    merged = interleave_results([plain, short])
    assert len(merged) == len(plain) + len(short)
    assert merged[0] is plain[0]
    assert merged[1] is short[0]
    assert merged[2] is plain[1]
    # Ranks 9 only exist for the ten-item page.
    assert merged[-1] is plain[9]


def test_interleave_fixture_with_itself_collapses_to_one_page(
    load_fixture: Callable[[str], Any],
) -> None:
    plain = load_fixture("search_plain.json")["web"]["results"]
    assert urls(interleave_results([plain, plain, plain])) == urls(plain)


# --------------------------------------------------------------------------------------
# citations_to_gfm, examples
# --------------------------------------------------------------------------------------


def test_gfm_docs_style_example() -> None:
    answer = "Paris is the capital of France. It lies on the Seine."
    sources = [
        {"id": "1", "url": "http://google.com"},
        {"id": "2", "url": "http://bing.com"},
    ]
    citations = [cite(31, "1"), cite(53, "2")]
    assert citations_to_gfm(answer, citations, sources) == (
        "Paris is the capital of France.[^1] It lies on the Seine.[^2]"
        "\n\n[^1]: http://google.com\n[^2]: http://bing.com"
    )


def test_gfm_normal_fixture(load_fixture: Callable[[str], Any]) -> None:
    data = load_fixture("answer_normal.json")
    assert citations_to_gfm(data["answer"], data["citations"], data["sources"]) == (
        "The Eiffel Tower is about 330 metres tall.[^1] It was completed in 1889 for the "
        "World Fair.[^2] Millions of people visit it every year."
        "\n\n[^1]: https://example.org/eiffel-tower\n[^2]: https://example.org/world-fair-1889"
    )


def test_gfm_repeated_citations_fixture(load_fixture: Callable[[str], Any]) -> None:
    data = load_fixture("answer_repeated_citations.json")
    assert citations_to_gfm(data["answer"], data["citations"], data["sources"]) == (
        "The Eiffel Tower is about 330 metres tall.[^1][^2] It was completed in 1889 for the "
        "World Fair.[^3][^1] Millions of people visit it every year.[^3]"
        "\n\n[^1]: https://example.org/eiffel-tower"
        "\n[^2]: https://example.org/paris-landmarks"
        "\n[^3]: https://example.org/world-fair-1889"
    )


def test_gfm_unknown_sources_fixture(load_fixture: Callable[[str], Any]) -> None:
    data = load_fixture("answer_unknown_sources.json")
    # The sources have ids, so "2" is not guessed to mean sources[1] (that would cite the wrong
    # page); "2", "7" and "x" are dropped and only "src_1" is exact.
    assert citations_to_gfm(data["answer"], data["citations"], data["sources"]) == (
        "The Eiffel Tower is about 330 metres tall. It was completed in 1889 for the "
        "World Fair.[^1] Millions of people visit it every year."
        "\n\n[^1]: https://example.org/eiffel-tower"
    )


def test_gfm_no_citations_fixture_is_unchanged(load_fixture: Callable[[str], Any]) -> None:
    data = load_fixture("answer_no_citations.json")
    assert citations_to_gfm(data["answer"], data["citations"], data["sources"]) == data["answer"]


# --------------------------------------------------------------------------------------
# citations_to_gfm, numbering and grouping
# --------------------------------------------------------------------------------------


def test_gfm_repeated_source_reuses_its_number() -> None:
    result = citations_to_gfm(
        "One. Two. Three.", [cite(4, "1"), cite(9, "2"), cite(16, "1")], SOURCES
    )
    assert result == f"One.[^1] Two.[^2] Three.[^1]\n\n[^1]: {URL_A}\n[^2]: {URL_B}"


def test_gfm_multiple_citations_at_same_position_join_in_input_order() -> None:
    result = citations_to_gfm("Hello.", [cite(6, "2"), cite(6, "1")], SOURCES)
    assert result == f"Hello.[^1][^2]\n\n[^1]: {URL_B}\n[^2]: {URL_A}"


def test_gfm_same_position_duplicate_pair_is_deduped() -> None:
    result = citations_to_gfm("Hello.", [cite(6, "1"), cite(6, "1"), cite(6, "1")], SOURCES)
    assert result == f"Hello.[^1]\n\n[^1]: {URL_A}"


def test_gfm_same_position_markers_are_adjacent_and_not_repeated() -> None:
    result = citations_to_gfm("Hi.", [cite(3, "1"), cite(3, "2"), cite(3, "1")], SOURCES)
    assert result.startswith("Hi.[^1][^2]\n\n")


def test_gfm_numbers_follow_text_order_not_input_order() -> None:
    result = citations_to_gfm("First. Second.", [cite(14, "1"), cite(6, "2")], SOURCES)
    assert result == f"First.[^1] Second.[^2]\n\n[^1]: {URL_B}\n[^2]: {URL_A}"


def test_gfm_ties_keep_input_order_for_numbering() -> None:
    result = citations_to_gfm("Hi.", [cite(3, "3"), cite(3, "1"), cite(3, "2")], SOURCES)
    assert result == f"Hi.[^1][^2][^3]\n\n[^1]: {URL_C}\n[^2]: {URL_A}\n[^3]: {URL_B}"


def test_gfm_definitions_only_for_cited_sources_in_number_order() -> None:
    result = citations_to_gfm("A. B.", [cite(5, "3"), cite(2, "2")], SOURCES)
    assert result == f"A.[^1] B.[^2]\n\n[^1]: {URL_B}\n[^2]: {URL_C}"
    assert URL_A not in result


def test_gfm_definitions_block_is_separated_by_one_blank_line() -> None:
    result = citations_to_gfm("Hi.", [cite(3, "1")], SOURCES)
    assert result == f"Hi.[^1]\n\n[^1]: {URL_A}"


def test_gfm_sources_with_same_url_share_a_footnote() -> None:
    sources = [{"id": "a", "url": URL_A}, {"id": "b", "url": URL_A}]
    result = citations_to_gfm("One. Two.", [cite(4, "a"), cite(9, "b")], sources)
    assert result == f"One.[^1] Two.[^1]\n\n[^1]: {URL_A}"


def test_gfm_citation_at_start_and_end() -> None:
    result = citations_to_gfm("Word", [cite(0, "1"), cite(4, "2")], SOURCES)
    assert result == f"[^1]Word[^2]\n\n[^1]: {URL_A}\n[^2]: {URL_B}"


# --------------------------------------------------------------------------------------
# citations_to_gfm, unchanged / ignored citations
# --------------------------------------------------------------------------------------


def test_gfm_empty_citations_returns_answer_unchanged() -> None:
    assert citations_to_gfm("Text.", [], SOURCES) == "Text."


def test_gfm_empty_sources_returns_answer_unchanged() -> None:
    assert citations_to_gfm("Text.", [cite(2, "1")], []) == "Text."


def test_gfm_empty_answer_is_marked_at_zero() -> None:
    assert citations_to_gfm("", [cite(0, "1")], SOURCES) == f"[^1]\n\n[^1]: {URL_A}"


def test_gfm_unknown_source_id_is_ignored() -> None:
    assert citations_to_gfm("Text.", [cite(2, "nope")], SOURCES) == "Text."


def test_gfm_unknown_source_is_ignored_but_valid_one_is_kept() -> None:
    result = citations_to_gfm("Text.", [cite(2, "nope"), cite(5, "2")], SOURCES)
    assert result == f"Text.[^1]\n\n[^1]: {URL_B}"


def test_gfm_missing_fields_are_ignored() -> None:
    citations: list[Mapping[str, Any]] = [{"position": 2}, {"source_id": "1"}, {}]
    assert citations_to_gfm("Text.", citations, SOURCES) == "Text."


@pytest.mark.parametrize("junk", [None, "str", 5, ["x"], b"bytes"])
def test_gfm_non_mapping_citation_is_ignored(junk: Any) -> None:
    assert citations_to_gfm("Text.", [junk, cite(5, "1")], SOURCES) == f"Text.[^1]\n\n[^1]: {URL_A}"


@pytest.mark.parametrize("junk", [None, "str", 5, ["x"]])
def test_gfm_non_mapping_source_is_ignored(junk: Any) -> None:
    sources = [junk, {"id": "x", "url": URL_A}]
    assert citations_to_gfm("Text.", [cite(5, "x")], sources) == f"Text.[^1]\n\n[^1]: {URL_A}"


@pytest.mark.parametrize("source_id", [None, [], {}, 1.5, ("1",)])
def test_gfm_non_str_non_int_source_id_is_ignored(source_id: object) -> None:
    assert citations_to_gfm("Text.", [cite(2, source_id)], SOURCES) == "Text."


def test_gfm_int_source_id_matches_string_id() -> None:
    assert citations_to_gfm("Text.", [cite(5, 2)], SOURCES) == f"Text.[^1]\n\n[^1]: {URL_B}"


def test_gfm_int_source_id_in_sources_matches_string_citation() -> None:
    sources = [{"id": 7, "url": URL_A}]
    assert citations_to_gfm("Text.", [cite(5, "7")], sources) == f"Text.[^1]\n\n[^1]: {URL_A}"


def test_gfm_source_without_url_cannot_be_cited() -> None:
    sources: list[dict[str, Any]] = [
        {"id": "1", "title": "no url"},
        {"id": "2", "url": "   "},
        {"id": "3", "url": 5},
    ]
    assert citations_to_gfm("Text.", [cite(2, "1"), cite(3, "2"), cite(4, "3")], sources) == "Text."


def test_gfm_source_without_id_is_only_reachable_by_numeric_fallback() -> None:
    sources = [{"url": URL_A}]
    assert citations_to_gfm("Text.", [cite(2, "x")], sources) == "Text."
    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == f"Text.[^1]\n\n[^1]: {URL_A}"


def test_gfm_url_is_stripped_in_definition() -> None:
    sources = [{"id": "1", "url": f"  {URL_A}\n"}]
    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == f"Text.[^1]\n\n[^1]: {URL_A}"


def test_gfm_first_usable_source_wins_for_repeated_id() -> None:
    sources = [{"id": "1", "url": URL_A}, {"id": "1", "url": URL_B}]
    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == f"Text.[^1]\n\n[^1]: {URL_A}"


def test_gfm_urlless_duplicate_id_does_not_shadow_a_usable_one() -> None:
    sources = [{"id": "1"}, {"id": "1", "url": URL_B}]
    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == f"Text.[^1]\n\n[^1]: {URL_B}"


# --------------------------------------------------------------------------------------
# citations_to_gfm, numeric 1-based fallback
# --------------------------------------------------------------------------------------


OPAQUE_SOURCES = [{"id": "src_a", "url": URL_A}, {"id": "src_b", "url": URL_B}]
NUMBERED_SOURCES = [{"url": URL_A}, {"url": URL_B}]  # no ids: the position is the only handle


@pytest.mark.parametrize(("source_id", "url"), [("1", URL_A), ("2", URL_B), (2, URL_B)])
def test_gfm_numeric_fallback_is_one_based(source_id: object, url: str) -> None:
    assert citations_to_gfm("Text.", [cite(5, source_id)], NUMBERED_SOURCES) == (
        f"Text.[^1]\n\n[^1]: {url}"
    )


@pytest.mark.parametrize(
    "source_id", ["0", "3", "-1", "+1", "1.0", " 1", "01x", "", ARABIC_INDIC_ONE, 0, 3, -1]
)
def test_gfm_numeric_fallback_rejects_out_of_range_or_non_decimal(source_id: object) -> None:
    # "\u0661" is an Arabic-Indic digit one: decimal for Python, but not an API-style number.
    assert citations_to_gfm("Text.", [cite(5, source_id)], NUMBERED_SOURCES) == "Text."


def test_gfm_exact_id_beats_numeric_fallback() -> None:
    sources = [{"id": "2", "url": URL_A}, {"id": "1", "url": URL_B}]
    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == f"Text.[^1]\n\n[^1]: {URL_B}"


def test_gfm_numeric_fallback_to_source_without_url_is_ignored() -> None:
    sources = [{"title": "no id, no url"}, {"url": URL_B}]
    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == "Text."


def test_gfm_unknown_id_is_not_guessed_when_the_sources_have_ids() -> None:
    # Regression: ids "1" and "3" with a citation to "2" must not cite sources[1], the "3" page.
    sources = [{"id": "1", "url": URL_A}, {"id": "3", "url": URL_C}]
    assert citations_to_gfm("Text.", [cite(5, "2")], sources) == "Text."
    assert citations_to_gfm("Text.", [cite(5, "2"), cite(5, "3")], sources) == (
        f"Text.[^1]\n\n[^1]: {URL_C}"
    )


@pytest.mark.parametrize("source_id", ["2", 2])
def test_gfm_unknown_id_is_not_guessed_for_named_sources(source_id: object) -> None:
    assert citations_to_gfm("Text.", [cite(5, source_id)], OPAQUE_SOURCES) == "Text."


def test_gfm_numeric_fallback_and_exact_id_never_mix() -> None:
    result = citations_to_gfm("One. Two.", [cite(4, "src_a"), cite(9, "1")], OPAQUE_SOURCES)
    assert result == f"One.[^1] Two.\n\n[^1]: {URL_A}"


# --------------------------------------------------------------------------------------
# citations_to_gfm, positions
# --------------------------------------------------------------------------------------


def test_gfm_position_beyond_end_is_clamped_to_end() -> None:
    assert citations_to_gfm("Text.", [cite(999, "1")], SOURCES) == f"Text.[^1]\n\n[^1]: {URL_A}"


def test_gfm_position_equal_to_length_is_end() -> None:
    assert citations_to_gfm("Text.", [cite(5, "1")], SOURCES) == f"Text.[^1]\n\n[^1]: {URL_A}"


def test_gfm_negative_position_is_clamped_to_start() -> None:
    assert citations_to_gfm("Text.", [cite(-3, "1")], SOURCES) == f"[^1]Text.\n\n[^1]: {URL_A}"


def test_gfm_clamped_positions_sort_with_real_ones() -> None:
    result = citations_to_gfm("A. B.", [cite(99, "1"), cite(-1, "2"), cite(2, "3")], SOURCES)
    assert result == f"[^1]A.[^2] B.[^3]\n\n[^1]: {URL_B}\n[^2]: {URL_C}\n[^3]: {URL_A}"


@pytest.mark.parametrize("junk", [None, "5", 5.0, 2.5, [5], {"p": 5}, True, False])
def test_gfm_non_int_and_bool_positions_are_ignored(junk: object) -> None:
    assert citations_to_gfm("Text.", [cite(junk, "1")], SOURCES) == "Text."


def test_gfm_bool_position_does_not_count_as_zero_or_one() -> None:
    result = citations_to_gfm(
        "Text.",
        [cite(position=True, source_id="1"), cite(position=False, source_id="2"), cite(5, "3")],
        SOURCES,
    )
    assert result == f"Text.[^1]\n\n[^1]: {URL_C}"


def test_index_for_clamps_and_rejects() -> None:
    assert _index_for("abc", 0) == 0
    assert _index_for("abc", 2) == 2
    assert _index_for("abc", 3) == 3
    assert _index_for("abc", 4) == 3
    assert _index_for("abc", -1) == 0
    assert _index_for("", 5) == 0
    assert _index_for("abc", position=True) is None
    assert _index_for("abc", 1.0) is None
    assert _index_for("abc", "1") is None
    assert _index_for("abc", None) is None


# --------------------------------------------------------------------------------------
# citations_to_gfm, placement normalisation
# --------------------------------------------------------------------------------------


def test_gfm_mid_word_position_advances_to_word_end() -> None:
    result = citations_to_gfm("Hello world.", [cite(2, "1")], SOURCES)
    assert result == f"Hello[^1] world.\n\n[^1]: {URL_A}"


def test_gfm_one_char_into_word_advances_to_word_end() -> None:
    result = citations_to_gfm("Hello world.", [cite(7, "1")], SOURCES)
    assert result == f"Hello world[^1].\n\n[^1]: {URL_A}"


def test_gfm_one_char_before_word_end_advances_by_one() -> None:
    result = citations_to_gfm("Hello world", [cite(10, "1")], SOURCES)
    assert result == f"Hello world[^1]\n\n[^1]: {URL_A}"


def test_gfm_word_start_boundary_is_not_mid_word() -> None:
    # Index 6 sits between a space and "w": whitespace-before rule moves it back to 5.
    result = citations_to_gfm("Hello world.", [cite(6, "1")], SOURCES)
    assert result == f"Hello[^1] world.\n\n[^1]: {URL_A}"


def test_gfm_word_end_before_punctuation_is_not_moved() -> None:
    result = citations_to_gfm("Hello, world.", [cite(5, "1")], SOURCES)
    assert result == f"Hello[^1], world.\n\n[^1]: {URL_A}"


def test_gfm_between_letter_and_punctuation_after_it_is_kept() -> None:
    result = citations_to_gfm("a.b", [cite(1, "1")], SOURCES)
    assert result == f"a[^1].b\n\n[^1]: {URL_A}"


def test_gfm_digits_count_as_word_characters() -> None:
    result = citations_to_gfm("In 1889 it opened.", [cite(5, "1")], SOURCES)
    assert result == f"In 1889[^1] it opened.\n\n[^1]: {URL_A}"


def test_gfm_letter_digit_boundary_is_inside_a_word() -> None:
    result = citations_to_gfm("abc123 x", [cite(3, "1")], SOURCES)
    assert result == f"abc123[^1] x\n\n[^1]: {URL_A}"


def test_gfm_underscore_and_hyphen_are_not_word_characters() -> None:
    assert citations_to_gfm("a_b", [cite(2, "1")], SOURCES) == f"a_[^1]b\n\n[^1]: {URL_A}"
    assert citations_to_gfm("a-b", [cite(2, "1")], SOURCES) == f"a-[^1]b\n\n[^1]: {URL_A}"


def test_gfm_whitespace_before_position_moves_back() -> None:
    result = citations_to_gfm("Done.  Next one.", [cite(7, "1")], SOURCES)
    assert result == f"Done.[^1]  Next one.\n\n[^1]: {URL_A}"


def test_gfm_newlines_and_tabs_count_as_whitespace() -> None:
    result = citations_to_gfm("Para one.\n\n\tPara two.", [cite(12, "1")], SOURCES)
    assert result == f"Para one.[^1]\n\n\tPara two.\n\n[^1]: {URL_A}"


def test_gfm_trailing_whitespace_at_end_is_skipped() -> None:
    result = citations_to_gfm("Done.\n", [cite(6, "1")], SOURCES)
    assert result == f"Done.[^1]\n\n\n[^1]: {URL_A}"


def test_gfm_whitespace_at_start_moves_marker_to_zero() -> None:
    result = citations_to_gfm("   Hi", [cite(2, "1")], SOURCES)
    assert result == f"[^1]   Hi\n\n[^1]: {URL_A}"


def test_gfm_position_inside_whitespace_run_moves_back_over_it_all() -> None:
    result = citations_to_gfm("a   b", [cite(3, "1")], SOURCES)
    assert result == f"a[^1]   b\n\n[^1]: {URL_A}"


def test_gfm_snapped_positions_merge_into_one_group() -> None:
    # Positions 2 and 4 are in the same word, so both land after "Hello".
    result = citations_to_gfm("Hello world", [cite(2, "1"), cite(4, "2")], SOURCES)
    assert result == f"Hello[^1][^2] world\n\n[^1]: {URL_A}\n[^2]: {URL_B}"


def test_gfm_snapping_reorders_numbering_by_final_position() -> None:
    # "1" asks for index 1 (snaps to 5), "2" asks for index 5. Both end up at 5 in input order,
    # while "3" at 11 stays last.
    result = citations_to_gfm("Hello world", [cite(1, "1"), cite(11, "3"), cite(5, "2")], SOURCES)
    assert result == (f"Hello[^1][^2] world[^3]\n\n[^1]: {URL_A}\n[^2]: {URL_B}\n[^3]: {URL_C}")


@pytest.mark.parametrize(
    ("text", "index", "expected"),
    [
        ("ab", 1, 2),
        ("a b", 1, 1),
        ("a b", 2, 1),
        ("ab cd", 4, 5),
        ("ab cd", 3, 2),
        ("ab", 0, 0),
        ("ab", 2, 2),
        ("", 0, 0),
        ("  ", 2, 0),
    ],
)
def test_snap_index_table(text: str, index: int, expected: int) -> None:
    assert _snap_index(text, index) == expected


# --------------------------------------------------------------------------------------
# citations_to_gfm, Unicode
# --------------------------------------------------------------------------------------


def test_gfm_non_ascii_text_uses_code_point_positions() -> None:
    text = "Café crème. Très bon."
    result = citations_to_gfm(text, [cite(11, "1")], SOURCES)
    assert result == f"Café crème.[^1] Très bon.\n\n[^1]: {URL_A}"


def test_gfm_accented_letters_are_word_characters() -> None:
    result = citations_to_gfm("Très bon", [cite(2, "1")], SOURCES)
    assert result == f"Très[^1] bon\n\n[^1]: {URL_A}"


def test_gfm_cjk_text() -> None:
    result = citations_to_gfm("你好世界。再见。", [cite(5, "1"), cite(8, "2")], SOURCES)
    assert result == f"你好世界。[^1]再见。[^2]\n\n[^1]: {URL_A}\n[^2]: {URL_B}"


def test_gfm_astral_characters_count_as_one_position() -> None:
    text = "Hi \U0001f600 there. Next."
    # "Hi " = 3, emoji = 1, " there." = 7 -> 11 is right after "there.".
    result = citations_to_gfm(text, [cite(11, "1")], SOURCES)
    assert result == f"Hi \U0001f600 there.[^1] Next.\n\n[^1]: {URL_A}"


def test_gfm_marker_after_astral_character_is_not_split() -> None:
    text = "\U0001f600\U0001f600 end"
    result = citations_to_gfm(text, [cite(1, "1"), cite(2, "2")], SOURCES)
    assert result == f"\U0001f600[^1]\U0001f600[^2] end\n\n[^1]: {URL_A}\n[^2]: {URL_B}"


def test_gfm_position_past_astral_text_is_clamped_by_code_points() -> None:
    text = "\U0001f600\U0001f600"
    assert citations_to_gfm(text, [cite(3, "1")], SOURCES) == f"{text}[^1]\n\n[^1]: {URL_A}"


def test_gfm_unicode_whitespace_moves_back() -> None:
    result = citations_to_gfm(f"end.{NBSP}{EM_SPACE}Next", [cite(6, "1")], SOURCES)
    assert result == f"end.[^1]{NBSP}{EM_SPACE}Next\n\n[^1]: {URL_A}"


def test_gfm_does_not_touch_markdown_in_answer() -> None:
    answer = "**Bold** and `code`.\n\n- item"
    result = citations_to_gfm(answer, [cite(20, "1")], SOURCES)
    assert result == f"**Bold** and `code`.[^1]\n\n- item\n\n[^1]: {URL_A}"


# --------------------------------------------------------------------------------------
# Hypothesis properties
# --------------------------------------------------------------------------------------

# The answer alphabet avoids "[" so that stripping inserted markers cannot eat real text.
ANSWER = st.text(
    alphabet=st.characters(blacklist_characters="[", blacklist_categories=["Cs"]),
    max_size=60,
)
PROP_SOURCES = st.lists(
    st.builds(
        lambda i, u: {"id": str(i), "url": f"https://example.org/{u}"},
        st.integers(1, 6),
        st.integers(0, 4),
    ),
    max_size=6,
)
POSITIONS = st.one_of(
    st.integers(-10, 80),
    st.floats(allow_nan=False),
    st.none(),
    st.booleans(),
    st.text(max_size=3),
)
SOURCE_IDS = st.one_of(
    st.integers(-1, 8),
    st.integers(-1, 8).map(str),
    st.text(max_size=3),
    st.none(),
)
CITATIONS = st.lists(
    st.builds(lambda p, s: {"position": p, "source_id": s}, POSITIONS, SOURCE_IDS),
    max_size=12,
)
MARKER = re.compile(r"\[\^(\d+)\]")
DEFINITION = re.compile(r"^\[\^(\d+)\]: (\S+)$")


def split_output(answer: str, output: str) -> tuple[str, dict[int, str]]:
    """Split a rendered answer into (marked body, {number: url}); body only if no footnotes."""
    if output == answer:
        return output, {}
    body, _, block = output.rpartition("\n\n")
    definitions: dict[int, str] = {}
    for line in block.split("\n"):
        match = DEFINITION.match(line)
        assert match is not None, line
        assert int(match[1]) not in definitions, "duplicate definition"
        definitions[int(match[1])] = match[2]
    return body, definitions


@given(ANSWER, CITATIONS, PROP_SOURCES)
def test_property_removing_markers_and_definitions_restores_answer(
    answer: str, citations: list[dict[str, Any]], sources: list[dict[str, str]]
) -> None:
    output = citations_to_gfm(answer, citations, sources)
    body, _ = split_output(answer, output)
    assert MARKER.sub("", body) == answer


@given(ANSWER, CITATIONS, PROP_SOURCES)
def test_property_numbers_are_contiguous_and_defined_exactly_once(
    answer: str, citations: list[dict[str, Any]], sources: list[dict[str, str]]
) -> None:
    output = citations_to_gfm(answer, citations, sources)
    body, definitions = split_output(answer, output)
    used = [int(m[1]) for m in MARKER.finditer(body)]
    assert set(used) == set(definitions)
    assert sorted(definitions) == list(range(1, len(definitions) + 1))
    # First appearance order is 1, 2, 3, ... and definitions are listed in number order.
    first_seen = list(dict.fromkeys(used))
    assert first_seen == sorted(first_seen)
    if definitions:
        assert list(definitions) == sorted(definitions)


@given(ANSWER, CITATIONS, PROP_SOURCES)
def test_property_definitions_are_distinct_source_urls(
    answer: str, citations: list[dict[str, Any]], sources: list[dict[str, str]]
) -> None:
    _, definitions = split_output(answer, citations_to_gfm(answer, citations, sources))
    known = {s["url"] for s in sources}
    assert set(definitions.values()) <= known
    assert len(set(definitions.values())) == len(definitions)


@given(ANSWER, CITATIONS, PROP_SOURCES)
def test_property_markers_never_split_words_or_follow_whitespace(
    answer: str, citations: list[dict[str, Any]], sources: list[dict[str, str]]
) -> None:
    body, _ = split_output(answer, citations_to_gfm(answer, citations, sources))
    for match in MARKER.finditer(body):
        before = MARKER.sub("", body[: match.start()])
        after = MARKER.sub("", body[match.end() :])
        assert not (before and after and before[-1].isalnum() and after[0].isalnum())
        assert not (before and before[-1].isspace())


@given(ANSWER, st.lists(st.sampled_from(["1", "2", "3"]), max_size=5))
def test_property_without_valid_positions_answer_is_unchanged(answer: str, ids: list[str]) -> None:
    citations = [{"position": None, "source_id": i} for i in ids]
    assert citations_to_gfm(answer, citations, SOURCES) == answer


@given(ANSWER, CITATIONS, PROP_SOURCES)
def test_property_does_not_mutate_inputs(
    answer: str, citations: list[dict[str, Any]], sources: list[dict[str, str]]
) -> None:
    citations_before = [dict(c) for c in citations]
    sources_before = [dict(s) for s in sources]
    citations_to_gfm(answer, citations, sources)
    assert citations == citations_before
    assert sources == sources_before


@given(ANSWER, st.integers(-5, 80))
def test_property_snap_index_stays_in_bounds_and_is_idempotent(answer: str, position: int) -> None:
    index = _index_for(answer, position)
    assert index is not None
    assert 0 <= index <= len(answer)
    snapped = _snap_index(answer, index)
    assert 0 <= snapped <= len(answer)
    assert _snap_index(answer, snapped) == snapped


JSON_SCALARS = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text(max_size=8)
)
JSON_VALUES = st.recursive(
    JSON_SCALARS,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=5), inner, max_size=4)
    ),
    max_leaves=10,
)


@given(
    st.dictionaries(
        st.sampled_from(["title", "url", "snippet", "extra_snippets", "full_content", "other"]),
        JSON_VALUES,
    )
)
def test_property_simplify_never_fails_and_has_the_documented_shape(
    result: dict[str, Any],
) -> None:
    simple = simplify_result(result)
    assert set(simple) in ({"title", "url", "snippet"}, {"title", "url", "snippet", "full_content"})
    assert all(isinstance(v, str) for v in simple.values())
    assert simple["snippet"] == "" or simple["snippet"].strip()
    if "full_content" in simple:
        assert simple["full_content"] != ""


@given(
    st.text(max_size=10),
    st.lists(
        st.one_of(st.text(max_size=10), st.builds(lambda c: {"chunk": c}, st.text(max_size=10))),
        max_size=5,
    ),
)
def test_property_simplify_snippet_is_the_separator_join_of_nonblank_parts(
    snippet: str, chunks: list[str | dict[str, str]]
) -> None:
    texts = [c if isinstance(c, str) else c["chunk"] for c in chunks]
    expected = SNIPPET_SEPARATOR.join(t for t in [snippet, *texts] if t.strip())
    assert simplify_result({"snippet": snippet, "extra_snippets": chunks})["snippet"] == expected


URL_POOL = st.sampled_from(["a", "b", "c", "d", "e"])
MARKET_LISTS = st.lists(
    st.lists(
        st.one_of(
            st.builds(lambda u: {"url": u}, URL_POOL),
            st.builds(lambda u: {"url": u}, st.integers()),
            st.just({"title": "no url"}),
        ),
        max_size=6,
    ),
    max_size=4,
)


@given(MARKET_LISTS)
def test_property_interleave_is_unique_complete_and_keeps_market_order(
    markets: list[list[dict[str, Any]]],
) -> None:
    merged = interleave_results(markets)
    merged_urls = [m["url"] for m in merged]
    assert len(merged_urls) == len(set(merged_urls))
    wanted = {i["url"] for market in markets for i in market if isinstance(i.get("url"), str)}
    assert set(merged_urls) == wanted
    for market in markets:
        survivors = [i for i in market if any(m is i for m in merged)]
        positions = [next(k for k, m in enumerate(merged) if m is i) for i in survivors]
        # Within one market, results that survived dedupe keep their relative order.
        assert positions == sorted(positions)


def test_gfm_citation_without_source_id_does_not_match_a_source_id_named_none() -> None:
    sources = [{"id": "None", "url": URL_A}]
    assert citations_to_gfm("Text.", [cite(5, None)], sources) == "Text."


def test_gfm_source_with_non_scalar_id_is_never_matched_by_its_text_form() -> None:
    sources: list[dict[str, Any]] = [{"id": None, "url": URL_A}, {"id": ["x"], "url": URL_B}]
    citations = [cite(5, "None"), cite(5, "['x']")]
    assert citations_to_gfm("Text.", citations, sources) == "Text."


# --------------------------------------------------------------------------------------
# citations_to_gfm, Markdown blocks (code fences and tables)
# --------------------------------------------------------------------------------------

FENCED = "Intro:\n\n```python\nx = 1\n```\n\nMore text."
TABLE = "Data:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nNext."


def gfm_a(answer: str, position: int) -> str:
    return citations_to_gfm(answer, [cite(position, "1")], SOURCES)


def test_gfm_marker_at_the_end_of_a_closing_fence_moves_before_the_block() -> None:
    # "```[^1]" is no longer a fence: the rest of the answer, definitions included, would be
    # swallowed into the code block and no footnote would render.
    result = gfm_a(FENCED, FENCED.index("```\n\nMore") + 3)

    assert result == f"Intro:[^1]\n\n```python\nx = 1\n```\n\nMore text.\n\n[^1]: {URL_A}"
    assert "```[^1]" not in result


@pytest.mark.parametrize(
    "anchor", ["python", "x = 1", "x =", "```\n\nMore", "\n\nMore", "```python"]
)
def test_gfm_marker_inside_or_next_to_a_fence_moves_before_the_block(anchor: str) -> None:
    result = gfm_a(FENCED, FENCED.index(anchor) + 1)

    assert result == f"Intro:[^1]\n\n```python\nx = 1\n```\n\nMore text.\n\n[^1]: {URL_A}"


def test_gfm_marker_after_the_block_is_placed_normally() -> None:
    result = gfm_a(FENCED, len(FENCED))

    assert result == f"{FENCED}[^1]\n\n[^1]: {URL_A}"


def test_gfm_marker_at_the_end_of_a_table_row_moves_before_the_table() -> None:
    result = gfm_a(TABLE, TABLE.index("| 1 | 2 |") + len("| 1 | 2 |"))

    assert result == f"Data:[^1]\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nNext.\n\n[^1]: {URL_A}"
    assert "|[^1]" not in result


@pytest.mark.parametrize("anchor", ["| a |", "|---|", "1 | 2", "| b |"])
def test_gfm_marker_inside_a_table_moves_before_the_table(anchor: str) -> None:
    result = gfm_a(TABLE, TABLE.index(anchor) + 2)

    assert result == f"Data:[^1]\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nNext.\n\n[^1]: {URL_A}"


def test_gfm_marker_in_the_text_after_a_table_is_unaffected() -> None:
    result = gfm_a(TABLE, TABLE.index("Next.") + len("Next."))

    assert result == f"{TABLE}[^1]\n\n[^1]: {URL_A}"


def test_gfm_a_single_pipe_line_is_not_a_table() -> None:
    answer = "| note\nplain text"

    assert gfm_a(answer, len("| note")) == f"| note[^1]\nplain text\n\n[^1]: {URL_A}"


def test_gfm_marker_in_a_block_at_the_very_start_is_dropped() -> None:
    answer = "```\ncode\n```\nafter"

    assert gfm_a(answer, 6) == answer
    assert gfm_a("| a |\n|---|\n| 1 |", 3) == "| a |\n|---|\n| 1 |"


def test_gfm_marker_after_whitespace_only_prefix_is_dropped() -> None:
    answer = "\n\n```\ncode\n```"

    assert gfm_a(answer, 8) == answer


def test_gfm_a_fence_directly_followed_by_a_table_moves_before_both() -> None:
    answer = "Lead\n\n```\nc\n```\n| a |\n|---|\n| 1 |\n"
    result = gfm_a(answer, answer.index("| 1 |") + 3)

    assert result == f"Lead[^1]\n\n```\nc\n```\n| a |\n|---|\n| 1 |\n\n\n[^1]: {URL_A}"


def test_gfm_an_unclosed_fence_runs_to_the_end_of_the_text() -> None:
    answer = "Lead\n```\ncode with no end"

    assert gfm_a(answer, len(answer)) == f"Lead[^1]\n```\ncode with no end\n\n[^1]: {URL_A}"


@pytest.mark.parametrize(
    ("answer", "inside"),
    [
        ("Lead\n~~~\ncode\n~~~\nTail", "code"),
        ("Lead\n````\n```\ninner\n```\n````\nTail", "inner"),  # a shorter fence does not close
        ("Lead\n  ```js\ncode\n  ```\nTail", "code"),
        ("Lead\r\n```\r\ncode\r\n```\r\nTail", "code"),
    ],
)
def test_gfm_fence_variants_are_recognised(answer: str, inside: str) -> None:
    result = gfm_a(answer, answer.index(inside) + 1)

    assert result == f"Lead[^1]{answer[4:]}\n\n[^1]: {URL_A}"


def test_gfm_a_longer_fence_is_not_closed_by_a_fence_with_an_info_string() -> None:
    answer = "Lead\n```\ncode\n```js\nstill code\n```\nTail"

    result = gfm_a(answer, answer.index("still") + 1)

    assert result == f"Lead[^1]{answer[4:]}\n\n[^1]: {URL_A}"


def test_gfm_backticks_inside_a_line_do_not_open_a_fence() -> None:
    answer = "Use ``` fences for code. Then more text."

    assert gfm_a(answer, 10) == f"Use ``` fences[^1] for code. Then more text.\n\n[^1]: {URL_A}"


def test_gfm_several_citations_in_one_block_share_one_marker_group() -> None:
    result = citations_to_gfm(
        FENCED,
        [cite(FENCED.index("x = 1"), "1"), cite(FENCED.index("```\n\n") + 3, "2")],
        SOURCES,
    )

    assert result == (
        f"Intro:[^1][^2]\n\n```python\nx = 1\n```\n\nMore text.\n\n[^1]: {URL_A}\n[^2]: {URL_B}"
    )


# --------------------------------------------------------------------------------------
# citations_to_gfm, hostile or odd upstream citation data
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://one.example\n\n# Injected",
        "https://one.example/a b",
        "https://one.example/\ttab",
        "https://one.example/\x00nul",
        "https://one.example/\u2028sep",
        "https://one.example/\u0085next",
        "javascript:alert(1)",
        "ftp://one.example/file",
        "file:///etc/passwd",
        "//one.example/path",
        "one.example/path",
        "https://",
        "https:///path",
        "http://[::1",
        "https://one.example:notaport/",
        "https://one.example/" + "a" * 2048,
    ],
)
def test_gfm_unsafe_source_urls_are_never_printed(url: str) -> None:
    sources = [{"id": "1", "url": url}]

    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == "Text."


@pytest.mark.parametrize(
    "url",
    [
        "https://one.example/path?q=1&r=2#frag",
        "HTTPS://One.Example/Path",
        "http://one.example:8080/",
        "https://[2001:db8::1]/doc",
        "https://one.example/" + "a" * (2048 - len("https://one.example/")),
        "https://one.example/café",
    ],
)
def test_gfm_safe_source_urls_are_kept(url: str) -> None:
    sources = [{"id": "1", "url": url}]

    assert citations_to_gfm("Text.", [cite(5, "1")], sources) == f"Text.[^1]\n\n[^1]: {url}"


def test_gfm_injected_markdown_cannot_follow_the_definitions() -> None:
    sources = [
        {"id": "1", "url": "https://one.example\n\n# Injected"},
        {"id": "2", "url": URL_B},
    ]

    result = citations_to_gfm("Text.", [cite(5, "1"), cite(5, "2")], sources)

    assert result == f"Text.[^1]\n\n[^1]: {URL_B}"
    assert "Injected" not in result


@pytest.mark.parametrize(
    "big",
    ["9" * 5000, 10**5000, -(10**5000), 2**64, "1" * 19],
    ids=["str-5000-digits", "int-5000-digits", "negative-int", "2**64", "19-digits"],
)
def test_gfm_huge_ids_in_citations_are_dropped_not_fatal(big: object) -> None:
    assert citations_to_gfm("Text.", [cite(5, big)], SOURCES) == "Text."
    assert citations_to_gfm("Text.", [cite(5, big)], NUMBERED_SOURCES) == "Text."


def test_gfm_huge_ids_in_sources_are_skipped_not_fatal() -> None:
    sources: list[dict[str, Any]] = [
        {"id": 10**5000, "url": URL_A},
        {"id": "9" * 5000, "url": URL_B},
        {"id": "ok", "url": URL_C},
    ]

    result = citations_to_gfm("Text.", [cite(5, "ok"), cite(5, "9" * 5000)], sources)

    # The int id (too big to print) is skipped; the 5000-digit string id is an ordinary key.
    assert result == f"Text.[^1][^2]\n\n[^1]: {URL_C}\n[^2]: {URL_B}"
    assert URL_A not in result


def test_gfm_ids_up_to_64_bits_still_work() -> None:
    sources = [{"id": 2**64 - 1, "url": URL_A}]

    assert citations_to_gfm("Text.", [cite(5, 2**64 - 1)], sources) == (
        f"Text.[^1]\n\n[^1]: {URL_A}"
    )


@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "id": st.one_of(st.integers(0, 4), st.text(max_size=3)),
                "url": st.one_of(
                    st.text(max_size=40),
                    st.builds(
                        lambda host, tail: f"https://{host}.example/{tail}",
                        st.text(alphabet="abc", min_size=1, max_size=4),
                        st.text(max_size=12),
                    ),
                ),
            }
        ),
        max_size=6,
    ),
    st.lists(st.integers(0, 4), max_size=6),
)
def test_property_every_definition_line_is_a_single_footnote_with_one_url(
    sources: list[dict[str, Any]], ids: list[int]
) -> None:
    answer = "alpha beta gamma delta epsilon"
    result = citations_to_gfm(answer, [cite(6 + i, i) for i in ids], sources)

    if result != answer:
        _, definitions = result.split("\n\n", 1)
        for line in definitions.split("\n"):
            assert re.fullmatch(r"\[\^\d+\]: \S+", line), line
        assert definitions.count("\n") + 1 == len(set(re.findall(r"\[\^(\d+)\]:", definitions)))


# --------------------------------------------------------------------------------------
# truncate_text and the content limit
# --------------------------------------------------------------------------------------


def marker(kept: int, total: int) -> str:
    return f"\n\n[... truncated: {kept} of {total} characters shown ...]"


@pytest.mark.parametrize("limit", [0, -5])
def test_truncate_without_a_limit_is_the_identity(limit: int) -> None:
    assert truncate_text("x" * 5000, limit) == "x" * 5000


@pytest.mark.parametrize("length", [0, 1, 99, 100])
def test_truncate_text_that_fits_is_unchanged(length: int) -> None:
    assert truncate_text("x" * length, 100) == "x" * length


def test_truncate_prefers_a_paragraph_boundary() -> None:
    text = "A" * 30 + "\n\n" + "B" * 30 + "\n\n" + "C" * 30

    assert truncate_text(text, 70) == "A" * 30 + "\n\n" + "B" * 30 + marker(62, len(text))


def test_truncate_falls_back_to_a_line_break() -> None:
    text = "A" * 30 + "\n" + "B" * 30 + "\n" + "C" * 30

    assert truncate_text(text, 70) == "A" * 30 + "\n" + "B" * 30 + marker(61, len(text))


def test_truncate_falls_back_to_a_space() -> None:
    text = " ".join(["word"] * 40)

    result = truncate_text(text, 52)

    assert result == " ".join(["word"] * 10) + marker(49, len(text))


def test_truncate_hard_cuts_when_there_is_no_boundary_in_the_second_half() -> None:
    text = "x" * 100

    assert truncate_text(text, 40) == "x" * 40 + marker(40, 100)


def test_truncate_ignores_a_boundary_in_the_first_half() -> None:
    text = "tiny\n\n" + "x" * 100

    assert truncate_text(text, 40) == text[:40] + marker(40, len(text))


@given(st.text(max_size=300), st.integers(1, 120))
def test_property_truncate_keeps_a_prefix_and_is_bounded(text: str, limit: int) -> None:
    result = truncate_text(text, limit)

    if len(text) <= limit:
        assert result == text
    else:
        kept = result.split("\n\n[... truncated:")[0]
        assert text.startswith(kept)
        assert len(kept) <= limit
        assert len(kept) >= limit // 2 - 1 or kept == text[:limit].rstrip()
        assert result.endswith(f"of {len(text)} characters shown ...]")


def test_simplify_truncates_only_the_full_content() -> None:
    result = {
        "title": "T",
        "url": "https://x.org",
        "snippet": "S" * 500,
        "extra_snippets": [{"chunk": "E" * 500}],
        "full_content": {"text": "P" * 1000},
    }

    simple = simplify_result(result, max_content_chars=300)

    assert simple["snippet"] == "S" * 500 + SNIPPET_SEPARATOR + "E" * 500
    assert simple["full_content"] == "P" * 300 + marker(300, 1000)


def test_simplify_without_a_limit_keeps_the_whole_page() -> None:
    result = {"full_content": {"text": "P" * 100_000}}

    assert simplify_result(result)["full_content"] == "P" * 100_000
