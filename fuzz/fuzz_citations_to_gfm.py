"""Fuzz target for citation rendering: :func:`staan_mcp.formatting.citations_to_gfm`.

Builds an answer string and JSON-ish citation and source lists from the fuzzed bytes and feeds
them to :func:`~staan_mcp.formatting.citations_to_gfm`. The function promises to render Markdown
footnote citations and definitions for any input: it must never raise (junk citations and
sources are ignored by design), always return a ``str``, and never mutate the sequences or
mappings it is given. The structures are built by a small local consumer instead of
``atheris.FuzzedDataProvider`` because ``test_one_input`` must run where atheris is not
installed; the byte-slicing is deterministic for all of that.
"""

from __future__ import annotations

import copy
import sys
from typing import Final

from staan_mcp.formatting import citations_to_gfm

_MAX_NESTING: Final = 2
_CITATION_KEYS: Final = ("position", "source_id", "extra")
_SOURCE_KEYS: Final = ("id", "url", "note")


class _Reader:
    """Deterministic consumer of fuzz bytes, taken from the front of the input.

    A self-contained stand-in for ``atheris.FuzzedDataProvider``: the unit tests exercise
    ``test_one_input`` on platforms without atheris wheels, so the slicing cannot depend on it.
    """

    def __init__(self, data: bytes) -> None:
        """Store ``data``; every reader method consumes from its front."""
        self._data = data

    def _pop(self, size: int) -> bytes:
        """Remove and return up to ``size`` bytes from the front of the input."""
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def small(self, bound: int) -> int:
        """Return a value in ``[0, bound]`` derived from the next byte."""
        chunk = self._pop(1)
        return (chunk[0] if chunk else 0) % (bound + 1)

    def text(self) -> str:
        """Return a short string decoded with ``surrogateescape``."""
        return self._pop(self.small(32)).decode("utf-8", errors="surrogateescape")

    def scalar(self) -> str | int | bool | None:
        """Return one JSON-ish scalar: text, an integer, a boolean or ``None``."""
        kind = ("text", "int", "bool", "none")[self.small(3)]
        if kind == "text":
            return self.text()
        if kind == "int":
            return int.from_bytes(self._pop(4), "little", signed=True)
        if kind == "bool":
            return bool(self.small(1))
        return None

    def value(self, depth: int) -> object:
        """Return a JSON-ish value: a scalar, or a list or dict of values while ``depth`` allows."""
        kind = ("scalar", "list", "dict")[self.small(2)] if depth else "scalar"
        if kind == "scalar":
            return self.scalar()
        if kind == "list":
            return [self.value(depth - 1) for _ in range(self.small(4))]
        return {self.text(): self.value(depth - 1) for _ in range(self.small(4))}

    def entry(self, keys: tuple[str, ...]) -> dict[str, object]:
        """Return a small mapping where each of ``keys`` appears or not, with JSON-ish values."""
        return {key: self.value(_MAX_NESTING) for key in keys if self.small(1)}


def test_one_input(data: bytes) -> None:
    """Fuzz one input as an answer string plus JSON-ish citation and source lists.

    Args:
        data: Raw fuzzer input.

    Raises:
        AssertionError: If an invariant of :mod:`staan_mcp.formatting` is violated.
    """
    reader = _Reader(data)
    answer = reader.text()
    citations = [reader.entry(_CITATION_KEYS) for _ in range(reader.small(6))]
    sources = [reader.entry(_SOURCE_KEYS) for _ in range(reader.small(6))]
    before = (copy.deepcopy(citations), copy.deepcopy(sources))
    output = citations_to_gfm(answer, citations, sources)
    assert isinstance(output, str)
    assert (citations, sources) == before


def main() -> None:
    """Run the target under libFuzzer until it stops."""
    import atheris  # noqa: PLC0415  (imported here so the module loads without atheris)

    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
