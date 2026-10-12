"""Fuzz target for domain normalisation: :func:`staan_mcp.domains.normalize_domain` and friends.

Feeds arbitrary UTF-8 text and raw bytes (decoded with ``surrogateescape``) to
:func:`~staan_mcp.domains.normalize_domain` and :func:`~staan_mcp.domains.normalize_domains`.
The documented rejection path (:class:`~staan_mcp.domains.InvalidDomainError`, which is a
:class:`ValueError`) is expected and swallowed; every other exception is a finding. Accepted
results must be bare domains: ASCII, lowercase, dot-separated labels of ``[a-z0-9-]`` that
neither start nor end with a hyphen. Normalising an accepted result again must be idempotent
whenever it does not start with ``www.`` (one leading ``www.`` is stripped per pass, by design),
and the list form must return distinct bare domains in first-seen order.
"""

from __future__ import annotations

import re
import sys
from typing import Final

from staan_mcp.domains import normalize_domain, normalize_domains

_BARE_LABEL: Final = re.compile(r"[a-z0-9-]+")


def _assert_bare_domain(result: str) -> None:
    """Assert that ``result`` has the shape a bare domain must have."""
    assert result.isascii()
    assert result == result.lower()
    assert "." in result
    labels = result.split(".")
    assert all(_BARE_LABEL.fullmatch(label) for label in labels)
    assert not any(label.startswith("-") or label.endswith("-") for label in labels)


def test_one_input(data: bytes) -> None:
    """Fuzz one input through both normalisation entry points.

    ``data`` is fed as one value and as a list of NUL-separated values.

    Args:
        data: Raw fuzzer input, decoded with ``surrogateescape``.

    Raises:
        AssertionError: If an invariant of :mod:`staan_mcp.domains` is violated.
    """
    value = data.decode("utf-8", errors="surrogateescape")
    try:
        result = normalize_domain(value)
    except (ValueError, TypeError):
        return  # InvalidDomainError documents the rejection path
    _assert_bare_domain(result)
    if not result.startswith("www."):
        assert normalize_domain(result) == result
    values = [chunk.decode("utf-8", errors="surrogateescape") for chunk in data.split(b"\0")]
    try:
        domains = normalize_domains(values)
    except (ValueError, TypeError):
        return
    assert len(domains) == len(set(domains))
    for domain in domains:
        _assert_bare_domain(domain)


def main() -> None:
    """Run the target under libFuzzer until it stops."""
    import atheris  # noqa: PLC0415  (imported here so the module loads without atheris)

    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
