"""Shared pytest configuration: anyio backend, live-test gating and fixture loading."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from local_staan import LocalStaan
from staan_fakes import FakeStaan

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LIVE_API_KEY_ENV = "STAAN_API_KEY"
MUTMUT_ENV = "MUTANT_UNDER_TEST"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--run-live`` option."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked 'live' against the real Staan API (needs STAAN_API_KEY)",
    )


def live_skip_reason(config: pytest.Config, environ: dict[str, str] | None = None) -> str | None:
    """Explain why live tests must be skipped, or return ``None`` if they may run."""
    env = os.environ if environ is None else environ
    if not config.getoption("--run-live"):
        return "live test: pass --run-live to run it"
    if not env.get(LIVE_API_KEY_ENV, "").strip():
        return f"live test: {LIVE_API_KEY_ENV} is not set"
    return None


def mutmut_skip_reason(environ: Mapping[str, str] | None = None) -> str | None:
    """Explain why a test that needs the pristine package must be skipped, or ``None``.

    mutmut runs the suite from a ``mutants/`` copy of the tree. Its ``staan_mcp/__init__.py``
    imports mutmut's trampoline, which a bare ``python -I -S`` (no site-packages) cannot import,
    so the tests that start one fail there and abort mutmut's clean run. mutmut sets
    ``MUTANT_UNDER_TEST`` in every process it runs the suite in: to the mutant's name, or to an
    empty string for the clean and the stats runs.
    """
    env = os.environ if environ is None else environ
    if MUTMUT_ENV in env:
        return "mutmut's copy of the package imports mutmut, which a bare interpreter cannot"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``live`` tests unless ``--run-live`` is given and ``STAAN_API_KEY`` is set."""
    reason = live_skip_reason(config)
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend() -> str:
    """Run anyio-marked tests on asyncio only."""
    return "asyncio"


def load_fixture_file(name: str) -> Any:
    """Load and parse ``tests/fixtures/<name>`` (JSON)."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Return a loader for JSON files in ``tests/fixtures``."""
    return load_fixture_file


@pytest.fixture
def fixture_bytes() -> Callable[[str], bytes]:
    """Return a loader for the raw bytes of a file in ``tests/fixtures``."""
    return lambda name: (FIXTURES_DIR / name).read_bytes()


@pytest.fixture
def fake() -> FakeStaan:
    """A fresh fake Staan API (see ``staan_fakes``); integration tests override its replies."""
    return FakeStaan()


@pytest.fixture
def local_staan() -> Iterator[LocalStaan]:
    """A loopback fake Staan API for the standalone stdio server (see ``local_staan``)."""
    with LocalStaan() as upstream:
        yield upstream
