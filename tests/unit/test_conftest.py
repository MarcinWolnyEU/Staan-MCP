"""Tests for the shared pytest plumbing in ``tests/conftest.py``."""
# pylint: disable=redefined-outer-name

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import mutmut_skip_reason

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

CONFTEST = Path(__file__).parent.parent / "conftest.py"
LIVE_SUITE = """
import pytest

@pytest.mark.live
def test_live():
    pass

def test_plain():
    pass
"""


@pytest.fixture
def live_suite(pytester: pytest.Pytester) -> pytest.Pytester:
    """A scratch project with this repo's conftest, one live test and one plain test."""
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(LIVE_SUITE)
    return pytester


def test_live_tests_are_skipped_by_default(
    live_suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "present-but-flag-missing")
    result = live_suite.runpytest("-rs", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*live test: pass --run-live to run it*"])


def test_live_tests_are_skipped_without_an_api_key(
    live_suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "   ")
    result = live_suite.runpytest("-rs", "--run-live", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*live test: STAAN_API_KEY is not set*"])


def test_live_tests_run_with_flag_and_key(
    live_suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STAAN_API_KEY", "present")
    result = live_suite.runpytest("--run-live", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)


def test_fixture_loaders(
    load_fixture: Callable[[str], Any], fixture_bytes: Callable[[str], bytes]
) -> None:
    parsed = load_fixture("search_short.json")
    assert isinstance(parsed["web"]["results"], list)
    assert fixture_bytes("search_short.json").startswith(b"{")


def test_anyio_backend_is_asyncio(anyio_backend: str) -> None:
    assert anyio_backend == "asyncio"


def test_tests_that_need_the_pristine_package_run_except_under_mutmut() -> None:
    assert mutmut_skip_reason({"PATH": "/bin"}) is None
    # mutmut sets the variable to the mutant's name, or to "" for the clean and the stats runs.
    for value in ("", "stats", "staan_mcp.client.x_StaanClient__post__mutmut_1"):
        reason = mutmut_skip_reason({"MUTANT_UNDER_TEST": value})
        assert reason is not None
        assert "mutmut" in reason


def test_the_pristine_package_check_reads_the_environment_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    assert mutmut_skip_reason() is None

    monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
    assert mutmut_skip_reason() is not None
