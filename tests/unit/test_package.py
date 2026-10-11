"""Tests for the package root and the ``python -m staan_mcp`` entry point."""

from __future__ import annotations

import importlib
import importlib.metadata
import runpy
import sys
import types
from typing import TYPE_CHECKING

import pytest

import staan_mcp

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def reloadable_package() -> Iterator[None]:
    """Restore the real ``staan_mcp.__version__`` after a test reloads the package."""
    yield
    importlib.reload(staan_mcp)


def test_version_matches_installed_metadata() -> None:
    assert staan_mcp.__version__ == importlib.metadata.version("staan-mcp")
    assert staan_mcp.__all__ == ["__version__"]


@pytest.mark.usefixtures("reloadable_package")
def test_version_comes_from_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "9.8.7")
    importlib.reload(staan_mcp)
    assert staan_mcp.__version__ == "9.8.7"


@pytest.mark.usefixtures("reloadable_package")
def test_version_falls_back_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    importlib.reload(staan_mcp)
    assert staan_mcp.__version__ == "0.0.0+unknown"


def test_python_dash_m_exits_with_the_cli_return_code(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_cli = types.ModuleType("staan_mcp.cli")

    def main() -> int:
        calls.append("main")
        return 7

    fake_cli.main = main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "staan_mcp.cli", fake_cli)
    with pytest.raises(SystemExit) as info:
        runpy.run_module("staan_mcp", run_name="__main__")
    assert info.value.code == 7
    assert calls == ["main"]
