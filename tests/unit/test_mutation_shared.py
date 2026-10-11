"""Assertions that pin what the stdlib-only shared modules compute at import time or raise.

``contract`` builds ``TOOL_LIST`` when it is imported, so a mutant of ``_field`` or
``_annotations`` is never seen through the constants (mutmut forks after the import). These
tests call the helpers directly. ``raise_for_failures`` has the same need for the ``endpoint``
the error it raises carries.
"""

from __future__ import annotations

import pytest

from staan_mcp.contract import _annotations, _field
from staan_mcp.errors import StaanAPIError
from staan_mcp.payloads import raise_for_failures, search_payload


def test_field_adds_a_description_and_a_title_made_from_the_name() -> None:
    assert _field("max_content_chars", "How much.", type="integer") == {
        "type": "integer",
        "description": "How much.",
        "title": "Max Content Chars",
    }


def test_annotations_of_a_read_only_open_world_tool() -> None:
    assert _annotations(idempotent=True) == {
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    }
    assert _annotations(idempotent=False)["idempotentHint"] is False


def test_a_search_that_failed_in_every_market_is_a_search_error_listing_each_market() -> None:
    outcomes = [
        StaanAPIError("rate limited", status_code=429, endpoint="search"),
        StaanAPIError("unavailable", status_code=503, endpoint="search"),
    ]

    with pytest.raises(StaanAPIError) as caught:
        raise_for_failures(["us-en", "fr-fr"], outcomes)

    assert str(caught.value) == "us-en: rate limited; fr-fr: unavailable"
    assert caught.value.endpoint == "search"
    assert caught.value.status_code is None


def test_outcomes_that_do_not_pair_up_with_the_markets_are_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="shorter"):
        raise_for_failures(["us-en", "fr-fr"], [{"web": {"results": []}}])

    with pytest.raises(ValueError, match="longer"):
        search_payload(["us-en"], [{}, {}], simple_response=False, max_content_chars=0)
