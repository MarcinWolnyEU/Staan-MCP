"""Fuzz target for market expansion: :func:`staan_mcp.markets.expand_markets`.

Feeds lists of arbitrary strings (raw bytes decoded with ``surrogateescape``, split on NUL) to
:func:`~staan_mcp.markets.expand_markets`, covering exact codes, wildcards and mixed specs. The
documented rejection path (:class:`~staan_mcp.markets.InvalidMarketError`) is expected and
swallowed; every other exception is a finding. Accepted results must be a non-empty,
duplicate-free subset of :data:`~staan_mcp.markets.MARKETS` in first-seen order, stable across
repeated calls and idempotent: expanding an expansion must return it unchanged.
"""

from __future__ import annotations

import sys

from staan_mcp.markets import MARKETS, expand_markets


def test_one_input(data: bytes) -> None:
    """Fuzz one input as a market specification of NUL-separated items.

    Args:
        data: Raw fuzzer input, decoded with ``surrogateescape``.

    Raises:
        AssertionError: If an invariant of :mod:`staan_mcp.markets` is violated.
    """
    items = [chunk.decode("utf-8", errors="surrogateescape") for chunk in data.split(b"\0")]
    try:
        result = expand_markets(items)
    except (ValueError, TypeError):
        return  # InvalidMarketError documents the rejection path
    assert result
    assert len(result) == len(set(result))
    assert set(result) <= set(MARKETS)
    assert expand_markets(items) == result
    assert expand_markets(result) == result


def main() -> None:
    """Run the target under libFuzzer until it stops."""
    import atheris  # noqa: PLC0415  (imported here so the module loads without atheris)

    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
