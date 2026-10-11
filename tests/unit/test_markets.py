"""Tests for market constants and wildcard expansion."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from staan_mcp.markets import DEFAULT_MARKET, MARKETS, InvalidMarketError, expand_markets

ENGLISH = [m for m in MARKETS if m.startswith("en-")]

# Characters that make an item a wildcard pattern, mixed with the market alphabet.
patterns = st.text(alphabet="abcdefghijklmnopqrstuvwxyz-*?[]!", min_size=1, max_size=8)
valid_items = st.one_of(st.sampled_from(MARKETS), st.sampled_from(["en-*", "*-fr", "*", "d?-de"]))


def test_markets_are_the_twelve_documented_codes() -> None:
    expected = "fr-fr de-de en-us en-gb en-fr en-ca en-au en-in en-ie en-nz en-za en-sg"
    assert " ".join(MARKETS) == expected
    assert len(set(MARKETS)) == 12


def test_default_market_is_en_gb_and_supported() -> None:
    assert DEFAULT_MARKET == "en-gb"
    assert DEFAULT_MARKET in MARKETS


def test_invalid_market_error_is_a_value_error() -> None:
    assert issubclass(InvalidMarketError, ValueError)


@pytest.mark.parametrize("market", MARKETS)
def test_exact_market_is_returned_as_is(market: str) -> None:
    assert expand_markets(market) == [market]


@pytest.mark.parametrize("raw", ["EN-GB", "  en-gb  ", "En-Gb", "\ten-gb\n"])
def test_exact_market_is_stripped_and_lowercased(raw: str) -> None:
    assert expand_markets(raw) == ["en-gb"]


@pytest.mark.parametrize("pattern", ["en-*", "en*", "EN-*", " en-* "])
def test_english_wildcards_expand_to_the_ten_english_markets(pattern: str) -> None:
    result = expand_markets(pattern)

    assert result == ENGLISH
    assert len(result) == 10


def test_suffix_wildcard_matches_both_french_markets_in_canonical_order() -> None:
    assert expand_markets("*-fr") == ["fr-fr", "en-fr"]


def test_star_expands_to_all_markets_in_canonical_order() -> None:
    assert expand_markets("*") == list(MARKETS)


def test_question_mark_matches_exactly_one_character() -> None:
    assert expand_markets("en-g?") == ["en-gb"]
    assert expand_markets("??-de") == ["de-de"]
    assert expand_markets("?n-us") == ["en-us"]


def test_character_set_wildcard() -> None:
    assert expand_markets("en-[gu][bs]") == ["en-us", "en-gb"]


def test_negated_character_set_wildcard() -> None:
    assert expand_markets("de-[!e]e") == ["de-de"]


def test_sequence_input_is_expanded_item_by_item() -> None:
    assert expand_markets(["de-de", "en-g*"]) == ["de-de", "en-gb"]


def test_tuple_input_is_accepted() -> None:
    assert expand_markets(("fr-fr", "EN-US")) == ["fr-fr", "en-us"]


def test_duplicates_are_removed_keeping_first_seen_order() -> None:
    assert expand_markets(["en-us", "fr-fr", "EN-US", "*-fr"]) == ["en-us", "fr-fr", "en-fr"]


def test_pattern_after_exact_market_does_not_reorder_first_seen() -> None:
    assert expand_markets(["en-sg", "en-*"]) == ["en-sg", *[m for m in ENGLISH if m != "en-sg"]]


def test_single_string_is_one_item_not_split_on_commas() -> None:
    with pytest.raises(InvalidMarketError, match="Unknown market 'en-us,fr-fr'"):
        expand_markets("en-us,fr-fr")


@pytest.mark.parametrize("spec", [[], (), [""], "", "   ", ["en-us", ""], ["en-us", "  "]])
def test_empty_spec_or_empty_item_is_rejected(spec: str | list[str] | tuple[str, ...]) -> None:
    with pytest.raises(InvalidMarketError, match="Valid markets"):
        expand_markets(spec)


GUIDANCE = (
    f"Valid markets: {', '.join(MARKETS)}. "
    "Wildcards are allowed: '*' (any characters), '?' (one character) and '[abc]' "
    "(a set), for example 'en-*' (all English markets) or '*-fr'."
)


def test_empty_list_message_asks_for_a_market() -> None:
    with pytest.raises(InvalidMarketError) as excinfo:
        expand_markets([])

    assert str(excinfo.value) == f"At least one market is required. {GUIDANCE}"


def test_empty_item_message_says_it_must_not_be_empty() -> None:
    with pytest.raises(InvalidMarketError) as excinfo:
        expand_markets(["en-us", ""])

    assert str(excinfo.value) == f"A market must not be empty. {GUIDANCE}"


@pytest.mark.parametrize("code", ["es-es", "en", "en_gb", "xx-yy", "en-gb-x", "fr-be"])
def test_unknown_exact_code_is_rejected_and_named(code: str) -> None:
    with pytest.raises(InvalidMarketError) as excinfo:
        expand_markets(code)

    assert f"Unknown market {code!r}" in str(excinfo.value)


@pytest.mark.parametrize("pattern", ["zz-*", "*-xx", "en-?", "en-[xyz]", "de-[!d]e", "["])
def test_pattern_matching_nothing_is_rejected_and_named(pattern: str) -> None:
    with pytest.raises(InvalidMarketError) as excinfo:
        expand_markets(pattern)

    assert f"The market pattern {pattern!r} matches no market" in str(excinfo.value)


def test_a_failing_item_rejects_the_whole_spec() -> None:
    with pytest.raises(InvalidMarketError, match="Unknown market 'nope'"):
        expand_markets(["en-us", "nope"])


def test_error_message_lists_every_market_and_the_wildcard_syntax() -> None:
    with pytest.raises(InvalidMarketError) as excinfo:
        expand_markets("nope")

    message = str(excinfo.value)
    assert ", ".join(MARKETS) in message
    assert "'*'" in message
    assert "'?'" in message
    assert "[abc]" in message
    assert "en-*" in message


@given(st.lists(valid_items, min_size=1, max_size=6))
def test_expansion_is_a_duplicate_free_subset_of_markets(items: list[str]) -> None:
    result = expand_markets(items)

    assert result
    assert len(result) == len(set(result))
    assert set(result) <= set(MARKETS)


@given(st.lists(valid_items, min_size=1, max_size=6))
def test_expansion_keeps_first_seen_order(items: list[str]) -> None:
    expected: list[str] = []
    for item in items:
        for market in expand_markets(item):
            if market not in expected:
                expected.append(market)

    assert expand_markets(items) == expected


@given(st.lists(valid_items, min_size=1, max_size=6))
def test_expansion_ignores_letter_case(items: list[str]) -> None:
    assert expand_markets([i.upper() for i in items]) == expand_markets(items)


@given(st.lists(valid_items, min_size=1, max_size=6))
def test_expansion_is_idempotent(items: list[str]) -> None:
    once = expand_markets(items)

    assert expand_markets(once) == once


@given(st.sampled_from(MARKETS))
def test_every_market_expands_to_itself_and_is_matched_by_star(market: str) -> None:
    assert expand_markets(market) == [market]
    assert market in expand_markets("*")


@given(patterns)
def test_arbitrary_pattern_either_raises_invalid_market_error_or_returns_a_subset(
    pattern: str,
) -> None:
    try:
        result = expand_markets(pattern)
    except InvalidMarketError:
        return

    assert result
    assert set(result) <= set(MARKETS)
    assert result == [m for m in MARKETS if m in result]
